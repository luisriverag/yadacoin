import json
import hashlib
import humanhash
import socket
import os
import requests
import socketio
import hmac
import re
import base64
import uuid
from multiprocessing import Process, Value, Array, Pool
from flask import Flask, render_template, request, Response, current_app as app, session
from socketIO_client import SocketIO, BaseNamespace
from flask_cors import CORS
from yadacoin import (
    TransactionFactory,
    Transaction,
    MissingInputTransactionException,
    Input,
    Output,
    Block,
    BlockFactory,
    Config,
    Peers,
    Blockchain,
    BlockChainException,
    BU,
    TU,
    Graph,
    Mongo,
    InvalidTransactionException,
    InvalidTransactionSignatureException,
    MiningPool,
    Peer,
    Config,
    NotEnoughMoneyException,
    FastGraph
)
from eccsnacks.curve25519 import scalarmult, scalarmult_base
from pyfcm import FCMNotification
from flask.views import View
from mnemonic import Mnemonic
from bip32utils import BIP32Key
from coincurve.utils import verify_signature

class ChatNamespace(BaseNamespace):
    def on_error(self, event, *args):
        print 'error'

class HomeView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        if not request.args.get('rid'):
            return '{"error": "rid not in query params"}'
        result = BU.verify_message(
            config,
            request.args.get('rid'),
            session.get('siginin_code'),
            config.public_key,
            request.args.get('id').replace(' ', '+'))
        return json.dumps({
            'authenticated': True if result[1] else False
        })

class GetYadaConfigView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        peer = "http://%s:%s" % (config.peer_host, config.peer_port)
        return json.dumps({
            "baseUrl": "{}".format(peer),
            "transactionUrl": "{}/transaction".format(peer),
            "fastgraphUrl": "{}/post-fastgraph-transaction".format(peer),
            "graphUrl": "{}".format(peer),
            "walletUrl": "{}/wallet".format(peer),
            "loginUrl": "{}/login".format(peer),
            "registerUrl": "{}/create-relationship".format(peer)
        }, indent=4)

class GetSiginCodeView(View):
    def dispatch_request(self):
        session.setdefault('siginin_code', str(uuid.uuid4()))
        return json.dumps({
            'signin_code': session.get('siginin_code')
        })

