from traceback import format_exc
import uuid
import random
from time import time
import binascii
from bitcoin.wallet import P2PKHBitcoinAddress
from logging import getLogger
from threading import Thread

from yadacoin.core.chain import CHAIN
from yadacoin.core.config import get_config
from yadacoin.core.block import Block
from yadacoin.core.blockchain import Blockchain
from yadacoin.core.transaction import (
    Transaction,
    MissingInputTransactionException, 
    InvalidTransactionException,
    InvalidTransactionSignatureException,
    TransactionInputOutputMismatchException,
    TotalValueMismatchException
)


class Job:
    @classmethod
    async def from_dict(cls, job):
        inst = cls()
        inst.id = job['job_id']
        inst.diff = job['difficulty']
        inst.target = job['target']
        inst.blob = job['blob']
        inst.seed_hash = job['seed_hash']
        inst.index = job['height']
        inst.extra_nonce = job['extra_nonce']
        inst.algo = job['algo']
        return inst

    def to_dict(self):
        return {
            'job_id': self.id,
            'difficulty': self.diff,
            'target': self.target,
            'blob': self.blob,
            'seed_hash': self.seed_hash,
            'height': self.index,
            'extra_nonce': self.extra_nonce,
            'algo': self.algo,
        }


class MiningPool(object):
    @classmethod
    async def init_async(cls):
        self = cls()
        self.config = get_config()
        self.mongo = self.config.mongo
        self.app_log = getLogger("tornado.application")
        self.target_block_time = CHAIN.target_block_time(self.config.network)
        self.max_target = CHAIN.MAX_TARGET
        self.inbound = {}
        self.connected_ips = {}
        self.last_block_time = 0
        self.index = 0
        last_block = await self.config.LatestBlock.block.copy()
        self.refreshing = False
        if last_block:
            self.last_block_time = int(last_block.time)
            self.index = last_block.index
        self.last_refresh = 0
        self.block_factory = None
        await self.refresh()
        return self

    def get_status(self):
        """Returns pool status as explicit dict"""
        status = {"miners": len(self.inbound), "ips": len(self.connected_ips)}
        return status

    def little_hash(self, block_hash):
        little_hex = bytearray.fromhex(block_hash)
        little_hex.reverse()

        str_little = ''.join(format(x, '02x') for x in little_hex)

        return str_little

    async def on_miner_nonce(self, nonce: str, job: Job, address: str='', miner_hash: str='') -> bool:
        nonce = nonce + job.extra_nonce.encode().hex()
        hash1 = self.block_factory.generate_hash_from_header(
            job.index,
            binascii.unhexlify(job.blob).decode().replace('{00}', '{nonce}').replace(job.extra_nonce, ''),
            nonce
        )
        if self.block_factory.index >= CHAIN.BLOCK_V5_FORK:
            hash1_test = self.little_hash(hash1)
        else:
            hash1_test = hash1

        if (
            int(hash1_test, 16) > self.block_factory.target and
            self.config.network != 'regnet' and
            (self.block_factory.special_min and
            int(hash1, 16) > self.block_factory.special_target)
        ):
            return False
        block_candidate = await self.block_factory.copy()
        block_candidate.hash = hash1
        block_candidate.nonce = nonce

        if block_candidate.special_min:
            delta_t = int(block_candidate.time) - int(self.last_block_time)
            special_target = CHAIN.special_target(
                block_candidate.index,
                block_candidate.target,
                delta_t, 
                self.config.network
            )
            block_candidate.special_target = special_target

        if (
            block_candidate.index >= 35200 and
            (int(block_candidate.time) - int(self.last_block_time)) < 600 and 
            block_candidate.special_min and
            self.config.network == 'mainnet'
        ):
            self.app_log.warning("Special min block too soon: hash {} header {} nonce {}".format(
                block_candidate.hash,
                block_candidate.header,
                block_candidate.nonce
            ))
            return False

        accepted = False

        if self.config.network == 'mainnet':
            target = 0x0000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
        elif self.config.network == 'regnet':
            target = 0x00FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
        if (
          (int(block_candidate.target) + target) > int(hash1, 16) or
          (
            block_candidate.index >= CHAIN.BLOCK_V5_FORK and
            (int(block_candidate.target) + target) > int(block_candidate.little_hash(), 16)
          )
        ):
            # submit share only now, not to slow down if we had a block
            await self.mongo.async_db.shares.update_one(
                {
                    'hash': block_candidate.hash
                },
                {
                    '$set': {
                        'address': address,
                        'index': block_candidate.index,
                        'hash': block_candidate.hash,
                        'nonce': nonce,
                        'time': int(time())
                    }
                },
                upsert=True
            )

            accepted = True

        if (
          int(block_candidate.target) > int(block_candidate.hash, 16) or
          (
            block_candidate.index >= CHAIN.BLOCK_V5_FORK and
            int(block_candidate.target) > int(block_candidate.little_hash(), 16)
          ) or
          (
            self.config.network == 'regnet' and
            target > int(block_candidate.little_hash(), 16)
          )
        ):
            block_candidate.signature = self.config.BU.generate_signature(block_candidate.hash, self.config.private_key)

            try:
                block_candidate.verify()
            except Exception as e:
                if accepted:
                    return {
                        'hash': hash1,
                        'nonce': nonce,
                        'height': job.index,
                        'id': block_candidate.signature
                    }
                self.app_log.warning("Verify error {} - hash {} header {} nonce {}".format(
                    e,
                    block_candidate.hash,
                    block_candidate.header,
                    block_candidate.nonce
                ))
                return False
            # accept winning block
            await self.accept_block(block_candidate)
            # Conversion to dict is important, or the object may change
            self.app_log.debug('block ok')

            return {
                'hash': block_candidate.hash,
                'nonce': nonce,
                'height': block_candidate.index,
                'id': block_candidate.signature
            }
        elif (
          block_candidate.special_min and (int(block_candidate.special_target) > int(block_candidate.hash, 16)) or
          (
            block_candidate.index >= CHAIN.BLOCK_V5_FORK and
            block_candidate.special_min and (int(block_candidate.special_target) > int(block_candidate.little_hash(), 16))
          )
        ):
            block_candidate.signature = self.config.BU.generate_signature(block_candidate.hash, self.config.private_key)

            try:
                block_candidate.verify()
            except Exception as e:
                if accepted:
                    return {
                        'hash': hash1,
                        'nonce': nonce,
                        'height': job.index,
                        'id': block_candidate.signature
                    }
                self.app_log.warning("Verify error {} - hash {} header {} nonce {}".format(
                    e,
                    block_candidate.hash,
                    block_candidate.header,
                    block_candidate.nonce
                ))
                return False
            # accept winning block
            await self.accept_block(block_candidate)
            # Conversion to dict is important, or the object may change
            self.app_log.debug('block ok - special_min')

            return {
                'hash': block_candidate.hash,
                'nonce': nonce,
                'height': block_candidate.index,
                'id': block_candidate.signature
            }

        if accepted:
            return {
                'hash': block_candidate.hash,
                'nonce': nonce,
                'height': block_candidate.index,
                'id': block_candidate.signature
            }

    async def refresh(self):
        """Refresh computes a new bloc to mine. The block is stored in self.block_factory and contains
        the transactions at the time of the refresh. Since tx hash is in the header, a refresh here means we have to
        trigger the events for the pools, even if the block index did not change."""
        # TODO: to be taken care of, no refresh atm between blocks
        try:
            if self.refreshing:
                return
            self.refreshing = True
            await self.config.LatestBlock.block_checker()
            if self.block_factory:
                self.last_block_time = int(self.block_factory.time)
            self.block_factory = await self.create_block(
                await self.get_pending_transactions(),
                self.config.public_key,
                self.config.private_key,
                index=self.config.LatestBlock.block.index + 1
            )
            await self.set_target(int(time()))
            self.block_factory.header = self.block_factory.generate_header()
            self.refreshing = False
        except Exception as e:
            self.refreshing = False
            from traceback import format_exc
            self.app_log.error("Exception {} mp.refresh".format(format_exc()))
            raise

    async def create_block(self, transactions, public_key, private_key, index):
        return await Block.generate(
            transactions,
            public_key,
            private_key,
            index=index
        )

    async def block_to_mine_info(self):
        """Returns info for current block to mine"""
        if self.block_factory is None:
            #await self.refresh()
            return {}
        res = {
            'target': hex(int(self.block_factory.target))[2:].rjust(64, '0'),  # target is now in hex format
            'special_target': hex(int(self.block_factory.special_target))[2:].rjust(64, '0'),  # target is now in hex format
            # TODO this is the network target, maybe also send some pool target?
            'special_min': self.block_factory.special_min,
            'header': self.block_factory.header,
            'version': self.block_factory.version,
            'height': self.block_factory.index,  # This is the height of the one we are mining
            'previous_time': self.config.LatestBlock.block.time,  # needed for miner to recompute the real diff
        }
        return res

    async def block_template(self, agent):
        """Returns info for current block to mine"""
        if self.block_factory is None:
            await self.refresh()
        if not self.block_factory.target:
            await self.set_target_from_last_non_special_min(self.config.LatestBlock.block)

        job = await self.generate_job(agent)
        return job

    async def generate_job(self, agent):
        difficulty = int(self.max_target / self.block_factory.target)
        seed_hash = '4181a493b397a733b083639334bc32b407915b9a82b7917ac361816f0a1f5d4d' #sha256(yadacoin65000)
        job_id = str(uuid.uuid4())
        extra_nonce = hex(random.randrange(1000000,1000000000000000))[2:]
        header = self.block_factory.header.replace('{nonce}', '{00}' + extra_nonce)

        if self.config.network == 'regnet':
            target = '00FFFFFFFFFFFFFF'
        elif 'XMRigCC/3' in agent or 'XMRig/3' in agent:
            target = '0000FFFFFFFFFFFF'
        else:
            target = '0000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF'

        res = {
            'job_id': job_id,
            'difficulty': difficulty,
            'target': target,  # can only be 16 characters long
            'blob': header.encode().hex(),
            'seed_hash': seed_hash,
            'height': self.config.LatestBlock.block.index + 1,  # This is the height of the one we are mining
            'extra_nonce': extra_nonce,
            'algo': 'rx/yada'
        }
        return await Job.from_dict(res)

    async def set_target(self, to_time):
        if not self.block_factory.special_min:
            await self.set_target_from_last_non_special_min(self.config.LatestBlock.block)
        # todo: keep block target at normal target, for header and block info.
        # Only tweak target at validation time, and don't include special_min into header
        if self.block_factory.index >= CHAIN.SPECIAL_MIN_FORK:  # TODO: use a CHAIN constant
            # print("test target", int(to_time), self.last_block_time)
            if self.block_factory.target == 0:
                # If the node is started when the current block is special_min, then we have a 0 target
                await self.set_target_as_previous_non_special_min()
                # print('target set to', self.block_factory.target)
            delta_t = int(to_time) - self.last_block_time
            if delta_t \
                    > CHAIN.special_min_trigger(self.config.network, self.block_factory.index):
                special_target = CHAIN.special_target(self.block_factory.index, self.block_factory.target, delta_t, self.config.network)
                self.block_factory.special_min = True
                self.block_factory.special_target = special_target
                self.block_factory.time = int(to_time)
            else:
                self.block_factory.special_min = False
        elif self.block_factory.index < CHAIN.SPECIAL_MIN_FORK:  # TODO: use a CHAIN constant
            if (int(to_time) - self.last_block_time) > self.target_block_time:
                self.block_factory.target = self.max_target
                self.block_factory.special_min = True
                self.block_factory.time = int(to_time)
            else:
                self.block_factory.special_min = False

    async def set_target_as_previous_non_special_min(self):
        """TODO: this is not correct, should use a cached version of the current target somewhere, and recalc on
        new block event if we cross a boundary (% 2016 currently). Beware, at boundary we need to recalc the new diff one block ahead
        that is, if we insert block before a boundary, we have to calc the diff for the next one right away."""
        self.app_log.error("set_target_as_previous_non_special_min should not be called anymore")
        res = await self.mongo.async_db.blocks.find_one(
            {
                'special_min': False,
            },
            {
                'target': 1
            }, 
            sort=[('index',-1)]
        )

        if res:
            self.block_factory.target = int(res['target'], 16)

    async def set_target_from_last_non_special_min(self, latest_block):
        if self.index >= CHAIN.FORK_10_MIN_BLOCK:
            self.block_factory.target = await CHAIN.get_target_10min(
                latest_block,
                self.block_factory
            )
        else:
            self.block_factory.target = await CHAIN.get_target(
                self.index,
                latest_block,
                self.block_factory
            )

    async def get_inputs(self, inputs):
        for x in inputs:
            yield x

    async def get_pending_transactions(self):
        transaction_objs = []
        used_sigs = []
        async for txn in self.mongo.async_db.miner_transactions.find().sort([('fee', -1)]):
            try:
                if isinstance(txn, Transaction):
                    transaction_obj = txn
                elif isinstance(txn, dict):
                    transaction_obj = Transaction.from_dict(txn)
                else:
                    self.config.app_log.warning('transaction unrecognizable, skipping')
                    continue
                
                await transaction_obj.verify()
                
                if transaction_obj.transaction_signature in used_sigs:
                    self.config.app_log.warning('duplicate transaction found and removed')
                    continue
                used_sigs.append(transaction_obj.transaction_signature)

                failed1 = False
                failed2 = False
                used_ids_in_this_txn = []

                async for x in self.get_inputs(transaction_obj.inputs):
                    is_input_spent = await self.config.BU.is_input_spent(x.id, transaction_obj.public_key)
                    if is_input_spent:
                        failed1 = True
                    if x.id in used_ids_in_this_txn:
                        failed2 = True
                    used_ids_in_this_txn.append(x.id)
                if failed1:
                    self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                    self.config.app_log.warning('transaction removed: input presumably spent already, not in unspent outputs {}'.format(transaction_obj.transaction_signature))
                    self.mongo.db.failed_transactions.insert({'reason': 'input presumably spent already', 'txn': transaction_obj.to_dict()})
                elif failed2:
                    self.config.app_log.warning('transaction removed: using an input used by another transaction in this block {}'.format(transaction_obj.transaction_signature))
                    self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                    self.mongo.db.failed_transactions.insert({'reason': 'using an input used by another transaction in this block', 'txn': transaction_obj.to_dict()})
                else:
                    transaction_objs.append(transaction_obj)

            except MissingInputTransactionException as e:
                self.config.app_log.warning('MissingInputTransactionException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'MissingInputTransactionException', 'txn': transaction_obj.to_dict()})

            except InvalidTransactionSignatureException as e:
                self.config.app_log.warning('InvalidTransactionSignatureException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'InvalidTransactionSignatureException', 'txn': transaction_obj.to_dict()})

            except InvalidTransactionException as e:
                self.config.app_log.warning('InvalidTransactionException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'InvalidTransactionException', 'txn': transaction_obj.to_dict()})

            except TransactionInputOutputMismatchException as e:
                self.config.app_log.warning('TransactionInputOutputMismatchException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'TransactionInputOutputMismatchException', 'txn': transaction_obj.to_dict()})

            except TotalValueMismatchException as e:
                self.config.app_log.warning('TotalValueMismatchException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'TotalValueMismatchException', 'txn': transaction_obj.to_dict()})

            except Exception as e:
                self.config.app_log.warning(format_exc())
                self.mongo.db.miner_transactions.remove({'id': txn['id']})
                self.mongo.db.failed_transactions.insert({'reason': 'Unhandled exception', 'error': format_exc()})

        return transaction_objs

    async def accept_block(self, block):
        from yadacoin.core.consensus import ProcessingQueueItem
        self.app_log.info('Candidate submitted for index: {}'.format(block.index))
        self.app_log.info('Transactions:')
        for x in block.transactions:
            self.app_log.info(x.transaction_signature)

        await self.config.consensus.insert_consensus_block(block, self.config.peer)

        await self.config.consensus.block_queue.add(ProcessingQueueItem(await Blockchain.init_async(block)))

        await self.config.nodeShared.send_block(block)

        await self.refresh()
        

