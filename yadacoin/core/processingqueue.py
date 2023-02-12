from yadacoin.core.blockchain import Blockchain
from yadacoin.core.transaction import Transaction
from yadacoin.core.job import Job
from yadacoin.core.miner import Miner
from yadacoin.core.block import Block


class BlockProcessingQueueItem:
    def __init__(self, blockchain: Blockchain, stream=None, body=None):
        self.blockchain = blockchain
        self.body = body or {}
        self.stream = stream


class BlockProcessingQueue:
    def __init__(self):
        self.queue = {}
        self.last_popped = ()

    async def add(self, item: BlockProcessingQueueItem):
        first_block = item.blockchain.first_block
        final_block = item.blockchain.final_block
        if isinstance(first_block, Block) and isinstance(final_block, Block):
            if (first_block.hash, final_block.hash) == self.last_popped:
                return
            self.queue.setdefault((first_block.hash, final_block.hash), item)
        else:
            if (first_block['hash'], final_block['hash']) == self.last_popped:
                return
            self.queue.setdefault((first_block['hash'], final_block['hash']), item)

    async def pop(self):
        if not self.queue:
            return None
        key, item = self.queue.popitem()
        self.last_popped = key
        return item


class TransactionProcessingQueueItem:
    def __init__(self, transaction: Transaction, stream=None):
        self.transaction = transaction
        self.stream = stream


class TransactionProcessingQueue:
    def __init__(self):
        self.queue = {}
        self.last_popped = ''

    async def add(self, item: TransactionProcessingQueueItem):
        if item.transaction.transaction_signature == self.last_popped:
            return
        self.queue.setdefault(item.transaction.transaction_signature, item)

    async def pop(self):
        if not self.queue:
            return None
        key, item = self.queue.popitem()
        self.last_popped = key
        return item


class NonceProcessingQueueItem:
    def __init__(self, miner: Miner='', stream=None, body=None):
        self.miner = miner
        self.stream = stream
        self.body = body
        self.id = body['params']['id']
        self.nonce = body['params']['nonce']


class NonceProcessingQueue:
    def __init__(self):
        self.queue = {}
        self.last_popped = ''

    async def add(self, item: NonceProcessingQueueItem):
        if (item.id, item.nonce) == self.last_popped:
            return
        self.queue.setdefault((item.id, item.nonce), item)

    async def pop(self):
        if not self.queue:
            return None
        key, item = self.queue.popitem()
        self.last_popped = key
        return item
