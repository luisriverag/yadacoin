import json
import socket
import base64
import time
from json.decoder import JSONDecodeError
from uuid import uuid4
from collections import OrderedDict
from traceback import format_exc

from tornado.tcpserver import TCPServer
from tornado.tcpclient import TCPClient
from tornado.iostream import StreamClosedError
from tornado.util import TimeoutError
from coincurve import verify_signature

from yadacoin.core.config import get_config, Config
from yadacoin.core.chain import CHAIN


REQUEST_RESPONSE_MAP = {
    'blockresponse': 'getblock',
    'blocksresponse': 'getblocks',
}

REQUEST_ONLY = [
    'connect',
    'challenge',
    'authenticate',
    'newblock',
    'blockresponse_confirmed',
    'blocksresponse_confirmed',
    'newblock_confirmed',
    'newtxn_confirmed',
    'disconnect',
]

class BaseRPC:
    def __init__(self):
        self.config = get_config()

    async def write_result(self, stream, method, data, req_id):
        await self.write_as_json(stream, method, data, 'result', req_id)

    async def write_params(self, stream, method, data):
        await self.write_as_json(stream, method, data, 'params')

    async def write_as_json(self, stream, method, data, rpc_type, req_id=None):

        rpc_data = {
            'id': req_id if req_id else str(uuid4()),
            'method': method,
            'jsonrpc': 2.0,
            rpc_type: data
        }
        if rpc_type == 'params':
            if method not in stream.message_queue:
                stream.message_queue[method] = {}
            stream.message_queue[method][rpc_data['id']] = rpc_data
        await stream.write('{}\n'.format(json.dumps(rpc_data)).encode())
        self.config.app_log.debug(f'SENT {stream.peer.host} {method} {data} {rpc_type} {req_id}')

class RPCSocketServer(TCPServer, BaseRPC):
    inbound_streams = {}
    inbound_pending = {}
    config = None

    async def handle_stream(self, stream, address):
        stream.synced = False
        stream.syncing = False
        stream.message_queue = {}
        while True:
            try:
                data = await stream.read_until(b"\n")
                body = json.loads(data)
                method = body.get('method')
                if 'result' in body:
                    if method in REQUEST_RESPONSE_MAP:
                        if body['id'] in stream.message_queue.get(REQUEST_RESPONSE_MAP[method], {}):
                            del stream.message_queue[REQUEST_RESPONSE_MAP[method]][body['id']]
                if not hasattr(self, method):
                    continue
                if hasattr(stream, 'peer'):
                    if hasattr(stream.peer, 'host'):
                        self.config.app_log.debug(f'RECEIVED {stream.peer.host} {method} {body}')
                    if hasattr(stream.peer, 'address'):
                        self.config.app_log.debug(f'RECEIVED {stream.peer.address} {method} {body}')
                    id_attr = getattr(stream.peer, stream.peer.id_attribute)
                    if id_attr not in self.inbound_streams[stream.peer.__class__.__name__]:
                        await self.write_params(stream, 'disconnect', {})
                        await self.remove_peer(stream)
                await getattr(self, method)(body, stream)
            except StreamClosedError:
                if hasattr(stream, 'peer'):
                    await self.remove_peer(stream)
                    self.config.app_log.warning('Disconnected from {}: {}'.format(stream.peer.__class__.__name__, stream.peer.to_json()))
                break
            except:
                if hasattr(stream, 'peer'):
                    self.config.app_log.warning('Bad data from {}: {}'.format(stream.peer.__class__.__name__, stream.peer.to_json()))
                    await self.remove_peer(stream)
                stream.close()
                self.config.app_log.debug("{}".format(format_exc()))
                break

    async def remove_peer(self, stream):
        id_attr = getattr(stream.peer, stream.peer.id_attribute)
        if id_attr in self.inbound_streams[stream.peer.__class__.__name__]:
            del self.inbound_streams[stream.peer.__class__.__name__][id_attr]
        if id_attr in self.inbound_pending[stream.peer.__class__.__name__]:
            del self.inbound_pending[stream.peer.__class__.__name__][id_attr]
        stream.close()


