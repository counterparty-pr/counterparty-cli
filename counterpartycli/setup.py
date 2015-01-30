#!/usr/bin/env python

import os, sys
import shutil
import ctypes.util
import configparser, platform
import urllib.request
import tarfile
import appdirs

from counterpartylib.lib import config, util

from decimal import Decimal as D

CURRENT_VERSION = '1.0.0rc4'

# generate commented config file from arguments list (client.CONFIG_ARGS and server.CONFIG_ARGS) and known values
def generate_config_file(filename, config_args, known_config={}, overwrite=False):
    if not overwrite and os.path.exists(filename):
        return

    config_dir = os.path.dirname(os.path.abspath(filename))
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, mode=0o755)

    config_lines = []
    config_lines.append('[Default]')
    config_lines.append('')

    for arg in config_args:
        key = arg[0][-1].replace('--', '')
        value = None
        if key in known_config:
            value = known_config[key]
        elif 'default' in arg[1]:
            value = arg[1]['default']
        if value is None:
            key = '# {}'.format(key)
            value = ''
        elif isinstance(value, bool):
            value = '1' if value else '0'
        elif isinstance(value, (float, D)):
            value = format(value, '.8f')

        config_lines.append('# {}'.format(arg[1]['help']))
        config_lines.append('{} = {}'.format(key, value))
        config_lines.append('')

    with open(filename, 'w', encoding='utf8') as config_file:
        config_file.writelines("\n".join(config_lines))
    os.chmod(filename, 0o660)

def extract_old_config():
    old_config = {}

    old_appdir = appdirs.user_config_dir(appauthor='Counterparty', appname='counterpartyd', roaming=True)
    old_configfile = os.path.join(old_appdir, 'counterpartyd.conf')

    if os.path.exists(old_configfile):
        configfile = configparser.ConfigParser()
        configfile.read(old_configfile)
        if 'Default' in configfile:
            for key in configfile['Default']:
                new_key = key.replace('backend-rpc-', 'backend-')
                new_key = new_key.replace('blockchain-service-name', 'backend-name')
                new_value = configfile['Default'][key].replace('jmcorgan', 'addrindex')
                old_config[new_key] = new_value

    return old_config

def extract_bitcoincore_config():
    bitcoincore_config = {}

    # Figure out the path to the bitcoin.conf file
    if platform.system() == 'Darwin':
        btc_conf_file = os.path.expanduser('~/Library/Application Support/Bitcoin/')
    elif platform.system() == 'Windows':
        btc_conf_file = os.path.join(os.environ['APPDATA'], 'Bitcoin')
    else:
        btc_conf_file = os.path.expanduser('~/.bitcoin')
    btc_conf_file = os.path.join(btc_conf_file, 'bitcoin.conf')

    # Extract contents of bitcoin.conf to build service_url
    if os.path.exists(btc_conf_file):
        conf = {}
        with open(btc_conf_file, 'r') as fd:
            # Bitcoin Core accepts empty rpcuser, not specified in btc_conf_file
            for line in fd.readlines():
                if '#' in line or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                conf[k.strip()] = v.strip()

            config_keys = {
                'rpcport': 'backend-port',
                'rpcuser': 'backend-user',
                'rpcpassword': 'backend-password',
                'rpcssl': 'backend-ssl'
            }

            for bitcoind_key in config_keys:
                if bitcoind_key in conf:
                    counterparty_key = config_keys[bitcoind_key]
                    bitcoincore_config[counterparty_key] = conf[bitcoind_key]

    return bitcoincore_config

def get_server_known_config():
    server_known_config = {}

    bitcoincore_config = extract_bitcoincore_config()
    server_known_config.update(bitcoincore_config)

    old_config = extract_old_config()
    server_known_config.update(old_config)

    return server_known_config

# generate client config from server config
def server_to_client_config(server_config):
    client_config = {}

    config_keys = {
        'backend-connect': 'wallet-connect',
        'backend-port': 'wallet-port',
        'backend-user': 'wallet-user',
        'backend-password': 'wallet-password',
        'backend-ssl': 'wallet-ssl',
        'backend-ssl-verify': 'wallet-ssl-verify',
        'rpc-host': 'counterparty-rpc-connect',
        'rpc-port': 'counterparty-rpc-port',
        'rpc-user': 'counterparty-rpc-user',
        'rpc-password': 'counterparty-rpc-password'
    }

    for server_key in config_keys:
        if server_key in server_config:
            client_key = config_keys[server_key]
            client_config[client_key] = server_config[server_key]

    return client_config