class TransactionView(View):
    push_service = None
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        if request.method == 'POST':
            items = request.json
            if not isinstance(items, list):
                items = [items, ]
            else:
                items = [item for item in items]
            transactions = []
            for txn in items:
                transaction = Transaction.from_dict(config, txn)
                try:
                    transaction.verify()
                except InvalidTransactionException:
                    mongo.db.failed_transactions.insert({
                        'exception': 'InvalidTransactionException',
                        'txn': txn
                    })
                    print 'InvalidTransactionException'
                    return 'InvalidTransactionException', 400
                except InvalidTransactionSignatureException:
                    print 'InvalidTransactionSignatureException'
                    mongo.db.failed_transactions.insert({
                        'exception': 'InvalidTransactionSignatureException',
                        'txn': txn
                    })
                    return 'InvalidTransactionSignatureException', 400
                except MissingInputTransactionException:
                    pass
                except:
                    raise
                    print 'uknown error'
                    return 'uknown error', 400
                transactions.append(transaction)

            for x in transactions:
                mongo.db.miner_transactions.insert(x.to_dict())
            #job = Process(target=TxnBroadcaster.txn_broadcast_job, args=(transaction,))
            #job.start()
            return json.dumps(request.get_json())
        else:
            rid = request.args.get('rid')
            if rid:
                transactions = BU.get_transactions_by_rid(config, rid, config.bulletin_secret, rid=True, raw=True)
            else:
                transactions = []
            return json.dumps([x for x in transactions])

    def do_push(self, txn, bulletin_secret):
        config = app.config['yada_config']
        mongo = Mongo(config)
        my_bulletin_secret = config.bulletin_secret
        rids = sorted([str(my_bulletin_secret), str(bulletin_secret)], key=str.lower)
        rid = hashlib.sha256(str(rids[0]) + str(rids[1])).digest().encode('hex')

        res1 = mongo.site_db.usernames.find({'rid': rid})
        if res1.count():
            username = res1[0]['username']
        else:
            username = humanhash.humanize(rid)

        if txn.get('relationship') and txn.get('dh_public_key') and txn.get('requester_rid') == rid:
            #friend request
            #if rid is the requester_rid, then we send a friend request notification to the requested_rid
            res = mongo.site_db.fcmtokens.find({"rid": txn['requested_rid']})
            for token in res:
                result = self.push_service.notify_single_device(
                    registration_id=token['token'],
                    message_title='%s sent you a friend request!' % username,
                    message_body="See the request and approve!",
                    extra_kwargs={'priority': 'high'}
                )

        elif txn.get('relationship') and txn.get('dh_public_key') and txn.get('requested_rid') == rid:
            #friend accept
            #if rid is the requested_rid, then we send a friend accepted notification to the requester_rid
            res = mongo.site_db.fcmtokens.find({"rid": txn['requester_rid']})
            for token in res:
                result = self.push_service.notify_single_device(
                    registration_id=token['token'],
                    message_title='%s approved your friend request!' % username,
                    message_body='Say "hi" to your friend!',
                    extra_kwargs={'priority': 'high'}
                )

        elif txn.get('relationship') and not txn.get('dh_public_key') and not txn.get('rid'):
            #post
            #we find all mutual friends of rid and send new post notifications to them
            rids = []
            rids.extend([x['requested_rid'] for x in BU.get_sent_friend_requests(config, rid)])
            rids.extend([x['requester_rid'] for x in BU.get_friend_requests(config, rid)])
            for friend_rid in rids:
                res = mongo.site_db.fcmtokens.find({"rid": friend_rid})
                used_tokens = []
                for token in res:
                    if token['token'] in used_tokens:
                        continue
                    used_tokens.append(token['token'])

                    result = self.push_service.notify_single_device(
                        registration_id=token['token'],
                        message_title='%s has posted something!' % username,
                        message_body='Check out what your friend posted!',
                        extra_kwargs={'priority': 'high'}
                    )

        elif txn.get('relationship') and not txn.get('dh_public_key') and txn.get('rid'):
            #message
            #we find the relationship of the transaction rid and send a new message notification to the rid
            #of the relationship that does not match the arg rid
            txns = [x for x in BU.get_transactions_by_rid(config, txn['rid'], config.bulletin_secret, rid=True, raw=True)]
            rids = []
            rids.extend([x['requested_rid'] for x in txns if 'requested_rid' in x and rid != x['requested_rid']])
            rids.extend([x['requester_rid'] for x in txns if 'requester_rid' in x and rid != x['requester_rid']])
            for friend_rid in rids:
                res = mongo.site_db.fcmtokens.find({"rid": friend_rid})
                used_tokens = []
                for token in res:
                    if token['token'] in used_tokens:
                        continue
                    used_tokens.append(token['token'])

                    result = self.push_service.notify_single_device(
                        registration_id=token['token'],
                        message_title='New message from %s!' % username,
                        message_body='Go see what your friend said!',
                        extra_kwargs={'priority': 'high'}
                    )
                    print result

class TxnBroadcaster(object):
    @classmethod
    def txn_broadcast_job(cls, transaction):
        config = app.config['yada_config']
        Peers.init(config, config.network)
        for peer in Peers.peers:
            try:
                socketIO = SocketIO(peer.host, peer.port, wait_for_connection=False)
                chat_namespace = socketIO.define(ChatNamespace, '/chat')
                chat_namespace.emit('newtransaction', transaction.to_dict())
                socketIO.wait(seconds=1)
                chat_namespace.disconnect()
            except Exception as e:
                pass

class BaseGraphView(View):
    def get_base_graph(self):
        raise Exception("you should implement this method")

class GraphView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        return graph.to_json()

class GraphSentFriendRequestsView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        graph.get_sent_friend_requests()
        return graph.to_json()

