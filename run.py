#!/usr/bin/env python3
import argparse
import os
import time
import stat
import sys
import subprocess
import datetime
import random
import shutil
import getpass
import traceback
from argparse import RawTextHelpFormatter
from threading import Lock

from pyhmy import (
    Typgpy,
    json_load,
)
from pyhmy import cli

from utils import *

with open("./node/validator_config.json") as f:  # WARNING: assumption of copied file on docker run.
    validator_info = json.load(f)
wallet_passphrase = ""  # WARNING: default passphrase is set here.
bls_key_folder = "/root/node/bls_keys"
shutil.rmtree(bls_key_folder, ignore_errors=True)
os.makedirs(bls_key_folder, exist_ok=True)
imported_bls_key_folder = "/root/harmony_bls_keys"  # WARNING: assumption made on auto_node.sh
node_sh_out_path = "/root/node/node_sh_logs/out.log"  # WARNING: assumption made on auto_node.sh
node_sh_err_path = "/root/node/node_sh_logs/err.log"  # WARNING: assumption made on auto_node.sh
os.makedirs("/root/node/node_sh_logs", exist_ok=True)  # WARNING: assumption made on auto_node.sh

env = os.environ
node_script_source = "https://raw.githubusercontent.com/harmony-one/harmony/master/scripts/node.sh"
directory_lock = Lock()


def parse_args():
    parser = argparse.ArgumentParser(description='== Run a Harmony node & validator automagically ==',
                                     usage="auto_node.sh [--container=CONTAINER_NAME] run [OPTIONS]",
                                     formatter_class=RawTextHelpFormatter, add_help=False)
    parser.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS,
                        help='Show this help message and exit')
    parser.add_argument("--auto-active", action="store_true",
                        help="Always try to set active when EPOS status is inactive.")
    parser.add_argument("--auto-reset", action="store_true",
                        help="Automatically reset node during hard resets.")
    parser.add_argument("--auto-interaction", action="store_true",
                        help="Say yes to all interaction (except wallet pw).")
    parser.add_argument("--clean", action="store_true", help="Clean shared node directory before starting node.")
    parser.add_argument("--wallet-passphrase", action="store_true",
                        help="Toggle specifying a passphrase interactively for the wallet.\n  "
                             "If not toggled, default CLI passphrase will be used.", )
    parser.add_argument("--wallet-passphrase-string", help="Specify passphrase string for validator's wallet.\n  "
                                                           "The passphrase may be exposed on the host machine.\n  ",
                        type=str, default=None)
    parser.add_argument("--bls-passphrase", action="store_true",
                        help="Toggle specifying a passphrase interactively for the BLS key.\n  "
                             "If not toggled, default CLI passphrase will be used.", )
    parser.add_argument("--bls-passphrase-string", help="Specify passphrase string for validator's BLS key.\n  "
                                                        "The passphrase may be exposed on the host machine.\n  ",
                        type=str, default=None)
    parser.add_argument("--shard", default=None,
                        help="Specify shard of generated bls key.\n  "
                             "Only used if no BLS keys are not provided.", type=int)
    parser.add_argument("--network", help="Network to connect to (staking, partner, stress).\n  "
                                          "Default: 'staking'.", type=str, default='staking')
    parser.add_argument("--duration", type=int, help="Duration of how long the node is to run in seconds.\n  "
                                                     "Default is forever.", default=float('inf'))
    parser.add_argument("--beacon-endpoint", dest="endpoint", type=str, default=default_endpoint,
                        help=f"Beacon chain (shard 0) endpoint for staking transactions.\n  "
                             f"Default is {default_endpoint}")
    return parser.parse_args()


def setup():
    cli.environment.update(cli.download("./bin/hmy", replace=False))
    cli.set_binary("./bin/hmy")


def check_min_bal_on_s0(address, amount, endpoint=default_endpoint):
    balances = json_load(cli.single_call(f"hmy --node={endpoint} balances {address}"))
    for bal in balances:
        if bal['shard'] == 0:
            return bal['amount'] >= amount