def generate_config_files():
    from counterpartycli.server import CONFIG_ARGS as SERVER_CONFIG_ARGS
    from counterpartycli.client import CONFIG_ARGS as CLIENT_CONFIG_ARGS

    configdir = appdirs.user_config_dir(appauthor=config.XCP_NAME, appname=config.APP_NAME, roaming=True)
    server_configfile = os.path.join(configdir, 'server.conf')
    client_configfile = os.path.join(configdir, 'client.conf')

    server_known_config = get_server_known_config()

    # generate random password
    if 'rpc-password' not in server_known_config:
        server_known_config['rpc-password'] = util.hexlify(util.dhash(os.urandom(16)))

    client_known_config = server_to_client_config(server_known_config)

    if not os.path.exists(server_configfile):
        generate_config_file(server_configfile, SERVER_CONFIG_ARGS, server_known_config)

    if not os.path.exists(client_configfile):
        generate_config_file(client_configfile, CLIENT_CONFIG_ARGS, client_known_config)

def tweak_py2exe_build():
    # py2exe copies only pyc files in site-packages.zip
    # modules with no pyc files must be copied in 'dist/library/'
    import counterpartylib, certifi
    additionals_modules = [counterpartylib, certifi]

    for module in additionals_modules:
        moudle_file = os.path.dirname(module.__file__)
        dest_file = '{}/library/{}'.format(WIN_DIST_DIR, module.__name__)
        shutil.copytree(moudle_file, dest_file)

    # additionals DLLs
    dlls = ['ssleay32.dll', 'libssl32.dll', 'libeay32.dll']
    dlls.append(ctypes.util.find_msvcrt())

    dlls_path = dlls
    for dll in dlls:
        dll_path = ctypes.util.find_library(dll)
        shutil.copy(dll_path, WIN_DIST_DIR)

# Download bootstrap database
def bootstrap(overwrite=True, ask_confirmation=False):
    bootstrap_url = 'https://s3.amazonaws.com/counterparty-bootstrap/counterpartyd-db.latest.tar.gz'
    bootstrap_url_testnet = 'https://s3.amazonaws.com/counterparty-bootstrap/counterpartyd-testnet-db.latest.tar.gz'

    data_dir = appdirs.user_data_dir(appauthor=config.XCP_NAME, appname=config.APP_NAME, roaming=True)
    database = os.path.join(data_dir, '{}.{}.db'.format(config.APP_NAME, config.VERSION_MAJOR))
    database_testnet = os.path.join(data_dir, '{}.{}.testnet.db'.format(config.APP_NAME, config.VERSION_MAJOR))

    if not os.path.exists(data_dir):
        os.makedirs(data_dir, mode=0o755)

    if not overwrite and os.path.exists(database):
        return

    if ask_confirmation:
        question = 'Would you like to bootstrap your local Counterparty database from ‘https://s3.amazonaws.com/counterparty-bootstrap/’? (y/N): '
        if input(question).lower() != 'y':
            return

    print('Downloading mainnet database from {}…'.format(bootstrap_url))
    urllib.request.urlretrieve(bootstrap_url, 'counterpartyd-db.latest.tar.gz')
    print('Extracting…')
    with tarfile.open('counterpartyd-db.latest.tar.gz', 'r:gz') as tar_file:
        tar_file.extractall()
    print('Copying {} to {}…'.format('counterpartyd.9.db', database))
    shutil.copy('counterpartyd.9.db', database)
    os.chmod(database, 0o660)
    os.remove('counterpartyd-db.latest.tar.gz')

    print('Downloading testnet database from {}…'.format(bootstrap_url_testnet))
    urllib.request.urlretrieve(bootstrap_url_testnet, 'counterpartyd-testnet-db.latest.tar.gz')
    print('Extracting…')
    with tarfile.open('counterpartyd-testnet-db.latest.tar.gz', 'r:gz') as tar_file:
        tar_file.extractall()
    print('Copying {} to {}…'.format('counterpartyd.9.testnet.db', database_testnet))
    shutil.copy('counterpartyd.9.testnet.db', database_testnet)
    os.chmod(database_testnet, 0o660)
    os.remove('counterpartyd-testnet-db.latest.tar.gz')