class GraphFriendRequestsView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        graph.get_friend_requests()
        return graph.to_json()

class GraphFriendsView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        return graph.to_json()

class GraphPostsView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        graph.get_posts()
        return graph.to_json()

class GraphMessagesView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        graph.get_messages()
        return graph.to_json()

class GraphNewMessagesView(BaseGraphView):
    def dispatch_request(self):
        graph = self.get_base_graph()
        graph.get_new_messages()
        return graph.to_json()


class WalletView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        address = request.args.get('address')
        wallet = {
            'balance': BU.get_wallet_balance(config, address),
            'unspent_transactions': [x for x in BU.get_wallet_unspent_transactions(config, address)]
        }
        return json.dumps(wallet, indent=4)

class FaucetView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        address = request.args.get('address')
        if len(address) < 36:
            exists = mongo.site_db.faucet.find({
                'address': address
            })
            if not exists.count():
                mongo.site_db.faucet.insert({
                    'address': address,
                    'active': True
                })
            return json.dumps({'status': 'ok'})
        else:
            return json.dumps({'status': 'error'}), 400

class RegisterView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        data = {
            'bulletin_secret': config.bulletin_secret,
            'username': config.username,
            'callbackurl': config.callbackurl,
            'to': config.address
        }
        return json.dumps(data, indent=4)

class CreateRelationshipView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        if request.method == 'GET':
            bulletin_secret = request.args.get('bulletin_secret', '')
            username = request.args.get('username', '')
            to = request.args.get('to', '')
        else:
            bulletin_secret = request.json.get('bulletin_secret', '')
            username = request.json.get('username', '')
            to = request.json.get('to', '')

        if not bulletin_secret:
            return 'error: "bulletin_secret" missing', 400

        if not username:
            return 'error: "username" missing', 400

        if not to:
            return 'error: "to" missing', 400
        rid = TU.generate_rid(config, bulletin_secret)
        dup = mongo.db.blocks.find({'transactions.rid': rid})
        if dup.count():
            for txn in dup:
                if txn['public_key'] == config.public_key:
                    return json.dumps({"success": False, "status": "Already added"})

        miner_transactions = mongo.db.miner_transactions.find()
        mtxn_ids = []
        for mtxn in miner_transactions:
            for mtxninput in mtxn['inputs']:
                mtxn_ids.append(mtxninput['id'])

        checked_out_txn_ids = mongo.db.checked_out_txn_ids.find()
        for mtxn in checked_out_txn_ids:
            mtxn_ids.append(mtxn['id'])


        a = os.urandom(32)
        dh_public_key = scalarmult_base(a).encode('hex')
        dh_private_key = a.encode('hex')

        transaction = TransactionFactory(
            config=config,
            bulletin_secret=bulletin_secret,
            username=username,
            fee=0.00,
            public_key=config.public_key,
            dh_public_key=dh_public_key,
            private_key=config.private_key,
            dh_private_key=dh_private_key,
            outputs=[
                Output(to=to, value=0)
            ]
        )

        TU.save(config, transaction.transaction)

        mongo.db.miner_transactions.insert(transaction.transaction.to_dict())
        #job = Process(target=TxnBroadcaster.txn_broadcast_job, args=(transaction.transaction,))
        #job.start()


        my_bulletin_secret = config.bulletin_secret
        bulletin_secrets = sorted([str(my_bulletin_secret), str(bulletin_secret)], key=str.lower)
        rid = hashlib.sha256(str(bulletin_secrets[0]) + str(bulletin_secrets[1])).digest().encode('hex')
        mongo.site_db.friends.insert({'rid': rid, 'relationship': {'bulletin_secret': bulletin_secret}})
        return json.dumps({"success": True})

