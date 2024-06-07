# yadacoin

[![Build and Release](https://github.com/pdxwebdev/yadacoin/actions/workflows/main.yml/badge.svg)](https://github.com/pdxwebdev/yadacoin/actions/workflows/main.yml)

## Setup

### Ubuntu 22 install command:

`sudo wget -O - https://raw.githubusercontent.com/pdxwebdev/yadacoin/master/yadanodesetup.sh | sudo bash`

### Windows installer:

https://yadacoin.io/download

## Configuration

- modes
  - type: array
  - default: ["node", "web", "pool"]
  - description: This setting defines all the modules to initialize when running the node. Node - will initialize all of the networking for exchanging blocks, syncing, transactions, etc. Web - will enable the http interface which can be used in your browser including the pool information and wallet app pages.
- root_app
  - type: string
  - default: "yadacoinpool"
  - description: If multiple http apps are loaded, this setting tells the node which app owns the root / path in the case of a conflict.
- seed
  - type: string
  - default: auto-generated
  - description: This is an auto-generated set of words representing your private key.
- xprv
  - type: string
  - default: auto-generated
  - description: Extended private key. This allows a heirarchy of keys to be created. Useful for exchanges with many child wallets.
- public_key
  - type: string
  - default: auto-generated
  - description: Public key for the corresponding private key.
- address
  - type: string
  - default: auto-generated
  - description: Bitcoin-style address for the corresponding public key
- private_key
  - type: string
  - default: auto-generated
  - description: Private key for the corresponding public key.
- wif
  - type: string
  - default: auto-generated
  - description: Bitcoin-style Wallet Import Format string for the corresponding private key.
- username_signature
  - type: string
  - default: auto-generated
  - description: The signature generated when this wallet signs the username field.
- mongodb_host
  - type: string
  - default: localhost
  - description: The server where the mongo db is located.
- mongodb_username
  - type: string
  - default: undefined
  - description: The username to authenticate against mongodb.
- mongodb_password
  - type: string
  - default: undefined
  - description: The password to authenticate against mongodb.
- api_whitelist
  - type: array
  - default: []
  - description: An array of IP addresses that are allowed to access your node. ie. ["ip.address.goes.here"]
- username
  - type: string
  - default: ""
  - description: This is the username other users on the network will see when interacting with you on the network.
- network
  - type: string
  - default: mainnet
  - description: Tell the node which network to use. Possible values are mainnet, testnet, regnet.
- database
  - type: string
  - default: yadacoin
  - description: The name of the mongodb database where all collections/yadacoin data will be stored.
- site_database
  - type: string
  - default: yadacoin_site
  - description: The name of the mongodb database where all third-party app data will be stored.
- peer_host
  - type: string
  - default: auto-generated
  - description: The IP address used by the network to access your node.
- peer_port
  - type: string
  - default: 8000
  - description: The port used by the network to access your node
- peer_type
  - type: string
  - default: user
  - description: The node type that determines when this node will place itself in the network topology. Possible values are user, service_provider, seed_gateway, and seed. If you would like to be a seed node, then you'll need to also run service_provider and seed_gateway nodes for your seed node. You'll also need to submit a pull request, requesting your servers be added to the network.
- serve_host
  - type: string
  - default: 0.0.0.0
  - description: The IP address bound by the node when initializing the server.
- serve_port
  - type: string
  - default: 8000
  - description: The port bound by the node when initializing the server.
- ssl
  - type: object
  - default: undefined
  - description: Specify the SSL information to enable https on your web server.
  - nested properties:
    - cafile
      - type: string
      - default: undefined
      - description: The absolute file path to your CA file.
    - certfile
      - type: string
      - default: undefined
      - description: The absolute file path to your Cetfificate file.
    - keyfile
      - type: string
      - default: undefined
      - description: The absolute file path to your Key file.
    - common_name
      - type: string
      - default: undefined
      - description: The common name used in your certificate.
    - port
      - type: integer
      - default: undefined
      - description: The port you wish to use for your SSL connections.
- origin
  - type: string
  - default: ""
  - description: Depricated
- fcm_key
  - type: string
  - default: undefined
  - description: Depricated
- sia_api_key
  - type: string
  - default: undefined
  - description: Depricated
- jwt_public_key
  - type: string
  - default: auto-generated
  - description: This value is generated to perform JWT auth for yadacoin apps.
- callbackurl
  - type: string
  - default: undefined
  - description: Depricated
- wallet_host_port
  - type: string
  - default: "http://localhost:8001"
  - description: The url used to contact the node from the wallet user interface. You may want to change this is you would like to access your wallet remotely.
- credits_per_share
  - type: decimal
  - default: 5
  - description: Specifies the number of credits a user earch for every share they submit to your pool.
- shares_required
  - type: bool
  - default: false
  - description: Specifies if shares are required to use an app on your node.
- pool_payout
  - type: bool
  - default: false
  - description: Specifies if your pool will payout to the addresses submitting shares. If false, the pool will keep all won coins in it's own wallet.
- pool_take
  - type: decimal
  - default: .01
  - description: Specifies the percentage of coins that are awarded to the pool for each block.
- pool_public_key
  - type: string
  - default: undefined
  - description: This allows you to specify a pool public key other than the current node in the case where you want to provide stats on the web and run the pool on a separate server.
- stratum_pool_port
  - type: integer
  - default: 3333
  - description: The port where your pool can be accessed by mining rigs.
- payout_frequency
  - type: integer
  - default: 6
  - description: This specifies the number of blocks that must by won by the pool before a payout can take place.
- max_miners
  - type: integer
  - default: 100
  - description: This specifies the number of miners for your pool.
- max_peers
  - type: integer
  - default: 20
  - description: This specifies the number of peers that can connect to your node.
- pool_diff
  - type: integer
  - default: 100000
  - description: This specifies the difficulty for pool shares in both xmrig versions 2/3
- email
  - type: object
  - default: undefined
  - description: Specify the email server you wish to use for notifications, communication, etc.
    - smtp_server
      - type: string
      - default: undefined
      - description: The hostname or IP address of your smtp server.
    - smtp_port
      - type: number
      - default: undefined
      - description: The port of your smtp server.
    - username
      - type: string
      - default: undefined
      - description: The username of your email account.
    - password
      - type: string
      - default: undefined
      - description: The password of your email account.
- skynet_url
  - type: string
  - default: ''
  - description: Specify the url of your skynet server.
- skynet_api_key
  - type: string
  - default: ''
  - description: Specify the api password for your skynet server.
- web_jwt_expiry
  - type: string
  - default: 23040
  - description: Specify the validity period for a json web token in seconds.
- websocket_host_port
  - type: string
  - default: 'ws://localhost:8000/websocket'
  - description: Specify the host and port of your websocket.
- tcp_traffic_debug
  - type: bool
  - default: undefined
  - description: Specify if you want all tcp traffic in debug logging.
- debug_memory
  - type: bool
  - default: undefined
  - description: Specify if you want a complete breakdown of memory usage by object type in status output.
- websocket_traffic_debug
  - type: bool
  - default: undefined
  - description: Specify if you want all websocket traffic in debug logging.
- mongo_debug
  - type: bool
  - default: undefined
  - description: Specify if you want all Mongo DB queries to be logged and profiled.
- peers_wait
  - type: integer
  - default: 3
  - description: Specify the number of seconds to wait before attempting to reconnect to peers.
- status_wait
  - type: integer
  - default: 10
  - description: Specify the number of seconds to wait before printing status message to terminal.
- queue_processor_wait
  - type: integer
  - default: 10
  - description: Specify the number of seconds to wait before checking for new transactions to process.
- block_checker_wait
  - type: integer
  - default: 1
  - description: Specify the number of seconds to wait before checking the for block height changes and updating peers.
- message_sender_wait
  - type: integer
  - default: 10
  - description: Specify the number of seconds to wait before retrying messages for transactions, blocks, etc.
- pool_payer_wait
  - type: integer
  - default: 120
  - description: Specify the number of seconds to wait before running the pool payout process.
- cache_validator_wait
  - type: integer
  - default: 30
  - description: Specify the number of seconds to wait before running the cache validator process.
- mempool_cleaner_wait
  - type: integer
  - default: 1200
  - description: Specify the number of seconds to wait before clearing the mempool of old and invalid transactions.
- nonce_processor_wait
  - type: integer
  - default: 1
  - description: Specify the number of seconds to wait before checking for new share submissions to process.
- mongo_query_timeout
  - type: integer
  - default: 30000
  - description: Specify the max number of milliseconds of execution time for all mongo queries.
- http_request_timeout
  - type: integer
  - default: 3000
  - description: Specify the max number of milliseconds of execution time for all http requests.
- log_health_status
  - type: bool
  - default: undefined
  - description: Specify if you want all Mongo DB queries to be logged and profiled.

## Development Environment

We use Black, Autoflake, isort, and commit message enforcement as pre-commit hooks. To install the hooks, run the following commands:

```
pip install pre-commit mongomock black autoflake isort pytest
pre-commit install
pre-commit install --hook-type pre-push
pre-commit install -t commit-msg
pre-commit autoupdate
```