def import_validator_address():
    if validator_info["validator-addr"] is None:
        print(f"{Typgpy.OKBLUE}Selecting random address in shared CLI keystore to be validator.{Typgpy.ENDC}")
        keys_list = list(cli.get_accounts_keystore().values())
        if not keys_list:
            print(f"{Typgpy.FAIL}Shared CLI keystore has no wallets{Typgpy.ENDC}")
            exit(-1)
        validator_info["validator-addr"] = random.choice(keys_list)
    elif validator_info['validator-addr'] not in cli.get_accounts_keystore().values():
        print(f"{Typgpy.FAIL}Cannot create validator, {validator_info['validator-addr']} "
              f"not in shared CLI keystore.{Typgpy.ENDC}")
        exit(-1)
    return validator_info["validator-addr"]


def import_bls_passphrase():
    if args.bls_passphrase:
        return getpass.getpass(f"Enter passphrase for all given BLS keys\n> ")
    elif args.bls_passphrase_string:
        return args.bls_passphrase_string
    else:
        return ""  # WARNING: default passphrase assumption for CLI


def import_wallet_passphrase():
    if args.wallet_passphrase:
        return getpass.getpass(f"Enter wallet passphrase for {validator_info['validator-addr']}\n> ")
    elif args.wallet_passphrase_string:
        return args.wallet_passphrase_string
    else:
        return ""  # WARNING: default passphrase assumption for CLI


def import_bls(passphrase):
    with open("/tmp/bls_pass", 'w') as fw:
        fw.write(passphrase)
    imported_keys = [k for k in os.listdir(imported_bls_key_folder) if k.endswith(".key")]
    if len(imported_keys) > 0:
        if args.shard is not None:
            print(f"{Typgpy.FAIL}[!] Shard option ignored since BLS keys provided in `./harmony_bls_keys`{Typgpy.ENDC}")
        keys_list = []
        for k in imported_keys:
            try:
                key = json_load(cli.single_call(f"hmy keys recover-bls-key {imported_bls_key_folder}/{k} "
                                                f"--passphrase-file /tmp/bls_pass"))
                keys_list.append(key)
                shutil.copy(f"{imported_bls_key_folder}/{k}", bls_key_folder)
                shutil.copy(f"{imported_bls_key_folder}/{k}", "./bin")  # For CLI
                with open(f"{bls_key_folder}/{key['public-key'].replace('0x', '')}.pass", 'w') as fw:
                    fw.write(passphrase)
            except (RuntimeError, json.JSONDecodeError, shutil.ExecError) as e:
                print(f"{Typgpy.FAIL}Failed to load BLS key {k}, error: {e}{Typgpy.ENDC}")
        if len(keys_list) == 0:
            print(f"{Typgpy.FAIL}Could not import any BLS key, exiting...{Typgpy.ENDC}")
            exit(-1)
        return [k['public-key'] for k in keys_list]
    elif args.shard is not None:
        while True:
            key = json_load(cli.single_call("hmy keys generate-bls-key --passphrase-file /tmp/bls_pass"))
            public_bls_key = key['public-key']
            bls_file_path = key['encrypted-private-key-path']
            shard_id = json_load(cli.single_call(f"hmy --node={args.endpoint} utility "
                                                 f"shard-for-bls {public_bls_key}"))["shard-id"]
            if int(shard_id) != args.shard:
                os.remove(bls_file_path)
            else:
                args.bls_private_key = key['private-key']
                print(f"{Typgpy.OKGREEN}Generated BLS key for shard {shard_id}: "
                      f"{Typgpy.OKBLUE}{public_bls_key}{Typgpy.ENDC}")
                break
        shutil.copy(bls_file_path, bls_key_folder)
        shutil.copy(bls_file_path, "./bin")  # For CLI
        with open(f"{bls_key_folder}/{key['public-key'].replace('0x', '')}.pass", 'w') as fw:
            fw.write(passphrase)
        return [public_bls_key]
    else:
        key = json_load(cli.single_call("hmy keys generate-bls-key --passphrase-file /tmp/bls_pass"))
        public_bls_key = key['public-key']
        bls_file_path = key['encrypted-private-key-path']
        args.bls_private_key = key['private-key']
        shard_id = json_load(cli.single_call(f"hmy --node={args.endpoint} utility "
                                             f"shard-for-bls {public_bls_key}"))["shard-id"]
        print(f"{Typgpy.OKGREEN}Generated BLS key for shard {shard_id}: {Typgpy.OKBLUE}{public_bls_key}{Typgpy.ENDC}")
        shutil.copy(bls_file_path, bls_key_folder)
        shutil.copy(bls_file_path, "./bin")  # For CLI
        with open(f"{bls_key_folder}/{key['public-key'].replace('0x', '')}.pass", 'w') as fw:
            fw.write(passphrase)
        return [public_bls_key]


