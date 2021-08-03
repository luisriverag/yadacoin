import hashlib
import base64
import random
import sys
from coincurve.keys import PrivateKey
from coincurve._libsecp256k1 import ffi
from bitcoin.wallet import P2PKHBitcoinAddress


class TU(object):  # Transaction Utilities

    @classmethod
    def hash(cls, message):
        return hashlib.sha256(message.encode('utf-8')).digest().hex()

    @classmethod
    def generate_deterministic_signature(cls, config, message:str, private_key=None):
        if not private_key:
            private_key = config.private_key
        key = PrivateKey.from_hex(private_key)
        signature = key.sign(message.encode('utf-8'))
        return base64.b64encode(signature).decode('utf-8')

    @classmethod
    def generate_signature_with_private_key(cls, private_key, message):
        x = ffi.new('long long *')
        x[0] = random.SystemRandom().randint(0, sys.maxsize)
        key = PrivateKey.from_hex(private_key)
        signature = key.sign(message.encode('utf-8'), custom_nonce=(ffi.NULL, x))
        return base64.b64encode(signature).decode('utf-8')

    @classmethod
    def generate_signature(cls, message, private_key):
        x = ffi.new('long long *')
        x[0] = random.SystemRandom().randint(0, sys.maxsize)
        key = PrivateKey.from_hex(private_key)
        signature = key.sign(message.encode('utf-8'), custom_nonce=(ffi.NULL, x))
        return base64.b64encode(signature).decode('utf-8')

    @classmethod
    def generate_rid(cls, config, bulletin_secret):
        bulletin_secrets = sorted([str(config.username_signature), str(bulletin_secret)], key=str.lower)
        return hashlib.sha256((str(bulletin_secrets[0]) + str(bulletin_secrets[1])).encode('utf-8')).digest().hex()
    
    @classmethod
    def check_rid_txn_fully_spent(cls, config, rid_txn, address, index):
        from yadacoin.core.transaction import Transaction
        rid_txn = Transaction.from_dict(rid_txn)
        spending_txn = rid_txn.used_as_input(rid_txn.transaction_signature)
        if spending_txn:
            for output in spending_txn['outputs']:
                if output['to'] == address and output['value'] == 0:
                    return True # now we can create a duplicate relationship
            x = config.BU.get_transaction_by_id(spending_txn['id'], instance=True)
            result = cls.check_rid_txn_fully_spent(config, x.to_dict(), address, index)
            if result:
                return True
            return False
        else:
            return False # hasn't been spent to zero yet

    @classmethod
    async def send(cls, config, to, value, from_address=True, inputs=None, dry_run=False, exact_match=False, outputs=None):
        from yadacoin.core.transaction import NotEnoughMoneyException, Transaction
        if from_address == config.address:
            public_key = config.public_key
            private_key = config.private_key
        else:
            child_key = await config.mongo.async_db.child_keys.find_one({'address': from_address})
            if child_key:
                public_key = child_key['public_key']
                private_key = child_key['private_key']
            else:
                return {'status': 'error', 'message': 'no wallet matching from address'}

        if outputs:
            for output in outputs:
                output['value'] = float(output['value'])
        else:
            outputs=[
                {'to': to, 'value': value}
            ]
        
        if not inputs:
            inputs = []

        try:
            transaction = await Transaction.generate(
                fee=0.00,
                public_key=public_key,
                private_key=private_key,
                inputs=inputs,
                outputs=outputs,
                exact_match=exact_match
            )
        except NotEnoughMoneyException:
            return {'status': "error", 'message': "not enough money"}
        except:
            raise
        try:
            await transaction.verify()
        except:
            return {"error": "invalid transaction"}

        if not dry_run:
            await config.mongo.async_db.miner_transactions.insert_one(transaction.to_dict())
            async for peer_stream in config.peer.get_sync_peers():
                await config.nodeShared.write_params(
                    peer_stream,
                    'newtxn',
                    {
                        'transaction': transaction.to_dict()
                    }
                )
                if peer_stream.peer.protocol_version > 1:
                    config.nodeClient.retry_messages[(peer_stream.peer.rid, 'newtxn', transaction.transaction_signature)] = {'transaction': transaction.to_dict()}
        return transaction.to_dict()