class MiningPoolView(View):
    def dispatch_request(self):
        config = app.config['yada_config']

        if 'mining_pool' not in app.config:
            app.config['mining_pool'] = MiningPool(config)
        mp = app.config['mining_pool']

        if not hasattr(mp.block_factory, 'header'):
            mp.refresh()

        if not hasattr(mp, 'gen'):
            mp.gen = mp.nonce_generator()

        return json.dumps({
            'nonces': next(mp.gen),
            'target': mp.block_factory.block.target,
            'special_min': mp.block_factory.block.special_min,
            'header': mp.block_factory.header
        })

class MiningPoolSubmitView(View):
    def dispatch_request(self):
        try:
            mp = app.config['mining_pool']
            config = app.config['yada_config']
            mongo = Mongo(config)
            block = mp.block_factory.block
            block.target = mp.block_factory.block.target
            block.version = mp.block_factory.block.version
            block.special_min = mp.block_factory.block.special_min
            block.hash = request.json.get('hash')
            block.nonce = request.json.get('nonce')
            block.signature = BU.generate_signature(block.hash, config.private_key)
            try:
                block.verify()
            except:
                print 'block failed verification'
                return ''

            # submit share
            mongo.db.shares.update({
                'address': request.json.get('address'),
                'index': block.index,
                'hash': block.hash
            },
            {
                'address': request.json.get('address'),
                'index': block.index,
                'hash': block.hash,
                'block': block.to_dict()
            }, upsert=True)

            if int(block.target) > int(block.hash, 16) or block.special_min:
                # broadcast winning block
                mp.broadcast_block(block)
                print 'block ok'
            else:
                print 'share ok'
            return block.to_json()
        except:
            raise
            return 'error'

class MiningPoolExplorerView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        query = {}
        if request.args.get('address'):
            query['address'] = request.args.get('address')
        if request.args.get('index'):
            query['index'] = int(request.args.get('index'))
        res = mongo.db.shares.find_one(query, {'_id': 0}, sort=[('index', -1)])
        if res:
            return 'Pool address: <a href="https://yadacoin.io/explorer?term=%s" target="_blank">%s</a>, Latest block height share: %s' % (config.address, config.address, res.get('index'))
        else:
            return 'Pool address: <a href="https://yadacoin.io/explorer?term=%s" target="_blank">%s</a>, No history' % (config.address, config.address)

class GetBlocksView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        blocks = [x for x in mongo.db.blocks.find({
            '$and': [
                {'index': 
                    {'$gte': int(request.args.get('start_index'))}
                }, 
                {'index': 
                    {'$lte': int(request.args.get('end_index'))}
                }
            ]
        }, {'_id': 0}).sort([('index',1)])]

        def generate(blocks):
            for i, block in enumerate(blocks):
                print 'sending block index:', block['index']
                prefix = '[' if i == 0 else ''
                suffix = ']' if i >= len(blocks) -1  else ','
                yield prefix + json.dumps(block) + suffix
        return Response(generate(blocks), mimetype='application/json')\

class NewBlockView(View):
    def dispatch_request(self):
        bcss = BlockchainSocketServer()
        bcss.on_newblock(request.json)
        return 'ok'

class NewTransactionView(View):
    def dispatch_request(self):
        bcss = BlockchainSocketServer()
        bcss.on_newtransaction(None, request.json)
        return 'ok'

class GetBlockHeightView(View):
    def dispatch_request(self):
        return json.dumps({'block_height': BU.get_latest_block(app.config['yada_config']).get('index')})

class GetBlockByHashView(View):
    def dispatch_request(self):
        mongo = Mongo(app.config['yada_config'])
        return json.dumps(mongo.db.blocks.find_one({'hash': request.args.get('hash')}, {'_id': 0}))

