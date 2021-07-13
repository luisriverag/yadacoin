import base64
import json
import time
from uuid import uuid4

from tornado.iostream import StreamClosedError
from coincurve import verify_signature

from yadacoin.tcpsocket.base import RPCSocketServer, RPCSocketClient, BaseRPC
from yadacoin.core.chain import CHAIN
from yadacoin.core.block import Block
from yadacoin.core.latestblock import LatestBlock
from yadacoin.core.peer import Peer, Seed, SeedGateway, ServiceProvider, User
from yadacoin.core.config import get_config
from yadacoin.core.transactionutils import TU
from yadacoin.core.identity import Identity
from yadacoin.core.transaction import Transaction
from yadacoin.core.blockchain import Blockchain


class NodeRPC(BaseRPC):
    retry_blocks = {}
    def __init__(self):
        super(NodeRPC, self).__init__()
        self.config = get_config()

    config = None
    async def getblocks(self, body, stream):
        # get blocks should be done only by syncing peers
        params = body.get('params')
        start_index = int(params.get("start_index", 0))
        end_index = min(int(params.get("end_index", 0)), start_index + CHAIN.MAX_BLOCKS_PER_MESSAGE)
        if start_index > self.config.LatestBlock.block.index:
            result = []
        else:
            blocks = self.config.mongo.async_db.blocks.find({
                '$and': [
                    {'index':
                        {'$gte': start_index}

                    },
                    {'index':
                        {'$lte': end_index}
                    }
                ]
            }, {'_id': 0}).sort([('index',1)])
            result = await blocks.to_list(length=CHAIN.MAX_BLOCKS_PER_MESSAGE)
        
        message = {
            'blocks': result,
            'start_index': start_index
        }
        await self.write_result(
            stream,
            'blocksresponse',
            message,
            body['id']
        )
        if stream.peer.protocol_version > 1:
            self.retry_blocks[(stream.peer.rid, 'blocksresponse', start_index, body['id'])] = message

    async def service_provider_request(self, body, stream):
        payload = body.get('params', {})
        if (
            not payload.get('seed_gateway')
        ):
            return
        seed_gateway = SeedGateway.from_dict(payload.get('seed_gateway'))
        if (
            self.config.peer.__class__ == SeedGateway and
            self.config.peer.identity.username_signature == seed_gateway.identity.username_signature
        ):
            service_provider = None
            for x, service_provider in self.config.nodeServer.inbound_streams[ServiceProvider.__name__].items():
                break
            
            if not service_provider:
                return
            payload[service_provider.peer.source_property] = service_provider.peer.to_dict()
            scheme = 'wss' if service_provider.peer.secure else 'ws'
            payload[service_provider.peer.source_property]['websocket_host'] = f'{scheme}://{service_provider.peer.http_host}:{service_provider.peer.http_port}'
            return await self.write_params(
                stream,
                'service_provider_request',
                payload
            )
        payload2 = payload.copy()
        payload2.setdefault(self.config.peer.source_property, self.config.peer.to_dict())
        async for peer_stream in self.config.peer.get_service_provider_request_peers(stream.peer, payload):
            try:
                await self.write_params(
                    peer_stream,
                    'service_provider_request',
                    payload2
                )
            except:
                await peer_stream.write_params(
                    'service_provider_request',
                    payload2
                )

    async def newtxn(self, body, stream):
        payload = body.get('params', {})
        if (
            not payload.get('transaction')
        ):
            return

        txn = Transaction.from_dict(payload.get('transaction'))
        try:
            await txn.verify()
        except:
            return

        to_store = txn.to_dict()
        to_store['sent_to'] = [stream.peer.to_dict()]
        await self.config.mongo.async_db.miner_transactions.replace_one(
            {
                'id': txn.transaction_signature
            },
            to_store,
            upsert=True,
        )

        async for peer_stream in self.config.peer.get_sync_peers():
            if peer_stream.peer.rid == stream.peer.rid:
                continue
            await self.write_params(
                peer_stream,
                'newtxn',
                payload
            )

    async def newblock(self, body, stream):
        payload = body.get('params', {}).get('payload')

        if stream.peer.protocol_version > 1:
            await self.write_result(
                stream,
                'newblock_confirmed',
                body.get('params', {}),
                body['id']
            )

        if not payload.get('block'):
            return

        block = await Block.from_dict(payload.get('block'))

        if block.index > (self.config.LatestBlock.block.index + 100) or block.index < self.config.LatestBlock.block.index:
            return

        blocks, status = await self.config.consensus.build_backward_from_block_to_fork(block, [], stream)

        if status:
            first_inbound_block = blocks[0] if blocks else block
            forward_blocks_chain = await self.config.consensus.build_remote_chain(block)
            forward_blocks = [x async for x in forward_blocks_chain.blocks]
            inbound_blocks = (blocks if blocks else [block]) + forward_blocks[1:]
            inbound_blockchain = await Blockchain.init_async(inbound_blocks, partial=True)
            existing_blockchain = await Blockchain.init_async(self.config.mongo.async_db.blocks.find({'index': {'$gte': first_inbound_block.index}}), partial=True)
            if await existing_blockchain.test_inbound_blockchain(inbound_blockchain):
                await self.config.consensus.integrate_blockchain_with_existing_chain(
                    inbound_blockchain,
                    stream
                )
            else:
                return
        else:
            return

        await self.config.consensus.insert_consensus_block(block, stream.peer)

        async for peer_stream in self.config.peer.get_sync_peers():
            if peer_stream.peer.rid == stream.peer.rid:
                continue
            await self.write_params(
                peer_stream,
                'newblock',
                body.get('params', {})
            )
            if peer_stream.peer.protocol_version > 1:
                self.retry_blocks[(peer_stream.peer.rid, 'newblock', block.hash)] = body.get('params', {})

    async def newblock_confirmed(self, body, stream):
        payload = body.get('result', {}).get('payload')
        block = await Block.from_dict(payload.get('block'))

        if (stream.peer.rid, 'newblock', block.hash) in self.retry_blocks:
            del self.retry_blocks[(stream.peer.rid, 'newblock', block.hash)]

    async def ensure_previous_block(self, block, stream):
        have_prev = await self.ensure_previous_on_blockchain(block)
        if not have_prev:
            have_prev = await self.ensure_previous_in_consensus(block)
            if not have_prev:
                await self.write_params(
                    stream,
                    'getblock',
                    {
                        'hash': block.prev_hash,
                        'index': block.index - 1
                    }
                )
                return False
        return True

    async def ensure_previous_on_blockchain(self, block):
        return await self.config.mongo.async_db.blocks.find_one({
            'hash': block.prev_hash
        })

    async def ensure_previous_in_consensus(self, block):
        return await self.config.mongo.async_db.consensus.find_one({
            'block.hash': block.prev_hash
        })

    async def fill_gap(self, end_index, stream):
        start_block = await self.config.mongo.async_db.blocks.find_one(
            {
                'index': {
                    '$lt': end_index
                }
            },
            sort=[('index', -1)]
        )
        await self.config.nodeShared.write_params(
            stream,
            'getblocks',
            {
                'start_index': start_block['index'] + 1,
                'end_index': end_index - 1
            }
        )


    async def send_block(self, block):
        payload = {
            'payload': {
                'block': block.to_dict()
            }
        }
        async for peer_stream in self.config.peer.get_sync_peers():
            await self.write_params(
                peer_stream,
                'newblock',
                payload
            )
            if peer_stream.peer.protocol_version > 1:
                self.retry_blocks[(peer_stream.peer.rid, 'newblock', block.hash)] = payload

    async def get_next_block(self, block):
        async for peer_stream in self.config.peer.get_sync_peers():
            await self.write_params(
                peer_stream,
                'getblock',
                {
                    'index': block.index + 1
                }
            )

    async def getblock(self, body, stream):
        # get blocks should be done only by syncing peers
        params = body.get('params')
        block_hash = params.get("hash")
        block_index = params.get("index")
        block = await self.config.mongo.async_db.blocks.find_one({'hash': block_hash}, {'_id': 0})
        if not block:
            block = await self.config.mongo.async_db.consensus.find_one({'block.hash': block_hash}, {'_id': 0})
            if block:
                block = block['block']
            else:
                block = await self.config.mongo.async_db.blocks.find_one({'index': block_index}, {'_id': 0})
        if block:
            message = {
                'block': block
            }
            await self.write_result(
                stream,
                'blockresponse',
                message,
                body['id']
            )
            if stream.peer.protocol_version > 1:
                self.retry_blocks[(stream.peer.rid, 'blockresponse', block['hash'], body['id'])] = message

    async def blocksresponse(self, body, stream):
        # get blocks should be done only by syncing peers
        result = body.get('result')
        blocks = result.get('blocks')
        if stream.peer.protocol_version > 1:
            await self.write_result(
                stream,
                'blocksresponse_confirmed',
                body.get('result', {}),
                body['id']
            )
        if not blocks:
            self.config.consensus.syncing = False
            stream.synced = True
            return
        self.config.consensus.syncing = True
        blocks = [await Block.from_dict(x) async for x in blocks]
        first_inbound_block = await Block.from_dict(blocks[0])
        forward_blocks_chain = await self.config.consensus.build_remote_chain(blocks[-1])
        forward_blocks = [x async for x in forward_blocks_chain.blocks]
        inbound_blocks = blocks + forward_blocks[1:]
        inbound_blockchain = await Blockchain.init_async(inbound_blocks, partial=True)
        existing_blockchain = await Blockchain.init_async(self.config.mongo.async_db.blocks.find({'index': {'$gte': first_inbound_block.index}}), partial=True)

        prev_block = await self.ensure_previous_block(first_inbound_block, stream)
        if not prev_block:
            await self.fill_gap(first_inbound_block.index, stream)
            stream.synced = True
            self.config.consensus.syncing = False
            return False
        if await existing_blockchain.test_inbound_blockchain(inbound_blockchain):
            await self.config.consensus.integrate_blockchain_with_existing_chain(
                inbound_blockchain,
                stream
            )
        self.config.consensus.syncing = False

    async def blocksresponse_confirmed(self, body, stream):
        params = body.get('result')
        start_index = params.get('start_index')
        if (stream.peer.rid, 'blocksresponse', start_index, body['id']) in self.retry_blocks:
            del self.retry_blocks[(stream.peer.rid, 'blocksresponse', start_index, body['id'])]

    async def blockresponse(self, body, stream):
        # get blocks should be done only by syncing peers
        result = body.get('result')
        if not result.get("block"):
            if stream.peer.protocol_version > 1:
                await self.write_result(
                    stream,
                    'blockresponse_confirmed',
                    body.get('result', {}),
                    body['id']
                )
            return
        block = await Block.from_dict(result.get("block"))
        if block.index > (self.config.LatestBlock.block.index + 100):
            return
        added = await self.config.consensus.insert_consensus_block(block, stream.peer)

        if stream.peer.protocol_version > 1:
            await self.write_result(
                stream,
                'blockresponse_confirmed',
                body.get('result', {}),
                body['id']
            )

        blocks, status = await self.config.consensus.build_backward_from_block_to_fork(block, [], stream)

        if status:
            first_inbound_block = blocks[0] if blocks else block
            forward_blocks_chain = await self.config.consensus.build_remote_chain(block)
            forward_blocks = [x async for x in forward_blocks_chain.blocks]
            inbound_blocks = (blocks if blocks else [block]) + forward_blocks[1:]
            inbound_blockchain = await Blockchain.init_async(inbound_blocks, partial=True)
            existing_blockchain = await Blockchain.init_async(self.config.mongo.async_db.blocks.find({'index': {'$gte': first_inbound_block.index}}), partial=True)
            if await existing_blockchain.test_inbound_blockchain(inbound_blockchain):
                await self.config.consensus.integrate_blockchain_with_existing_chain(
                    inbound_blockchain,
                    stream
                )

    async def blockresponse_confirmed(self, body, stream):
        result = body.get('result')
        if not result.get("block"):
            return
        block = await Block.from_dict(result.get("block"))
        if (stream.peer.rid, 'blockresponse', block.hash, body['id']) in self.retry_blocks:
            del self.retry_blocks[(stream.peer.rid, 'blockresponse', block.hash, body['id'])]

    async def connect(self, body, stream):
        params = body.get('params')
        if not params.get('peer'):
            stream.close()
            return {}
        generic_peer = Peer.from_dict(params.get('peer'))
        if isinstance(self.config.peer, Seed):

            if generic_peer.identity.username_signature in self.config.seeds:
                peerCls = Seed
            elif generic_peer.identity.username_signature in self.config.seed_gateways:
                peerCls = SeedGateway

        elif isinstance(self.config.peer, SeedGateway):

            if generic_peer.identity.username_signature in self.config.seeds:
                peerCls = Seed
            else:
                peerCls = ServiceProvider

        elif isinstance(self.config.peer, ServiceProvider):

            if generic_peer.identity.username_signature in self.config.seed_gateways:
                peerCls = SeedGateway
            else:
                peerCls = User

        elif isinstance(self.config.peer, User):

            peerCls = User
        else:
            self.config.app_log.error('inbound peer is not defined, disconnecting')
            stream.close()
            return {}

        limit = self.config.peer.__class__.type_limit(peerCls)
        if (len(NodeSocketServer.inbound_pending[peerCls.__name__]) + len(NodeSocketServer.inbound_streams[peerCls.__name__])) >= limit:
            await self.write_result(stream, 'capacity', {}, body['id'])
            stream.close()
            return {}

        try:
            stream.peer = peerCls.from_dict(params.get('peer'))
        except:
            self.config.app_log.error('invalid peer identity')
            stream.close()
            return {}

        if generic_peer.rid in NodeSocketServer.inbound_pending[stream.peer.__class__.__name__]:
            stream.close()
            return {}

        if generic_peer.rid in NodeSocketServer.inbound_streams[stream.peer.__class__.__name__]:
            stream.close()
            return {}

        if generic_peer.rid in self.config.nodeClient.outbound_ignore[stream.peer.__class__.__name__]:
            stream.close()
            return

        if generic_peer.rid in self.config.nodeClient.outbound_pending[stream.peer.__class__.__name__]:
            stream.close()
            return

        if generic_peer.rid in self.config.nodeClient.outbound_streams[stream.peer.__class__.__name__]:
            stream.close()
            return

        try:
            result = verify_signature(
                base64.b64decode(stream.peer.identity.username_signature),
                stream.peer.identity.username.encode(),
                bytes.fromhex(stream.peer.identity.public_key)
            )
            if result:
                self.config.app_log.info('new {} peer is valid'.format(stream.peer.__class__.__name__))
        except:
            self.config.app_log.error('invalid peer identity signature')
            stream.close()
            return {}

        NodeSocketServer.inbound_streams[peerCls.__name__][stream.peer.rid] = stream
        self.config.app_log.info('Connected to {}: {}'.format(stream.peer.__class__.__name__, stream.peer.to_json()))
        return {}

    async def challenge(self, body, stream):
        self.ensure_protocol_version(body, stream)
        params = body.get('params', {})
        challenge = params.get('token')
        signed_challenge = TU.generate_signature(challenge, self.config.private_key)
        if stream.peer.protocol_version > 1:
            await self.write_params(
                stream,
                'authenticate',
                {
                    'peer': self.config.peer.to_dict(),
                    'signed_challenge': signed_challenge
                }
            )
        else:
            await self.write_result(
                stream,
                'authenticate',
                {
                    'peer': self.config.peer.to_dict(),
                    'signed_challenge': signed_challenge
                },
                body['id']
            )
        stream.peer.token = str(uuid4())
        await self.write_params(stream, 'challenge', {
            'peer': self.config.peer.to_dict(),
            'token': stream.peer.token
        })

    async def authenticate(self, body, stream):
        self.ensure_protocol_version(body, stream)
        if stream.peer.protocol_version > 1:
            params = body.get('params', {})
        else:
            params = body.get('result', {})
        signed_challenge = params.get('signed_challenge')
        result = verify_signature(
            base64.b64decode(signed_challenge),
            stream.peer.token.encode(),
            bytes.fromhex(stream.peer.identity.public_key)
        )
        if result:
            stream.peer.authenticated = True
            self.config.app_log.info('Authenticated {}: {}'.format(stream.peer.__class__.__name__, stream.peer.to_json()))
            await self.send_block(self.config.LatestBlock.block)
            await self.get_next_block(self.config.LatestBlock.block)
        else:
            stream.close()

    def ensure_protocol_version(self, body, stream):
        params = body.get('params', {})
        peer = params.get('peer', {})
        protocol_version = peer.get('protocol_version', 1)
        stream.peer.protocol_version = protocol_version


