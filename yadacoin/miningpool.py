import time
import requests
from bitcoin.wallet import P2PKHBitcoinAddress
from logging import getLogger

from yadacoin.chain import CHAIN
from yadacoin.config import get_config
from yadacoin.block import Block, BlockFactory
from yadacoin.blockchain import Blockchain
from yadacoin.transaction import Transaction, MissingInputTransactionException, InvalidTransactionException, \
    InvalidTransactionSignatureException
from yadacoin.fastgraph import FastGraph


class MiningPool(object):
    def __init__(self):
        self.config = get_config()
        self.mongo = self.config.mongo
        self.block_factory = None
        self.app_log = getLogger("tornado.application")
        self.target_block_time = CHAIN.target_block_time(self.config.network)
        self.max_target = CHAIN.MAX_TARGET
        self.inbound = {}
        self.connected_ips = {}

    def get_status(self):
        """Returns pool status as explicit dict"""
        status = {"miners": len(self.inbound), "ips": len(self.connected_ips)}
        return status

    @property
    def free_inbound_slots(self):
        """How many free inbound slots we have"""
        return self.config.max_miners - len(self.inbound)

    def allow_ip(self, IP):
        """Returns True if that ip can connect"""
        return True  # IP not in self.connected_ips  # Allows if we're not connected already.

    def on_new_ip(self, ip):
        """We got an inbound.
        avoid initiating one connection twice if the handshake does not go fast enough."""
        self.app_log.info("miner on_new_ip:{}".format(ip))
        if ip not in self.connected_ips:
            self.connected_ips[ip] = 1
        else:
            self.connected_ips[ip] += 1

    async def on_new_inbound(self, ip:str, version, worker, address, type, sid):
        """Inbound peer provided a correct version and ip, add it to our pool"""
        self.app_log.info("miner on_new_inbound {}:{} {}".format(ip, address, worker))
        self.inbound[sid] = {"ip":ip, "version": version, "worker": worker, "address": address, "type": type}

    async def on_close_inbound(self, sid):
        # We only allow one in or out per ip
        try:
            self.app_log.info("miner on_close_inbound {}".format(sid))
            info = self.inbound.pop(sid, None)
            ip = info['ip']
            self.connected_ips[ip] -= 1
            if self.connected_ips[ip] <= 0:
                self.connected_ips.pop(ip)
        except Exception as e:
            print(e)
            pass

    def refresh(self, block=None):
        """Refresh computes a new bloc to mine. The block is stored in self.block_factory.block and contains
        the transactions at the time of the refresh. Since tx hash is in the header, a refresh here means we have to
        trigger the events for the pools, even if the block index did not change."""
        # TODO: to be taken care of, no refresh atm between blocks

        if block is None:
            block = self.config.BU.get_latest_block()
        if block:
            block = Block.from_dict(block)
            self.height = block.index + 1
        else:
            genesis_block = BlockFactory.get_genesis_block()
            genesis_block.save()
            self.mongo.db.consensus.insert({
                'block': genesis_block.to_dict(),
                'peer': 'me',
                'id': genesis_block.signature,
                'index': 0
                })
            block = Block.from_dict(self.config.BU.get_latest_block())
            self.height = block.index

        try:
            self.block_factory = BlockFactory(
                transactions=self.get_pending_transactions(),
                public_key=self.config.public_key,
                private_key=self.config.private_key,
                index=self.height)

            # TODO: centralize handling of min target
            self.set_target(int(self.block_factory.block.time))
            if not self.block_factory.block.special_min:
                self.set_target_from_last_non_special_min(block)
            self.block_factory.block.header = BlockFactory.generate_header(self.block_factory.block)
        except Exception as e:
            raise e

    def block_to_mine_info(self):
        """Returns info for current block to mine"""
        res = {
            'target': hex(self.block_factory.block.target)[2:].rjust(64, '0'),  # target is now in hex format
            'special_min': self.block_factory.block.special_min,
            'header': self.block_factory.block.header,
            'version': self.block_factory.block.version,
            'height': self.block_factory.block.index,  # This is the height of the one we are mining
        }
        return res

    def set_target(self, to_time):
        latest_block = self.config.BU.get_latest_block()
        if self.block_factory.block.index >= 38600:  # TODO: use a CHAIN constant
            if (int(to_time) - int(latest_block['time'])) > self.target_block_time:
                target_factor = (int(to_time) - int(latest_block['time'])) / self.target_block_time
                #print("mp", self.block_factory.block.target, target_factor)
                #print(self.block_factory.block.to_dict())
                target = self.block_factory.block.target * (target_factor * 4)
                if target > self.max_target:
                    self.block_factory.block.target = self.max_target
                self.block_factory.block.special_min = True
            else:
                self.block_factory.block.special_min = False
        elif self.block_factory.block.index < 38600:  # TODO: use a CHAIN constant
            if (int(to_time) - int(latest_block['time'])) > self.target_block_time:
                self.block_factory.block.target = self.max_target
                self.block_factory.block.special_min = True
            else:
                self.block_factory.block.special_min = False
    
    def set_target_from_last_non_special_min(self, latest_block):
        i = 1
        while 1:
            res = self.mongo.db.blocks.find_one({
                'index': self.height - i,
                'special_min': False,
                'target': {'$ne': CHAIN.MAX_TARGET_HEX}
            })
            if res:
                chain = [x for x in self.mongo.db.blocks.find({
                    'index': {'$gte': res['index']}
                })]
                break
            else:
                i += 1
        self.block_factory.block.target = BlockFactory.get_target(
            self.height,
            latest_block,
            self.block_factory.block,
            Blockchain(
                blocks=chain,
                partial=True
            )
        )
    
    def nonce_generator(self):
        self.app_log.error("nonce_generator is deprecated")
        latest_block_index = self.config.BU.get_latest_block()['index']
        start_nonce = 0
        while 1:
            next_latest_block = self.config.BU.get_latest_block()
            next_latest_block_index = next_latest_block['index']
            if latest_block_index < next_latest_block_index:
                latest_block_index = next_latest_block_index
                start_nonce = 0
                self.refresh()
            else:
                try:
                    start_nonce += 10000000
                except:
                    start_nonce = 0
            self.index = latest_block_index
            to_time = int(time.time())
            self.set_target(to_time)
            if self.block_factory.block.special_min:
                self.block_factory.block.header = BlockFactory.generate_header(self.block_factory.block)
                self.block_factory.block.time = str(int(time.time()))
            self.block_factory.block.header = BlockFactory.generate_header(self.block_factory.block)
            yield [start_nonce, start_nonce + 10000000]

    def combine_transaction_lists(self):
        transactions = self.mongo.db.fastgraph_transactions.find()
        for transaction in transactions:
            if 'txn' in transaction:
                yield transaction['txn']

        transactions = self.mongo.db.miner_transactions.find()
        for transaction in transactions:
            yield transaction

    def get_pending_transactions(self):
        transaction_objs = []
        unspent_indexed = {}
        used_sigs = []
        for txn in self.combine_transaction_lists():
            try:
                if isinstance(txn, FastGraph) and hasattr(txn, 'signatures'):
                    transaction_obj = txn
                elif isinstance(txn, Transaction):
                    transaction_obj = txn
                elif isinstance(txn, dict) and 'signatures' in txn:
                    transaction_obj = FastGraph.from_dict(self.config.BU.get_latest_block()['index'], txn)
                elif isinstance(txn, dict):
                    transaction_obj = Transaction.from_dict(self.config.BU.get_latest_block()['index'], txn)
                else:
                    print('transaction unrecognizable, skipping')
                    continue

                if transaction_obj.transaction_signature in used_sigs:
                    print('duplicate transaction found and removed')
                    continue
                used_sigs.append(transaction_obj.transaction_signature)

                transaction_obj.verify()

                if not isinstance(transaction_obj, FastGraph) and transaction_obj.rid:
                    for input_id in transaction_obj.inputs:
                        input_block = self.config.BU.get_transaction_by_id(input_id.id, give_block=True)
                        if input_block and input_block['index'] > (self.config.BU.get_latest_block()['index'] - 2016):
                            continue

                #check double spend
                address = str(P2PKHBitcoinAddress.from_pubkey(bytes.fromhex(transaction_obj.public_key)))
                if address in unspent_indexed:
                    unspent_ids = unspent_indexed[address]
                else:
                    needed_value = sum([float(x.value) for x in transaction_obj.outputs]) + float(transaction_obj.fee)
                    res = self.config.BU.get_wallet_unspent_transactions(address, needed_value=needed_value)
                    unspent_ids = [x['id'] for x in res]
                    unspent_indexed[address] = unspent_ids

                failed1 = False
                failed2 = False
                used_ids_in_this_txn = []

                for x in transaction_obj.inputs:
                    if x.id not in unspent_ids:
                        failed1 = True
                    if x.id in used_ids_in_this_txn:
                        failed2 = True
                    used_ids_in_this_txn.append(x.id)
                if failed1:
                    self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                    print('transaction removed: input presumably spent already, not in unspent outputs', transaction_obj.transaction_signature)
                    self.mongo.db.failed_transactions.insert({'reason': 'input presumably spent already', 'txn': transaction_obj.to_dict()})
                elif failed2:
                    self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                    print('transaction removed: using an input used by another transaction in this block', transaction_obj.transaction_signature)
                    self.mongo.db.failed_transactions.insert({'reason': 'using an input used by another transaction in this block', 'txn': transaction_obj.to_dict()})
                else:
                    transaction_objs.append(transaction_obj)
            except MissingInputTransactionException as e:
                #print 'missing this input transaction, will try again later'
                pass
            except InvalidTransactionSignatureException as e:
                print('InvalidTransactionSignatureException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'InvalidTransactionSignatureException', 'txn': transaction_obj.to_dict()})
            except InvalidTransactionException as e:
                print('InvalidTransactionException: transaction removed')
                self.mongo.db.miner_transactions.remove({'id': transaction_obj.transaction_signature})
                self.mongo.db.failed_transactions.insert({'reason': 'InvalidTransactionException', 'txn': transaction_obj.to_dict()})
            except Exception as e:
                print(e)
                #print 'rejected transaction', txn['id']
                pass
        return transaction_objs

    def pool_mine(self, pool_peer, address, header, target, nonces, special_min):
        nonce, lhash = BlockFactory.mine(header, target, nonces, special_min)
        if nonce and lhash:
            try:
                requests.post("http://{pool}/pool-submit".format(pool=pool_peer), json={
                    'nonce': nonce,
                    'hash': lhash,
                    'address': address
                }, headers={'Connection':'close'})
            except Exception as e:
                print(e)

    def broadcast_block(self, block):
        # Peers.init(self.config.network)
        # Peer.save_my_peer(self.config.network)
        print('\r\nCandidate submitted for index:', block.index)
        print('\r\nTransactions:')
        for x in block.transactions:
            print(x.transaction_signature)
        self.mongo.db.consensus.insert({'peer': 'me', 'index': block.index, 'id': block.signature, 'block': block.to_dict()})
        print('\r\nSent block to:')
        # TODO: convert to async // send
        # Do we need to send to other nodes than the ones we're connected to via websocket? Event will flow.
        # Then maybe a list of "root" nodes (explorer, known pools) from config, just to make sure.
        for peer in self.config.peers.peers:
            if peer.is_me:
                continue
            try:
                block_dict = block.to_dict()
                block_dict['peer'] = self.config.peers.my_peer
                requests.post(
                    'http://{peer}/newblock'.format(
                        peer=peer.host + ":" + str(peer.port)
                    ),
                    json=block_dict,
                    timeout=3,
                    headers={'Connection':'close'}
                )
                print(peer.host + ":" + str(peer.port))
            except Exception as e:
                print(e)
                peer.report()