class CreateRawTransactionView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        unspent = BU.get_wallet_unspent_transactions(config, request.json.get('address'))
        unspent_inputs = dict([(x['id'], x) for x in unspent])
        input_sum = 0
        for x in request.json.get('inputs'):
            found = False
            if x['id'] in unspent_inputs:
                for tx in unspent_inputs[x['id']].get('outputs'):
                    input_sum += float(tx['value'])
                found = True
                break
            if not found:
                if mongo.db.blocks.find_one({'transactions.id': x['id']}, {'_id': 0}):
                    return json.dumps({'status': 'error', 'msg': 'output already spent'}), 400
                else:
                    return json.dumps({'status': 'error', 'msg': 'transaction id not in blockchain'}), 400
        output_sum = 0
        for x in request.json.get('outputs'):
            output_sum += float(x['value'])

        if (output_sum + float(request.json.get('fee'))) > input_sum:
            return json.dumps({'status': 'error', 'msg': 'not enough inputs to pay for transaction outputs + fee'})
        try:
            txn = TransactionFactory(
                config,
                public_key=request.json.get('public_key'),
                fee=float(request.json.get('fee')),
                inputs=[Input(x['id']) for x in request.json.get('inputs')],
                outputs=[Output(x['to'], x['value']) for x in request.json.get('outputs')]
            )
        except NotEnoughMoneyException as e:
            return json.dumps({'status': 'error', 'msg': 'not enough coins from referenced inputs to pay for transaction outputs + fee'}), 400
        return '{"header": "%s", "hash": "%s"}' % (txn.header, txn.hash)

class SignRawTransactionView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        res = mongo.db.signed_transactions.find_one({'hash': request.json.get('hash')})
        if res:
            return 'no', 400
        try:
            rid = TU.generate_rid(config, request.json.get('bulletin_secret'))
            my_entry_for_relationship = BU.get_transaction_by_rid(config, rid, config.wif, rid=True)
            their_entry_for_relationship = BU.get_transaction_by_rid(config, rid, config.bulletin_secret, rid=True, raw=True)
            verified = verify_signature(
                base64.b64decode(request.json.get('bulletin_secret')),
                my_entry_for_relationship['relationship']['their_username'],
                their_entry_for_relationship['public_key'].decode('hex')
            )
            if not verified:
                return 'no', 400
            verified = verify_signature(
                base64.b64decode(request.json.get('id')),
                request.json.get('hash'),
                their_entry_for_relationship['public_key'].decode('hex')
            )
            if verified:
                transaction_signature = TU.generate_signature_with_private_key(config.private_key, request.json.get('hash'))
                signature = {
                    'id': transaction_signature,
                    'hash': request.json.get('hash'),
                    'bulletin_secret': config.bulletin_secret,
                    'input': request.json.get('id')
                }
                mongo.db.signed_transactions.insert(signature)
                return json.dumps(signature['id'])
            else:
                return 'no', 400
        except Exception as e:
            return json.dumps({
                'status': 'error',
                'msg': e
            }), 400

class GenerateWalletView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        wallet = config.generate()
        return wallet.to_json()

class GenerateChildWalletView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        try:
            wallet = config.generate(
                xprv=request.json.get('xprv'),
                child=request.json.get('child')
            )
            return wallet.inst_to_json()
        except:
            return json.dumps({
                "status": "error",
                "msg": "error creating child wallet"
            }), 400

class BlockchainSocketServer(socketio.Namespace):
    def on_newblock(self, data):
        #print("new block ", data)
        config = app.config['yada_config']
        mongo = Mongo(config)
        try:
            peer = Peer.from_string(config, request.json.get('peer'))
            block = Block.from_dict(config, data)
            if block.index == 0:
                return
            if int(block.version) != BU.get_version_for_height(block.index):
                print 'rejected old version %s from %s' % (block.version, peer)
                return
            mongo.db.consensus.update({
                'index': block.to_dict().get('index'),
                'id': block.to_dict().get('id'),
                'peer': peer.to_string()
            },
            {
                'block': block.to_dict(),
                'index': block.to_dict().get('index'),
                'id': block.to_dict().get('id'),
                'peer': peer.to_string()
            }, upsert=True)
            
        except Exception as e:
            print "block is bad"
            print e
        except BaseException as e:
            print "block is bad"
            print e
        try:
            requests.post(
                'https://yadacoin.io/peers',
                json.dumps({
                    'host': config.peer_host,
                    'port': config.peer_port
                }),
                headers={
                    "Content-Type": "application/json"
                }
            )
        except:
            print 'ERROR: failed to get peers, exiting...'

    def on_newtransaction(self, sid, data):
        #print("new transaction ", data)
        config = app.config['yada_config']
        mongo = Mongo(config)
        try:
            incoming_txn = Transaction.from_dict(config, data)
        except Exception as e:
            print "transaction is bad"
            print e
        except BaseException as e:
            print "transaction is bad"
            print e

        try:
            dup_check = mongo.db.miner_transactions.find({'id': incoming_txn.transaction_signature})
            if dup_check.count():
                print 'found duplicate'
                return
            mongo.db.miner_transactions.update(incoming_txn.to_dict(), incoming_txn.to_dict(), upsert=True)
        except Exception as e:
            print e
        except BaseException as e:
            print e

