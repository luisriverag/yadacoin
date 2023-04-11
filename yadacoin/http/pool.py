"""
Handlers required by the pool operations
"""
import json

from tornado import escape
from coincurve import PrivateKey, PublicKey

from yadacoin.core.miningpool import MiningPool
from yadacoin.core.miningpoolpayout import PoolPayer
from yadacoin.core.transactionutils import TU
from yadacoin.core.block import Block
from yadacoin.core.chain import CHAIN
from yadacoin.core.config import Config
from yadacoin.http.base import BaseHandler


class PoolSharesHandler(BaseHandler):
    async def get(self):
        address = self.get_query_argument('address')
        query = {'address': address}
        if '.' not in address:
            query = {
                '$or': [
                    {
                        'address': address
                    },
                    {
                        'address_only': address
                    },
                ]
            }
        total_share = await self.config.mongo.async_db.shares.count_documents(query)
        total_hash = total_share * self.config.pool_diff
        self.render_as_json({'total_hash': int(total_hash)})

class PoolBlocksHandler(BaseHandler):
    async def get(self):
        pool_public_key = self.config.public_key
        blocks = await self.config.mongo.async_db.blocks.find(
            {
                'public_key': pool_public_key
            },
            {
                '_id': 0
            }
        ).sort([('index', -1)]).to_list(100)
        self.render_as_json({'blocks': blocks})

class PoolPayoutsHandler(BaseHandler):
    async def get(self):
        address = self.get_query_argument('address')
        query = {'address': address}
        if '.' in address:
            query = {'address': address.split('.')[0]}
        out = []
        results = self.config.mongo.async_db.share_payout.find({'txn.outputs.to': address}, {'_id': 0}).sort([('index', -1)])
        async for result in results:
            if await self.config.mongo.async_db.blocks.count_documents({'transactions.id': result['txn']['id']}) > 0:
                out.append(result)
        self.render_as_json({'results': out})


class PoolHashRateHandler(BaseHandler):
    async def get(self):
        address = self.get_query_argument('address')
        query = {'address': address}
        if '.' not in address:
            query = {
                '$or': [
                    {
                        'address': address
                    },
                    {
                        'address_only': address
                    },
                ]
            }
        last_share = await self.config.mongo.async_db.shares.find_one(query, {'_id': 0}, sort=[('time', -1)])
        if not last_share:
            return self.render_as_json({'result': 0})
        miner_hashrate_seconds = self.config.miner_hashrate_seconds if hasattr(self.config, 'miner_hashrate_seconds') else 1200

        query = {'time': { '$gt': last_share['time'] - miner_hashrate_seconds}}
        if '.' in address:
            query['address'] = address
        else:
            query['$or'] = [
                {
                    'address': address
                },
                {
                    'address_only': address
                },
            ]
        number_of_shares = await self.config.mongo.async_db.shares.count_documents(query)
        miner_hashrate = (number_of_shares * self.config.pool_diff) / miner_hashrate_seconds
        self.render_as_json({'miner_hashrate': int(miner_hashrate)})

class PoolScanMissedPayoutsHandler(BaseHandler):
    async def get(self):
        start_index = self.get_query_argument('start_index')
        await self.config.pp.do_payout({'index': int(start_index)})
        self.render_as_json({'status': True})


POOL_HANDLERS = [
    (r'/pool-blocks', PoolBlocksHandler),
    (r'/shares-for-address', PoolSharesHandler),
    (r'/payouts-for-address', PoolPayoutsHandler),
    (r'/hashrate-for-address', PoolHashRateHandler),
    (r'/scan-missed-payouts', PoolScanMissedPayoutsHandler),
]
