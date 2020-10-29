import sys
from sys import exc_info
from os import path
import json
import logging
import datetime
from traceback import format_exc
from time import time
from urllib3.exceptions import *
from asyncio import sleep as async_sleep

from asyncstdlib import anext
from bitcoin.wallet import P2PKHBitcoinAddress
from pymongo.errors import DuplicateKeyError
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httputil import HTTPHeaders
from tornado import ioloop

from yadacoin.core.chain import CHAIN
from yadacoin.core.config import get_config
from yadacoin.core.blockchain import Blockchain
from yadacoin.core.block import Block, BlockFactory
from yadacoin.core.transaction import (
    InvalidTransactionException,
    InvalidTransactionSignatureException,
    MissingInputTransactionException,
    NotEnoughMoneyException
)
from yadacoin.core.latestblock import LatestBlock
from yadacoin.socket.node import NodeSocketServer
from yadacoin.core.peer import Peer


class Consensus(object):

    lowest = CHAIN.MAX_TARGET

    def __init__(
        self,
        debug=False,
        prevent_genesis=False,
        target=None,
        special_target=None
    ):
        self.app_log = logging.getLogger("tornado.application")
        self.debug = debug
        self.config = get_config()
        self.mongo = self.config.mongo
        self.prevent_genesis = prevent_genesis
        self.latest_block = None
        self.target = target
        self.special_target = special_target

    async def verify_existing_blockchain(self, reset=False):
        self.app_log.info('verifying existing blockchain')
        existing_blockchain = await Blockchain.init_async(self.config.mongo.async_db.blocks.find({}).sort([('index', 1)]))
        result = await existing_blockchain.verify()
        if result['verified']:
            print('Block height: %s | time: %s' % (self.latest_block.index, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            return True
        else:
            self.app_log.critical(result)
            if reset:
                if 'last_good_block' in result:
                    self.mongo.db.blocks.remove({"index": {"$gt": result['last_good_block'].index}}, multi=True)
                else:
                    self.mongo.db.blocks.remove({"index": {"$gt": 0}}, multi=True)
                self.app_log.debug("{} {}".format(result['message'], '...truncating'))
            else:
                self.app_log.critical("{} - reset False, not truncating - DID NOT VERIFY".format(result['message']))
            self.config.BU.latest_block = None
            latest_block = self.config.LatestBlock.block
            if latest_block:
                self.latest_block = await Block.from_dict(latest_block)
            else:
                if not self.prevent_genesis:
                    await self.config.BU.insert_genesis()

    def remove_pending_transactions_now_in_chain(self, block):
        #remove transactions from miner_transactions collection in the blockchain
        self.mongo.db.miner_transactions.remove({'id': {'$in': [x['id'] for x in block['block']['transactions']]}}, {'_id': 0})

    def remove_fastgraph_transactions_now_in_chain(self, block):
        self.mongo.db.fastgraph_transactions.remove({'id': {'$in': [x['id'] for x in block['block']['transactions']]}}, {'_id': 0})

    async def insert_consensus_block(self, block, peer):
        try:
            block.verify()
        except:
            return
        await self.mongo.async_db.consensus.replace_one({
            'id': block.signature,
            'peer.rid': peer.rid
        },
        {
            'block': block.to_dict(),
            'index': block.index,
            'id': block.signature,
            'peer': peer.to_dict()
        }, upsert=True)

    async def integrate_block_with_existing_chain(self, block: Block):
        """Even in case of retrace, this is the only place where we insert a new block into the block collection and update BU"""

        await self.mongo.async_db.blocks.replace_one({'index': block.index}, block.to_dict(), upsert=True)
        await self.mongo.async_db.miner_transactions.delete_many({'id': {'$in': [x.transaction_signature for x in block.transactions]}})

        self.app_log.info("New block inserted for height: {}".format(block.index))

        await self.config.LatestBlock.block_checker()
        return True

    async def get_target(self, block):
        if block.index >= CHAIN.FORK_10_MIN_BLOCK:
            self.target = await BlockFactory.get_target_10min(block.index, self.config.LatestBlock.block, block)
        else:
            self.target = await BlockFactory.get_target(block.index, self.config.LatestBlock.block, block)

    async def get_special_target(self, block):
        delta_t = int(time()) - int(self.config.LatestBlock.block.time)
        self.special_target = CHAIN.special_target(block.index, block.target, delta_t, get_config().network)

    async def test_block_insertable(
        self,
        latest_local_block,
        latest_remote_block
    ):
        block = latest_remote_block
        if block.index == 0:
            return False

        try:
            block.verify()
        except Exception as e:
            self.app_log.warning("Consensus block did not verify. Rejecting")
            return False

        try:
            await block.check_transactions()
        except:
            return False

        if int(block.index) > CHAIN.CHECK_TIME_FROM and int(block.time) < int(latest_local_block.time):
            self.config.app_log.warning('Block too far in the future. Rejecting')
            return False

        if int(block.index) > CHAIN.CHECK_TIME_FROM and (int(block.time) < (int(latest_local_block.time) + 600)) and block.special_min:
            self.config.app_log.warning('Block should not yet be special min. Rejecting')
            return False


        delta_t = int(block.time) - int(latest_local_block.time)
        if block.index >= 35200 and delta_t < 600 and block.special_min:
            self.config.app_log.warning('Special min block too soon. Rejecting')

        consecutive = False
        if latest_local_block.index == (block.index - 1) and latest_local_block.hash == block.prev_hash:
            consecutive = True

        passed = False
        if int(block.hash, 16) < latest_local_block.target:
            passed = True

        if block.special_min and int(block.hash, 16) < latest_local_block.special_target:
            passed = True

        if block.special_min and block.index < 35200:
            passed = True

        target_block_time = CHAIN.target_block_time(self.config.network)
        if (
            block.index >= 35200 and 
            block.index < 38600 and 
            block.special_min and
            (int(block.time) - int(latest_local_block.time)) > target_block_time
        ):
            passed = True

        return passed and consecutive

    async def test_chain_insertable(
        self,
        fork_block,
        local_chain,
        remote_chain
    ):
        # this function should only accept chains starting at the same height
        first_block_local = await anext(local_chain.blocks)
        first_block_remote = await anext(remote_chain.blocks)

        if await local_chain.count == 0:
            if await self.test_block_insertable(
                fork_block,
                first_block_remote
            ):
                return True
            return False

        if first_block_local.index != first_block_remote.index:
            return False

        if first_block_local.prev_hash != first_block_remote.prev_hash:
            return False

        if not await local_chain.is_consecutive or not await remote_chain.is_consecutive:
            return False

        final_block_local = await local_chain.final_block
        final_block_remote = await remote_chain.final_block
        if (
            final_block_remote.index < final_block_local.index or 
            await remote_chain.get_difficulty() < await local_chain.get_difficulty()
        ):
            return False

        if await remote_chain.count == 2:
            if await self.test_block_insertable(
                first_block_local,
                final_block_remote
            ):
                return True
            return False

        #latest remote block is one ahead of local and deserves to advance the chain
        latest_block = fork_block
        async for block in remote_chain.blocks:
            if not await self.test_block_insertable(
                latest_block,
                block
            ):
                return False
            latest_block = block

        return True

    async def build_chains_and_test(self, block: Block):

        local_blocks = await self.config.mongo.async_db.blocks.find({'index': {'$gte': block.index}}).sort([('index', 1)])
        local_chain = await Blockchain.init_async(local_blocks, partial=True)

        # now we just need to see how far this chain extends
        blocks = [block]
        while True:
            # get the heighest block from this chain
            block = await self.config.mongo.async_db.blocks.find_one({'prevHash': block.hash}, {'_id': 0})
            if block:
                block = await Block.from_dict(block)
                blocks.append(block)
            else:
                block = await self.config.mongo.async_db.consensus.find_one({'block.prevHash': block.hash}, {'_id': 0})
                if block:
                    block = await Block.from_dict(block['block'])
                    blocks.append(block)
            if not block:
                break

        blocks.sort(blocks, key=lambda x: x.index)

        remote_chain = await Blockchain.init_async(blocks, partial=True)

        if not await self.test_chain_insertable(
            local_chain,
            remote_chain
        ):
            return False
        
        await self.config.mongo.async_db.blocks.delete_many({'index': {'$gte': blocks[0].index}})
        await self.integrate_block_with_existing_chain(block)