def import_node_info():
    global wallet_passphrase
    print(f"{Typgpy.HEADER}Importing node info...{Typgpy.ENDC}")

    address = import_validator_address()
    wallet_passphrase = import_wallet_passphrase()
    bls_passphrase = import_bls_passphrase()
    public_bls_keys = import_bls(bls_passphrase)

    print("")
    # Save information for other scripts
    print("~" * 110)
    with open(os.path.abspath("/.val_address"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}Validator address:{Typgpy.ENDC} {address}")
        f.write(address)
    with open(os.path.abspath("/.wallet_passphrase"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}Validator wallet passphrase:{Typgpy.ENDC} {wallet_passphrase}")
        f.write(wallet_passphrase)
    with open(os.path.abspath("/.bls_keys"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}BLS keys:{Typgpy.ENDC} {public_bls_keys}")
        f.write(str(public_bls_keys))
    with open(os.path.abspath("/.bls_passphrase"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}BLS passphrase (for all keys):{Typgpy.ENDC} {bls_passphrase}")
        f.write(wallet_passphrase)
    with open(os.path.abspath("/.network"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}Network:{Typgpy.ENDC} {args.network}")
        f.write(args.network)
    with open(os.path.abspath("/.beacon_endpoint"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}Beacon chain endpoint:{Typgpy.ENDC} {args.endpoint}")
        f.write(args.endpoint)
    with open(os.path.abspath("/.duration"),
              'w') as f:  # WARNING: assumption made of where to store address in other scripts.
        print(f"{Typgpy.OKGREEN}Node duration:{Typgpy.ENDC} {args.duration}")
        f.write(str(args.duration))
    print("~" * 110)
    print("")
    print(f"{Typgpy.HEADER}[!] Copied BLS key file to shared node directory "
          f"with the given passphrase (or default CLI passphrase if none){Typgpy.ENDC}")
    return public_bls_keys


def start_node(bls_keys_path, network, clean=False):
    directory_lock.acquire()
    os.chdir("/root/node")
    if os.path.isfile("/root/node/node.sh"):
        os.remove("/root/node/node.sh")
    r = requests.get(node_script_source)
    with open("node.sh", 'w') as f:
        node_sh = r.content.decode()
        # WARNING: Hack untill node.sh is changed for auto-node.
        node_sh = node_sh.replace("save_pass_file=false", 'save_pass_file=true')
        node_sh = node_sh.replace("sudo", '')
        f.write(node_sh)
    st = os.stat("node.sh")
    os.chmod("node.sh", st.st_mode | stat.S_IEXEC)
    node_args = ["./node.sh", "-N", network, "-z", "-f", bls_keys_path, "-M"]
    if clean:
        node_args.append("-c")
    print(f"{Typgpy.HEADER}Starting node!{Typgpy.ENDC}")
    directory_lock.release()
    with open(node_sh_out_path, 'w+') as fo:
        with open(node_sh_err_path, 'w+') as fe:
            pid = subprocess.Popen(node_args, env=env, stdout=fo, stderr=fe).pid
    return pid


def wait_for_node_liveliness():
    alive = False
    while not alive:
        try:
            get_latest_headers("http://localhost:9500/")
            alive = True
        except requests.exceptions.ConnectionError:
            time.sleep(.5)
            pass
    print(f"{Typgpy.HEADER}\n[!] Node Launched!\n{Typgpy.ENDC}")


def add_key_to_validator(val_info, bls_pub_keys, passphrase):
    print(f"{Typgpy.HEADER}{val_info['validator-addr']} already in list of validators!{Typgpy.ENDC}")
    chain_val_info = json_load(cli.single_call(f"hmy --node={args.endpoint} blockchain "
                                               f"validator information {val_info['validator-addr']}"))["result"]
    bls_keys = chain_val_info["validator"]["bls-public-keys"]
    directory_lock.acquire()
    for k in bls_pub_keys:
        if k not in bls_keys:  # Add imported BLS key to existing validator if needed
            print(f"{Typgpy.OKBLUE}adding bls key: {k} "
                  f"to validator: {val_info['validator-addr']}{Typgpy.ENDC}")
            os.chdir("/root/bin")
            proc = cli.expect_call(f"hmy --node={args.endpoint} staking edit-validator "
                                   f"--validator-addr {val_info['validator-addr']} "
                                   f"--add-bls-key {k} --passphrase-file /.wallet_passphrase ")
            proc.expect("Enter the bls passphrase:\r\n")
            proc.sendline(passphrase)
            proc.expect(pexpect.EOF)
            print(f"\n{Typgpy.OKBLUE}Edit-validator transaction response: "
                  f"{Typgpy.OKGREEN}{proc.before.decode()}{Typgpy.ENDC}")
    directory_lock.release()
    new_val_info = json_load(cli.single_call(f"hmy --node={args.endpoint} blockchain "
                                             f"validator information {val_info['validator-addr']}"))["result"]
    new_bls_keys = new_val_info["validator"]["bls-public-keys"]
    print(f"{Typgpy.OKBLUE}{val_info['validator-addr']} updated bls keys: {new_bls_keys}{Typgpy.ENDC}")
    verify_node_sync()
    print()


def verify_node_sync():
    print(f"{Typgpy.OKBLUE}Verifying Node Sync...{Typgpy.ENDC}")
    wait_for_node_liveliness()
    curr_headers = get_latest_headers("http://localhost:9500/")
    curr_epoch_shard = curr_headers['shard-chain-header']['epoch']
    curr_epoch_beacon = curr_headers['beacon-chain-header']['epoch']
    ref_epoch = get_latest_header(args.endpoint)['epoch']
    while curr_epoch_shard != ref_epoch or curr_epoch_beacon != ref_epoch:
        sys.stdout.write(f"\rWaiting for node to sync: shard epoch ({curr_epoch_shard}/{ref_epoch}) "
                         f"& beacon epoch ({curr_epoch_beacon}/{ref_epoch})")
        sys.stdout.flush()
        time.sleep(1)
        curr_headers = get_latest_headers("http://localhost:9500/")
        curr_epoch_shard = curr_headers['shard-chain-header']['epoch']
        curr_epoch_beacon = curr_headers['beacon-chain-header']['epoch']
        ref_epoch = get_latest_header(args.endpoint)['epoch']
    print(f"\n{Typgpy.OKGREEN}Node synced to current epoch{Typgpy.ENDC}")


def create_new_validator(val_info, bls_pub_keys, passphrase):
    print(f"{Typgpy.HEADER}Checking validator...{Typgpy.ENDC}")
    staking_epoch = get_staking_epoch(args.endpoint)
    curr_epoch = get_current_epoch(args.endpoint)
    print(f"{Typgpy.OKBLUE}Verifying Epoch...{Typgpy.ENDC}")
    while curr_epoch < staking_epoch:  # WARNING: using staking epoch for extra security of configs.
        sys.stdout.write(f"\rWaiting for staking epoch ({staking_epoch}) -- current epoch: {curr_epoch}")
        sys.stdout.flush()
        time.sleep(8)  # Assumption of 8 second block time...
        curr_epoch = get_current_epoch(args.endpoint)
    print(f"{Typgpy.OKGREEN}Network is at or past staking epoch{Typgpy.ENDC}")
    print(f"{Typgpy.OKBLUE}Verifying Balance...{Typgpy.ENDC}")
    # Check validator amount +1 for gas fees.
    if not check_min_bal_on_s0(val_info['validator-addr'], val_info['amount'] + 1, args.endpoint):
        print(f"{Typgpy.FAIL}Cannot create validator, {val_info['validator-addr']} "
              f"does not have sufficient funds.{Typgpy.ENDC}")
        return
    else:
        print(f"{Typgpy.OKGREEN}Address: {val_info['validator-addr']} has enough funds{Typgpy.ENDC}")
    verify_node_sync()
    print(f"\n{Typgpy.OKBLUE}Sending create validator transaction...{Typgpy.ENDC}")
    send_create_validator_tx(val_info, bls_pub_keys, passphrase, args.endpoint)
    print()


def send_create_validator_tx(val_info, bls_pub_keys, passphrase, endpoint):
    directory_lock.acquire()
    os.chdir("/root/bin")  # Needed for implicit BLS key...
    proc = cli.expect_call(f'hmy --node={endpoint} staking create-validator '
                           f'--validator-addr {val_info["validator-addr"]} --name "{val_info["name"]}" '
                           f'--identity "{val_info["identity"]}" --website "{val_info["website"]}" '
                           f'--security-contact "{val_info["security-contact"]}" --details "{val_info["details"]}" '
                           f'--rate {val_info["rate"]} --max-rate {val_info["max-rate"]} '
                           f'--max-change-rate {val_info["max-change-rate"]} '
                           f'--min-self-delegation {val_info["min-self-delegation"]} '
                           f'--max-total-delegation {val_info["max-total-delegation"]} '
                           f'--amount {val_info["amount"]} --bls-pubkeys {",".join(bls_pub_keys)} '
                           f'--passphrase-file /.wallet_passphrase ')
    for _ in range(len(bls_pub_keys)):
        proc.expect("Enter the bls passphrase:\r\n")  # WARNING: assumption about interaction
        proc.sendline(passphrase)
    proc.expect(pexpect.EOF)
    try:
        response = json_load(proc.before.decode())
        print(f"{Typgpy.OKBLUE}Created Validator!\n{Typgpy.OKGREEN}{json.dumps(response, indent=4)}{Typgpy.ENDC}")
    except (json.JSONDecodeError, RuntimeError, pexpect.exceptions):
        print(f"{Typgpy.FAIL}Failed to create validator!\n\tError: {e}"
              f"\n\tMsg:\n{proc.before.decode()}{Typgpy.ENDC}")
    directory_lock.release()


def setup_validator(val_info, bls_pub_keys):
    print(f"{Typgpy.OKBLUE}Create validator config\n{Typgpy.OKGREEN}{json.dumps(val_info, indent=4)}{Typgpy.ENDC}")
    with open("/.bls_passphrase", 'r') as fr:
        bls_passphrase = fr.read()

    # Check BLS key with validator if it exists
    all_val = json_load(cli.single_call(f"hmy --node={args.endpoint} blockchain validator all"))["result"]
    if val_info['validator-addr'] in all_val \
            and (args.auto_interaction
                 or input("Add BLS key to existing validator? [Y]/n \n> ") in {'Y', 'y', 'yes', 'Yes'}):
        print(f"{Typgpy.HEADER}Editing validator...{Typgpy.ENDC}")
        add_key_to_validator(val_info, bls_pub_keys, bls_passphrase)
    elif val_info['validator-addr'] not in all_val \
            and (args.auto_interaction or input("Create validator? [Y]/n \n> ") in {'Y', 'y', 'yes', 'Yes'}):
        print(f"{Typgpy.HEADER}Creating new validator...{Typgpy.ENDC}")
        create_new_validator(val_info, bls_pub_keys, bls_passphrase)


def check_and_activate(address, epos_status_msg):
    if "not eligible" in epos_status_msg or "not signing" in epos_status_msg:
        print(f"{Typgpy.FAIL}Node not active, reactivating...{Typgpy.ENDC}")
        cli.single_call(f"hmy staking edit-validator --validator-addr {address} "
                        f"--active true --node {args.endpoint} --passphrase-file /.wallet_passphrase ")


def run():
    bls_keys = import_node_info()
    shard = json_load(cli.single_call(f"hmy utility shard-for-bls {bls_keys[0].replace('0x', '')} "
                                      f"-n {args.endpoint}"))['shard-id']
    shard_endpoint = get_sharding_structure(args.endpoint)[shard]["http"]
    start_time = time.time()
    pid = start_node(bls_key_folder, args.network, clean=args.clean)
    setup_validator(validator_info, bls_keys)
    wait_for_node_liveliness()
    while get_latest_header('http://localhost:9500/')['blockNumber'] == 0:
        pass
    curr_time = time.time()
    while curr_time - start_time < args.duration:
        try:
            directory_lock.acquire()
            fb_ref_hash = get_block_by_number(1, shard_endpoint).get('hash', None)
            fb_hash = get_block_by_number(1, 'http://localhost:9500/').get('hash', None)
            if args.auto_reset and fb_hash is not None and fb_ref_hash is not None and fb_hash != fb_ref_hash:
                directory_lock.release()
                print(f"\n{Typgpy.HEADER}== HARD RESETTING NODE =={Typgpy.ENDC}\n")
                print(f"{Typgpy.HEADER}This block 1 hash: {fb_hash} !=  Chain block 1 hash: {fb_ref_hash}{Typgpy.ENDC}")
                subprocess.call(["kill", "-9", f"{pid}"])
                subprocess.call(["killall", "-9", "harmony"])
                time.sleep(10)  # Sleep to ensure node is terminated b4 restart
                pid = start_node(bls_key_folder, args.network, clean=args.clean)
                setup_validator(validator_info, bls_keys)
                wait_for_node_liveliness()
                while get_latest_header('http://localhost:9500/')['blockNumber'] == 0:
                    pass
                directory_lock.acquire()
            val_chain_info = get_validator_information(validator_info["validator-addr"], args.endpoint)
            print(f"{Typgpy.HEADER}EPOS status:  {Typgpy.OKGREEN}{val_chain_info['epos-status']}{Typgpy.ENDC}")
            print(f"{Typgpy.HEADER}Current epoch performance: {Typgpy.OKGREEN}"
                  f"{json.dumps(val_chain_info['current-epoch-performance'], indent=4)}{Typgpy.ENDC}")
            print(f"{Typgpy.HEADER}This node's latest header at {datetime.datetime.utcnow()}: "
                  f"{Typgpy.OKGREEN}{json.dumps(get_latest_headers('http://localhost:9500/'), indent=4)}"
                  f"{Typgpy.ENDC}")
            if args.auto_active:
                check_and_activate(validator_info["validator-addr"], val_chain_info['epos-status'])
            time.sleep(8)
            curr_time = time.time()
            directory_lock.release()
        except (json.JSONDecodeError, requests.exceptions.ConnectionError,
                RuntimeError, ConnectionError, KeyError) as e:
            print(f"{Typgpy.FAIL}Error when checking validator. Error: {e}{Typgpy.ENDC}")
            curr_time = time.time()
            directory_lock.release()


if __name__ == "__main__":
    args = parse_args()
    setup()
    try:
        run()
    except Exception as e:
        if isinstance(e, KeyboardInterrupt):
            print(f"{Typgpy.OKGREEN}Killing all harmony processes...{Typgpy.ENDC}")
            subprocess.call(["killall", "harmony"])
            exit()
        traceback.print_exc(file=sys.stdout)
        print(f"{Typgpy.FAIL}Auto node failed with error: {e}{Typgpy.ENDC}")
        print(f"Docker image still running; `auto_node.sh` commands will still work.")
        subprocess.call(['tail', '-f', '/dev/null'], env=env, timeout=None)