class RPCSocketClient(TCPClient):
    outbound_streams = {}
    outbound_pending = {}
    outbound_ignore = {}
    config = None

    async def connect(self, peer):
        try:
            id_attr = getattr(peer, peer.id_attribute)
            if id_attr in self.outbound_ignore[peer.__class__.__name__]:
                return
            if id_attr in self.outbound_pending[peer.__class__.__name__]:
                return
            if id_attr in self.outbound_streams[peer.__class__.__name__]:
                return
            if id_attr in self.config.nodeServer.inbound_pending[peer.__class__.__name__]:
                return
            if id_attr in self.config.nodeServer.inbound_streams[peer.__class__.__name__]:
                return
            if self.config.peer.identity.username_signature == peer.identity.username_signature:
                return
            if (self.config.peer.host, self.config.peer.host) == (peer.host, peer.port):
                return
            self.outbound_pending[peer.__class__.__name__][id_attr] = peer
            stream = await super(RPCSocketClient, self).connect(peer.host, peer.port, timeout=1)
            stream.synced = False
            stream.syncing = False
            stream.message_queue = {}
            stream.peer = peer
            stream.last_activity = int(time.time())
            try:
                result = verify_signature(
                    base64.b64decode(stream.peer.identity.username_signature),
                    stream.peer.identity.username.encode(),
                    bytes.fromhex(stream.peer.identity.public_key)
                )
                if not result:
                    self.config.app_log.warning('new {} peer signature is invalid'.format(peer.__class__.__name__))
                    stream.close()
                    return
                self.config.app_log.info('new {} peer is valid'.format(peer.__class__.__name__))
            except:
                self.config.app_log.warning('invalid peer identity signature')
                stream.close()
                return
            if id_attr in self.outbound_pending[peer.__class__.__name__]:
                del self.outbound_pending[peer.__class__.__name__][id_attr]
            self.outbound_streams[peer.__class__.__name__][id_attr] = stream
            self.config.app_log.info('Connected to {}: {}'.format(peer.__class__.__name__, peer.to_json()))
            return stream
        except StreamClosedError:
            await self.remove_peer(stream)
            self.config.app_log.warning('Streamed closed for {}: {}'.format(peer.__class__.__name__, peer.to_json()))
        except TimeoutError:
            await self.remove_peer(stream)
            self.config.app_log.warning('Timeout connecting to {}: {}'.format(peer.__class__.__name__, peer.to_json()))

    async def wait_for_data(self, stream):
        while True:
            try:
                body = json.loads(await stream.read_until(b"\n"))
                if 'result' in body:
                    if body['method'] in REQUEST_RESPONSE_MAP:
                        if body['id'] in stream.message_queue.get(REQUEST_RESPONSE_MAP[body['method']], {}):
                            del stream.message_queue[REQUEST_RESPONSE_MAP[body['method']]][body['id']]
                if hasattr(stream, 'peer'):
                    self.config.app_log.debug(f'RECEIVED {stream.peer.host} {body["method"]} {body}')
                    id_attr = getattr(stream.peer, stream.peer.id_attribute)
                    if id_attr not in self.outbound_streams[stream.peer.__class__.__name__]:
                        await self.remove_peer(stream)
                stream.last_activity = int(time.time())
                await getattr(self, body.get('method'))(body, stream)
            except StreamClosedError:
                del self.outbound_streams[stream.peer.__class__.__name__][stream.peer.rid]
                break

    async def remove_peer(self, stream):
        if stream.peer.rid in self.outbound_streams[stream.peer.__class__.__name__]:
            del self.outbound_streams[stream.peer.__class__.__name__][stream.peer.rid]
        if stream.peer.rid in self.outbound_pending[stream.peer.__class__.__name__]:
            del self.outbound_pending[stream.peer.__class__.__name__][stream.peer.rid]
        stream.close()