class ExplorerSearchView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        mongo = Mongo(config)
        if not request.args.get('term'):
            return '{}'

        try:
            term = int(request.args.get('term'))
            res = mongo.db.blocks.find({'index': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'block_height',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass
        try:
            term = request.args.get('term')
            res = mongo.db.blocks.find({'public_key': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'block_height',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass
        try:
            term = request.args.get('term')
            res = mongo.db.blocks.find({'transactions.public_key': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'block_height',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass
        try:
            term = request.args.get('term')
            re.search(r'[A-Fa-f0-9]{64}', term).group(0)
            res = mongo.db.blocks.find({'hash': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'block_hash',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        try:
            term = request.args.get('term').replace(' ', '+')
            base64.b64decode(term)
            res = mongo.db.blocks.find({'id': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'block_id',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        try:
            term = request.args.get('term')
            re.search(r'[A-Fa-f0-9]{64}', term).group(0)
            res = mongo.db.blocks.find({'transactions.hash': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'txn_hash',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        try:
            term = request.args.get('term')
            re.search(r'[A-Fa-f0-9]{64}', term).group(0)
            res = mongo.db.blocks.find({'transactions.rid': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'txn_rid',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        try:
            term = request.args.get('term').replace(' ', '+')
            base64.b64decode(term)
            res = mongo.db.blocks.find({'transactions.id': term}, {'_id': 0})
            if res.count():
                return json.dumps({
                    'resultType': 'txn_id',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        try:
            term = request.args.get('term')
            re.search(r'[A-Fa-f0-9]+', term).group(0)
            res = mongo.db.blocks.find({'transactions.outputs.to': term}, {'_id': 0}).sort('index', -1)
            if res.count():
                balance = BU.get_wallet_balance(config, term)
                return json.dumps({
                    'balance': balance,
                    'resultType': 'txn_outputs_to',
                    'result': [self.changetime(x) for x in res]
                }, indent=4)
        except:
            pass

        return '{}'

    def changetime(self, block):
        from datetime import datetime
        block['time'] = datetime.utcfromtimestamp(int(block['time'])).strftime('%Y-%m-%dT%H:%M:%S UTC')
        return block

class GetLatestBlockView(View):
    def dispatch_request(self):
        config = app.config['yada_config']
        block = BU.get_latest_block(config)
        return json.dumps(block, indent=4)

class PostFastGraphView(View):
    def dispatch_request(self):
        # after the necessary signatures are gathered, the transaction is sent here.
        config = app.config['yada_config']
        mongo = Mongo(config)
        fastgraph = request.json
        fastgraph = FastGraph.from_dict(config, fastgraph)
        result = fastgraph.verify()
        if not result:
            return 'did not verify', 400
        result = mongo.db.fastgraph_transactions.find_one({
            'txn.hash': fastgraph.hash
        })
        if result:
            return 'duplicate transaction found', 400
        fastgraph.save()
        fastgraph.broadcast()
        return 'ok'

class GetFastGraphView(View):
    def dispatch_request(self):
        # after the necessary signatures are gathered, the transaction is sent here.
        return BU.get_wallet_unspent_fastgraph_transactions(app.config.get('yada_config'), request.args.get('address'))