class NodeSocketServer(RPCSocketServer, NodeRPC):

    retry_blocks = {}

    def __init__(self):
        super(NodeSocketServer, self).__init__()
        self.config = get_config()


class NodeSocketClient(RPCSocketClient, NodeRPC):

    retry_blocks = {}

    def __init__(self):
        super(NodeSocketClient, self).__init__()
        self.config = get_config()

    async def connect(self, peer: Peer):
        try:
            stream = await super(NodeSocketClient, self).connect(peer)

            if not stream:
                return

            await self.write_params(
                stream,
                'connect',
                {
                    'peer': self.config.peer.to_dict()
                }
            )

            stream.peer.token = str(uuid4())
            await self.write_params(
                stream,
                'challenge',
                {
                    'peer': self.config.peer.to_dict(),
                    'token': stream.peer.token
                }
            )

            await self.wait_for_data(stream)
        except StreamClosedError:
            get_config().app_log.error('Cannot connect to {}: {}'.format(peer.__class__.__name__, peer.to_json()))
    
    async def challenge(self, body, stream):
        self.ensure_protocol_version(body, stream)
        params = body.get('params', {})
        challenge =  params.get('token')
        signed_challenge = TU.generate_signature(challenge, self.config.private_key)
        if stream.peer.protocol_version > 1:
            await self.write_params(
                stream,
                'authenticate',
                {
                    'peer': self.config.peer.to_dict(),
                    'signed_challenge': signed_challenge
                }
            )
        else:
            await self.write_result(
                stream,
                'authenticate',
                {
                    'peer': self.config.peer.to_dict(),
                    'signed_challenge': signed_challenge
                },
                body['id']
            )
    
    async def capacity(self, body, stream):
        NodeSocketClient.outbound_ignore[stream.peer.__class__.__name__][stream.peer.rid] = stream.peer
        self.config.app_log.warning('{} at full capacity: {}'.format(stream.peer.__class__.__name__, stream.peer.to_json()))
