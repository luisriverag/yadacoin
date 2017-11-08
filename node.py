import argparse
import hashlib
import json
import requests
import time
from uuid import uuid4
from ecdsa import SigningKey, SECP256k1
from block import Block, BlockFactory
from transaction import Transaction, Input, Output
from blockchainutils import BU
from transactionutils import TU
from transaction import TransactionFactory

def verify_block(block):
    pass

def verify_transaction(transaction):
    signature = transaction.signature

def generate_block(blocks, coinbase, block_reward, transactions):
    block = {
        'index': len(blocks),
        'prevHash': blocks[len(blocks)-1]['hash'] if len(blocks) > 0 else '',
        'reward': {
            'to': coinbase,
            'value': block_reward
        },
        'nonce': str(uuid4()),
        'transactions': transactions
    }
    block['hash'] = hashlib.sha256(json.dumps(block)).digest().encode('hex')
    return block

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('runtype',
                    help='If you want to mine blocks')
    parser.add_argument('--conf',
                    help='set your config file')
    args = parser.parse_args()

    with open(args.conf) as f:
        config = json.loads(f.read())

    public_key = config.get('public_key')
    private_key = config.get('private_key')
    TU.private_key = private_key
    BU.private_key = private_key

    # default run state will be to mine some blocks!

    # proof of work time!
    coinbase = config.get('coinbase')
    block_reward = config.get('block_reward')
    difficulty = config.get('difficulty')

    blocks = BU.get_block_objs()  # verifies as the blocks are created so no need to call block.verify() on each block
    print 'waiting for transactions...'
    while 1:
        with open('miner_transactions.json', 'r+') as f:
            transactions_parsed = json.loads(f.read())
            if transactions_parsed:
                f.seek(0)
                f.write('[]')
                f.truncate()
            transactions = []
            for txn in transactions_parsed:
                transaction = Transaction.from_dict(txn)
                transactions.append(transaction)

        if not transactions and len(blocks):
            pass
        elif not transactions and not len(blocks):
            block = BlockFactory.mine(transactions, coinbase, 50, difficulty, public_key, private_key)
            block.save()
            txn = TransactionFactory(
                public_key=public_key,
                private_key=private_key,
                fee=0.1,
                outputs=[
                    Output(
                        to='1CHVGmXNZgznyYVHzs64WcDVYn3aV8Gj4u',
                        value=10
                    ),
                    Output(
                        to='14opV2ZB6uuzzYPQZhWFewo9oF7RM6pJeQ',
                        value=39.9
                    )
                ],
                inputs=[Input(x.transaction_signature) for x in block.transactions]
            ).generate_transaction()
            block2 = BlockFactory.mine([txn,], coinbase, 1, difficulty, public_key, private_key)
            block2.save()
            print 'waiting for transactions...'
            blocks = BU.get_block_objs()
        else:
            block = BlockFactory.mine(transactions, coinbase, block_reward, difficulty, public_key, private_key)
            block.save()
            print 'waiting for transactions...'
            blocks = BU.get_block_objs()

        time.sleep(1)
