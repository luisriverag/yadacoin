import json
import hashlib
import logging
import time

from yadacoin.core.blockchainutils import BU
from yadacoin.core.transactionutils import TU
from yadacoin.core.graphutils import GraphUtils as GU
from yadacoin.core.crypt import Crypt


class Graph(object):

    def __init__(self, config, mongo, username_signature, ids, rids, key_or_wif=None):
        self.config = config
        self.mongo = mongo
        self.app_log = logging.getLogger('tornado.application')
        self.friend_requests = []
        self.sent_friend_requests = []
        self.friends = []
        self.posts = []
        self.logins = []
        self.messages = []
        self.new_messages = []
        self.reacts = []
        self.comments = []
        self.comment_reacts = []
        self.collection = []
        self.already_added_messages = []
        self.username_signature = str(username_signature)
        self.ids = ids
        self.rids = rids
        self.server_username_signature = str(config.username_signature)
        username_signatures = sorted([str(config.username_signature), str(username_signature)], key=str.lower)
        rid = hashlib.sha256((str(username_signatures[0]) + str(username_signatures[1])).encode('utf-8')).digest().hex()
        self.rid = rid
        self.username = self.config.username

        if key_or_wif in [config.private_key, config.wif]:
            self.wallet_mode = True
        else:
            self.wallet_mode = False
            nodes = GU().get_transactions_by_rid(username_signature, config.username_signature, raw=True, returnheight=True, inc_mempool=True)
            me = None
            them = None
            for node in nodes:
                if node.get('dh_public_key') and self.rid == node.get('rid'):
                    if node.get('public_key') != self.config.public_key:
                        them = node
                        self.friends.append(node)
                    elif node.get('public_key') == self.config.public_key:
                        me = node
                        self.friends.append(node)
                if them and me:
                    break

            if them and me:
                for x in self.friends:
                    for y in x['outputs']:
                        if y['to'] != config.address:
                            self.mongo.site_db.usernames.update({
                                'rid': self.rid,
                                'username': self.username,
                                },
                                {
                                'rid': self.rid,
                                'username': self.username,
                                'to': y['to'],
                                'relationship': {
                                    'username_signature': username_signature
                                }
                            },
                            upsert=True)

    def get_lookup_rids(self):
        lookup_rids = [self.rid,]
        lookup_rids.extend([x['rid'] for x in GU().get_friend_requests(self.rid)])
        lookup_rids.extend([x['rid'] for x in GU().get_sent_friend_requests(self.rid)])
        return list(set(lookup_rids))

    def get_request_rids_for_rid(self):
        lookup_rids = {}
        for x in GU().get_friend_requests(self.rid):
            if x['rid'] not in lookup_rids:
                lookup_rids[x['rid']] = []
            lookup_rids[x['rid']].append(x['requester_rid'])

        for x in GU().get_sent_friend_requests(self.rid):
            if x['rid'] not in lookup_rids:
                lookup_rids[x['rid']] = []
            lookup_rids[x['rid']].append(x['requested_rid'])

        return lookup_rids

    def generate_rid(self, first_username_signature, second_username_signature):
        username_signatures = sorted([str(first_username_signature), str(second_username_signature)], key=str.lower)
        return hashlib.sha256((str(username_signatures[0]) + str(username_signatures[1])).encode('utf-8')).digest().hex()

    async def get_friend_requests(self, search_rid):
        self.friend_requests = []
        self.friend_requests += [x for x in GU().get_friend_requests(search_rid)]

        res = await self.config.mongo.async_db.miner_transactions.find({
            'dh_public_key': {'$ne': ''},
            'relationship': {'$ne': ''},
            'requested_rid': search_rid
        }, {
            '_id': 0
        }).to_list(length=1000)
        for txn in res:
            txn['pending'] = True
            self.friend_requests.append(txn)

    async def get_sent_friend_requests(self, search_rid):
        self.sent_friend_requests = []
        self.sent_friend_requests += [x for x in GU().get_sent_friend_requests(search_rid)]

        res = await self.config.mongo.async_db.miner_transactions.find({
            'dh_public_key': {'$ne': ''},
            'relationship': {'$ne': ''},
            'requester_rid': search_rid
        }, {
            '_id': 0
        }).to_list(length=1000)
        for txn in res:
            txn['pending'] = True
            self.sent_friend_requests.append(txn)

    async def get_messages(self, not_mine=False):
        if self.wallet_mode:
            for transaction in self.mongo.db.miner_transactions.find({"relationship": {"$ne": ""}}):
                try:
                    decrypted = self.config.cipher.decrypt(transaction['relationship'])
                    relationship = json.loads(decrypted.decode('latin1'))
                    transaction['relationship'] = relationship
                except:
                    pass
            rid_transactions = GU().get_transactions_by_rid(
                self.rids,
                username_signature=self.config.username_signature,
                rid=True,
                raw=True,
                returnheight=True,
                requested_rid=True
            )
            self.messages = []
            used_ids = []
            for x in rid_transactions:
                if x.get('id') not in used_ids and x['rid'] and x['relationship']:
                    self.messages.append(x)
                    used_ids.append(x.get('id'))
            if not_mine:
                messages = []
                for x in self.messages:
                    if x['public_key'] != self.config.public_key:
                        messages.append(x)
                self.messages = messages
        else:
            rids = self.get_lookup_rids() + self.rids
            self.messages = [x for x in GU().get_collection(rids)]
        res = await self.config.mongo.async_db.miner_transactions.find({
            'relationship': {'$ne': ''},
            '$or': [
                {'rid': {'$in': self.rids}},
                {'requester_rid': {'$in': self.rids}},
                {'requested_rid': {'$in': self.rids}}
            ]
        }, {
            '_id': 0
        }).to_list(length=1000)
        for txn in res:
            txn['pending'] = True
            self.messages.append(txn)

    async def get_sent_messages(self, not_mine=False):
        if self.wallet_mode:
            for transaction in self.mongo.db.miner_transactions.find({"relationship": {"$ne": ""}}):
                try:
                    decrypted = self.config.cipher.decrypt(transaction['relationship'])
                    relationship = json.loads(decrypted.decode('latin1'))
                    transaction['relationship'] = relationship
                except:
                    pass
            rid_transactions = GU().get_transactions_by_rid(
                self.rids,
                username_signature=self.config.username_signature,
                rid=True,
                raw=True,
                returnheight=True,
                requested_rid=True
            )
            self.messages = []
            used_ids = []
            for x in rid_transactions:
                if x.get('id') not in used_ids and x['rid'] and x['relationship']:
                    self.messages.append(x)
                    used_ids.append(x.get('id'))
            if not_mine:
                messages = []
                for x in self.messages:
                    if x['public_key'] != self.config.public_key:
                        messages.append(x)
                self.messages = messages
        else:
            #rids = self.get_lookup_rids() + self.rids
            self.messages = [x for x in GU().get_collection()]
        res = await self.config.mongo.async_db.miner_transactions.find({
            'relationship': {'$ne': ''},
            '$or': [
                {'rid': {'$in': self.rids}},
                {'requester_rid': {'$in': self.rids}},
                {'requested_rid': {'$in': self.rids}}
            ]
        }, {
            '_id': 0
        }).to_list(length=1000)
        for txn in res:
            txn['pending'] = True
            self.messages.append(txn)


    async def get_new_messages(self):
        await self.get_messages(not_mine=True)
        self.messages = sorted(self.messages, key=lambda x: int(x.get('time', 0)), reverse=True)
        used_rids = []
        for message in self.messages:
            if message['rid'] not in used_rids:
                self.new_messages.append(message)
                used_rids.append(message['rid'])

    def get_group_messages(self):
        if self.wallet_mode:
            self.rid_transactions = GU().get_transactions_by_rid(self.rids, username_signature=self.config.username_signature, rid=True, raw=True, returnheight=True)
        else:
            my_username_signature = self.config.username_signature
            posts = []
            blocked = [x['username'] for x in self.mongo.db.blocked_users.find({'username_signature': self.username_signature})]
            flagged = [x['id'] for x in self.mongo.db.flagged_content.find({'username_signature': self.username_signature})]
            for x in GU().get_posts(self.rid):
                rids = sorted([str(my_username_signature), str(x.get('username_signature'))], key=str.lower)
                rid = hashlib.sha256((str(rids[0]) + str(rids[1])).encode('utf-8')).digest().hex()
                if rid in self.rid_usernames:
                    x['username'] = self.rid_usernames[rid]
                    if x['username'] not in blocked and x['id'] not in flagged:
                        posts.append(x)
            self.posts = posts

    async def get_comments(self):
        if self.wallet_mode:
            self.comments = []
            return

        my_username_signature = self.config.username_signature
        comments = []
        blocked = [x['username'] for x in self.mongo.db.blocked_users.find({'username_signature': self.username_signature})]
        flagged = [x['id'] for x in self.mongo.db.flagged_content.find({'username_signature': self.username_signature})]
        out = {}
        if not self.ids:
            return json.dumps({})
        used_ids = []
        for x in GU().get_comments(self.rid, self.ids):
            if x['relationship'].get('id') not in out:
                out[x['relationship'].get('id')] = []

            rids = sorted([str(my_username_signature), str(x.get('username_signature'))], key=str.lower)
            rid = hashlib.sha256((str(rids[0]) + str(rids[1])).encode('utf-8')).digest().hex()

            if rid in self.rid_usernames:
                x['username'] = self.rid_usernames[rid]
                if x['username'] not in blocked and x['id'] not in flagged:
                    comments.append(x)
            x['id'] = str(x['id'])
            if x['id'] in used_ids:
                continue
            used_ids.append(x['id'])
            if x['username'] not in blocked:
                out[x['relationship'].get('id')].append(x)
        self.comments = out

    async def get_reacts(self):
        if self.wallet_mode:
            self.reacts = []
            return

        my_username_signature = self.config.username_signature
        reacts = []
        blocked = [x['username'] for x in self.mongo.db.blocked_users.find({'username_signature': self.username_signature})]
        flagged = [x['id'] for x in self.mongo.db.flagged_content.find({'username_signature': self.username_signature})]
        out = {}
        if not self.ids:
            return json.dumps({})
        for x in GU().get_reacts(self.rid, self.ids):
            if x['relationship'].get('id') not in out:
                out[x['relationship'].get('id')] = []

            rids = sorted([str(my_username_signature), str(x.get('username_signature'))], key=str.lower)
            rid = hashlib.sha256((str(rids[0]) + str(rids[1])).encode('utf-8')).digest().hex()

            if rid in self.rid_usernames:
                x['username'] = self.rid_usernames[rid]
                if x['username'] not in blocked and x['id'] not in flagged:
                    reacts.append(x)
            x['id'] = str(x['id'])
            if x['username'] not in blocked:
                out[x['relationship'].get('id')].append(x)
        self.reacts = out

    async def get_collection(self):
        rids = self.get_lookup_rids() + self.rids
        self.collection = [x for x in GU().get_collection(rids)]
        res = await self.config.mongo.async_db.miner_transactions.find({
            'relationship': {'$ne': ''},
            '$or': [
                {'rid': {'$in': self.rids}},
                {'requester_rid': {'$in': self.rids}},
                {'requested_rid': {'$in': self.rids}}
            ]
        }, {
            '_id': 0
        }).to_list(length=1000)
        for txn in res:
            txn['pending'] = True
            self.collection.append(txn)

    def from_dict(self, obj):
        self.friends = obj['friends']
        self.sent_friend_requests = obj['sent_friend_requests']
        self.friend_requests = obj['friend_requests']
        self.posts = obj['posts']
        self.logins = obj['logins']
        self.messages = obj['messages']
        self.rid = obj['rid']
        self.username = obj['username']

    def to_dict(self):
        return {
            'friends': self.friends,
            'sent_friend_requests': self.sent_friend_requests,
            'friend_requests': self.friend_requests,
            'posts': self.posts,
            'messages': self.messages,
            'rid': self.rid,
            'username_signature': self.username_signature,
            'server_username_signature': self.server_username_signature,
            'username': self.username,
            'new_messages': self.new_messages,
            'reacts': self.reacts,
            'comments': self.comments,
            'comment_reacts': self.comment_reacts,
            'collection': self.collection
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=4)
