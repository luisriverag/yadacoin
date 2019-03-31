from pymongo import MongoClient, IndexModel, ASCENDING, DESCENDING
from motor.motor_tornado import MotorClient


class Mongo(object):
    def __init__(self, config):
        self.config = config
        self.client = MongoClient(self.config.mongodb_host)
        self.db = self.client[self.config.database]
        self.site_db = self.client[self.config.site_database]

        __id = IndexModel([("id", ASCENDING)], name="__id", unique=True)
        __hash = IndexModel([("hash", ASCENDING)], name="__hash")
        __index = IndexModel([("index", ASCENDING)], name="__index")
        __to = IndexModel([("transactions.outputs.to", ASCENDING)], name="__to")
        __txn_id = IndexModel([("transactions.id", ASCENDING)], name="__txn_id")
        __txn_inputs_id = IndexModel([("transactions.inputs.id", ASCENDING)], name="__txn_inputs_id")
        try:
            self.db.blocks.create_indexes([__hash, __index, __id, __to, __txn_id])
        except:
            pass

        __id = IndexModel([("id", ASCENDING)], name="__id")
        __height = IndexModel([("height", ASCENDING)], name="__height")
        try:
            self.db.unspent_cache.create_indexes([__id, __height])
        except:
            pass

        __id = IndexModel([("id", ASCENDING)], name="__id")
        __index = IndexModel([("index", ASCENDING)], name="__index")
        __block_hash = IndexModel([("block.hash", ASCENDING)], name="__block_hash")
        try:
            self.db.consensus.create_indexes([__id, __index, __block_hash])
        except:
            pass

        __address = IndexModel([("address", ASCENDING)], name="__address")
        __index = IndexModel([("index", ASCENDING)], name="__index")
        __hash = IndexModel([("hash", ASCENDING)], name="__hash")
        try:
            self.db.shares.create_indexes([__address, __index, __hash])
        except:
            pass

        # TODO: add indexes for peers

        # See https://motor.readthedocs.io/en/stable/tutorial-tornado.html
        self.async_client = MotorClient(self.config.mongodb_host)
        self.async_db = self.async_client[self.config.database]
        self.async_site_db = self.async_client[self.config.site_database]
