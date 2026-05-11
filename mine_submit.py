#!/usr/bin/env python3
"""HASH256 CUDA coordinator.

Safe default: no-submit. It mines against the address-bound challenge, verifies any hit,
and only signs/broadcasts when --submit is passed and HASH256_PRIVATE_KEY is set.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTRACT = '0xAC7b5d06fa1e77D08aea40d46cB7C5923A87A0cc'
CHAIN_ID = 1
SEL_MINING_STATE = '0xf06d67bb'       # miningState()
SEL_GET_CHALLENGE = '0xf37381ad'      # getChallenge(address)
SEL_MINE = '0x4d474898'                   # mine(uint256)
DEFAULT_RPCS = [
    'https://ethereum.publicnode.com',
    'https://eth.llamarpc.com',
]


def load_dotenv(path: Path):
    """Tiny .env loader. Existing environment variables win over .env values."""
    if not path.exists():
        return False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ[key] = value
    return True


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.lower() in ('1', 'true', 'yes', 'y', 'on')


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value in (None, '') else int(value)


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if value in (None, '') else float(value)


def env_str(name: str, default=None):
    value = os.environ.get(name)
    return default if value in (None, '') else value


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def strip0x(x: str) -> str:
    return x[2:] if x.startswith(('0x', '0X')) else x


def hex_quantity(n: int) -> str:
    return hex(int(n))


def parse_hex_quantity(x: str) -> int:
    if x is None:
        raise ValueError('missing hex quantity')
    return int(x, 16)


def pad_addr(addr: str) -> str:
    h = strip0x(addr).lower()
    if len(h) != 40:
        raise ValueError(f'bad address length: {addr}')
    int(h, 16)
    return '0' * 24 + h


def encode_uint256(n: int) -> str:
    if n < 0 or n >= 1 << 256:
        raise ValueError('uint256 out of range')
    return f'{n:064x}'


class Rpc:
    def __init__(self, urls):
        self.urls = urls
        self.next_id = 1

    def call(self, method, params):
        payload = {'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params}
        self.next_id += 1
        data = json.dumps(payload).encode()
        last = None
        for url in self.urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={'content-type': 'application/json', 'user-agent': 'hash256-cuda-coordinator'},
                )
                with urllib.request.urlopen(req, timeout=25) as r:
                    body = r.read()
                j = json.loads(body)
                if 'error' in j:
                    last = f'{url}: {j["error"]}'
                    continue
                return j['result']
            except Exception as exc:
                last = f'{url}: {exc!r}'
        raise RuntimeError(f'RPC {method} failed: {last}')

    def eth_call(self, data, block='latest'):
        return self.call('eth_call', [{'to': CONTRACT, 'data': data}, block])


def decode_words(hexstr):
    h = strip0x(hexstr)
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def get_state(rpc: Rpc, wallet: str):
    state = decode_words(rpc.eth_call(SEL_MINING_STATE))
    challenge = rpc.eth_call(SEL_GET_CHALLENGE + pad_addr(wallet))
    target = '0x' + state[2].to_bytes(32, 'big').hex()
    return {
        'contract': CONTRACT,
        'wallet': wallet,
        'challenge': challenge,
        'target': target,
        'era': state[0],
        'reward_wei': state[1],
        'difficulty_as_target': state[2],
        'minted_wei': state[3],
        'remaining_wei': state[4],
        'epoch': state[5],
        'epochBlocksLeft': state[6],
    }


def verify_hit(challenge_hex: str, target_hex: str, nonce_hex: str, expected_hash_hex: str | None = None):
    from eth_utils import keccak

    challenge = bytes.fromhex(strip0x(challenge_hex))
    target = int(strip0x(target_hex), 16)
    nonce = bytes.fromhex(strip0x(nonce_hex))
    if len(challenge) != 32 or len(nonce) != 32:
        return False, None, 'bad challenge/nonce length'
    digest = keccak(challenge + nonce)
    got = '0x' + digest.hex()
    if expected_hash_hex and got.lower() != expected_hash_hex.lower():
        return False, got, f'hash mismatch expected {expected_hash_hex}'
    if int.from_bytes(digest, 'big') >= target:
        return False, got, 'hash >= target'
    return True, got, None


def run_miner(args, state, rpc: Rpc, wallet: str):
    """Run one CUDA mining slice.

    The CUDA binary itself searches a fixed challenge/target.  This coordinator
    polls chain state while the subprocess is running and aborts the slice as
    soon as challenge/target/epoch changes, so the next loop mines against the
    fresh difficulty instead of wasting a full round on stale work.
    """
    prefix = args.prefix or ('0x' + secrets.token_bytes(24).hex())
    cmd = [
        args.binary, '--benchmark', '--seconds', str(args.round_seconds),
        '--device', str(args.device), '--threads', str(args.threads),
        '--blocks', str(args.blocks), '--iters', str(args.iters),
        '--challenge', state['challenge'], '--target', state['target'],
        '--prefix', prefix,
    ]
    eprint('[miner]', ' '.join(cmd))
    proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stale_reason = None
    while proc.poll() is None:
        if not args.restart_on_state_change or args.state_poll_seconds <= 0:
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            continue
        try:
            proc.wait(timeout=args.state_poll_seconds)
            break
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is not None:
            break
        try:
            fresh = get_state(rpc, wallet)
        except Exception as exc:
            eprint(f'[warn] state poll failed while miner is running: {exc}')
            continue
        changed = [
            key for key in ('challenge', 'target', 'epoch')
            if str(fresh.get(key)).lower() != str(state.get(key)).lower()
        ]
        if changed:
            stale_reason = {
                'changed': changed,
                'old_epoch': state.get('epoch'),
                'new_epoch': fresh.get('epoch'),
                'old_target': state.get('target'),
                'new_target': fresh.get('target'),
            }
            eprint(f'[retarget] live state changed ({",".join(changed)}); stopping stale CUDA slice')
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            if stderr:
                eprint(stderr.strip())
            return {
                'found': False,
                'stale_restarted': True,
                'prefix': prefix,
                'reason': stale_reason,
                'stdout_tail': stdout[-500:],
            }

    stdout, stderr = proc.communicate()
    if stderr:
        eprint(stderr.strip())
    if proc.returncode != 0:
        raise RuntimeError(f'miner exited {proc.returncode}: {stdout[-500:]}')
    start = stdout.find('{')
    if start < 0:
        raise RuntimeError(f'miner produced no JSON: {stdout[-500:]}')
    out = json.loads(stdout[start:])
    out.setdefault('prefix', prefix)
    return out


def gwei_to_wei(x) -> int:
    return int(Decimal(str(x)) * Decimal(1_000_000_000))


def clamp_gas(gas: int, min_gas: int, max_gas: int, mult: Decimal) -> int:
    gas = int(Decimal(gas) * mult)
    return max(min_gas, min(max_gas, gas))


def build_tx(rpc: Rpc, account, nonce_u256: int, args):
    data = SEL_MINE + encode_uint256(nonce_u256)
    from_addr = account.address
    tx_for_est = {'from': from_addr, 'to': CONTRACT, 'data': data, 'value': '0x0'}
    try:
        est = parse_hex_quantity(rpc.call('eth_estimateGas', [tx_for_est]))
        gas = clamp_gas(est, args.min_gas, args.max_gas, Decimal(str(args.gas_multiplier)))
    except Exception as exc:
        eprint(f'[warn] eth_estimateGas failed, fallback gas={args.fallback_gas}: {exc}')
        gas = args.fallback_gas

    latest = rpc.call('eth_getBlockByNumber', ['latest', False])
    base_fee = parse_hex_quantity(latest.get('baseFeePerGas') or '0x0')
    priority = gwei_to_wei(args.priority_fee_gwei)
    if args.max_fee_gwei is not None:
        max_fee = gwei_to_wei(args.max_fee_gwei)
    else:
        max_fee = int(Decimal(base_fee) * Decimal(str(args.base_fee_multiplier))) + priority
    if max_fee < priority:
        max_fee = priority

    tx_count = parse_hex_quantity(rpc.call('eth_getTransactionCount', [from_addr, 'pending']))
    tx = {
        'type': 2,
        'chainId': CHAIN_ID,
        'nonce': tx_count,
        'to': CONTRACT,
        'value': 0,
        'data': data,
        'gas': gas,
        'maxFeePerGas': max_fee,
        'maxPriorityFeePerGas': priority,
    }
    return tx, {'estimated_gas': est if 'est' in locals() else None, 'base_fee': base_fee}


def send_tx(rpc: Rpc, account, tx):
    signed = account.sign_transaction(tx)
    raw = getattr(signed, 'raw_transaction', None) or signed.rawTransaction
    return rpc.call('eth_sendRawTransaction', ['0x' + raw.hex()])


def wait_receipt(rpc: Rpc, tx_hash: str, timeout_sec: int):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        receipt = rpc.call('eth_getTransactionReceipt', [tx_hash])
        if receipt:
            return receipt
        time.sleep(3)
    return None


def main():
    env_loaded = load_dotenv(ROOT / '.env')

    ap = argparse.ArgumentParser(description='HASH256 CUDA miner coordinator; --submit required for real txs')
    ap.add_argument('--wallet', default=env_str('HASH256_WALLET'), help='0x address. Optional when HASH256_PRIVATE_KEY is set.')
    ap.add_argument('--submit', action='store_true', default=env_bool('HASH256_SUBMIT', False), help='Actually sign and broadcast mine(nonce). Default is no-submit.')
    ap.add_argument('--once', action='store_true', default=env_bool('HASH256_ONCE', False), help='Exit after first found nonce / submitted tx.')
    ap.add_argument('--max-rounds', type=int, default=env_int('HASH256_MAX_ROUNDS', 0), help='0 = unlimited')
    ap.add_argument('--round-seconds', type=int, default=env_int('HASH256_ROUND_SECONDS', 20), help='Miner subprocess duration before state refresh')
    ap.add_argument('--state-poll-seconds', type=float, default=env_float('HASH256_STATE_POLL_SECONDS', 3.0), help='Poll chain state while CUDA is running; 0 disables mid-round polling')
    ap.add_argument('--restart-on-state-change', action=argparse.BooleanOptionalAction, default=env_bool('HASH256_RESTART_ON_STATE_CHANGE', True), help='Stop/restart CUDA slice when challenge/target/epoch changes')
    ap.add_argument('--rpc', action='append', default=[], help='Optional RPC URL; may repeat. Default uses public RPCs.')
    ap.add_argument('--binary', default=env_str('HASH256_CUDA_BINARY', str(ROOT / 'hash256-cuda')))
    ap.add_argument('--device', type=int, default=env_int('HASH256_DEVICE', 0))
    ap.add_argument('--threads', type=int, default=env_int('HASH256_THREADS', 128))
    ap.add_argument('--blocks', type=int, default=env_int('HASH256_BLOCKS', 5440))
    ap.add_argument('--iters', type=int, default=env_int('HASH256_ITERS', 256))
    ap.add_argument('--prefix', default=env_str('HASH256_PREFIX'), help='fixed 24-byte nonce prefix for debugging only')
    ap.add_argument('--priority-fee-gwei', type=str, default=env_str('HASH256_PRIORITY_FEE_GWEI', '3'))
    ap.add_argument('--max-fee-gwei', type=str, default=env_str('HASH256_MAX_FEE_GWEI'))
    ap.add_argument('--base-fee-multiplier', type=str, default=env_str('HASH256_BASE_FEE_MULTIPLIER', '2'))
    ap.add_argument('--gas-multiplier', type=str, default=env_str('HASH256_GAS_MULTIPLIER', '1.5'))
    ap.add_argument('--min-gas', type=int, default=env_int('HASH256_MIN_GAS', 200000))
    ap.add_argument('--max-gas', type=int, default=env_int('HASH256_MAX_GAS', 400000))
    ap.add_argument('--fallback-gas', type=int, default=env_int('HASH256_FALLBACK_GAS', 300000))
    ap.add_argument('--receipt-timeout', type=int, default=env_int('HASH256_RECEIPT_TIMEOUT', 120))
    args = ap.parse_args()

    try:
        from eth_account import Account
        from eth_utils import to_checksum_address
    except ModuleNotFoundError as exc:
        raise SystemExit(f'Missing Python dependency {exc.name!r}. Install with: python -m pip install -r requirements.txt')

    pk = os.environ.get('HASH256_PRIVATE_KEY')
    account = None
    if pk:
        account = Account.from_key(pk)
    if args.submit and not account:
        raise SystemExit('Refusing to submit: set HASH256_PRIVATE_KEY in environment')
    wallet = args.wallet or (account.address if account else None)
    if not wallet:
        raise SystemExit('Provide --wallet for no-submit mode, or set HASH256_PRIVATE_KEY')
    wallet = to_checksum_address(wallet)

    rpc_urls = []
    # Default is public RPCs. HASH256_RPC is only honored when explicitly set in
    # the process environment or .env; otherwise no RPC config is required.
    if os.environ.get('HASH256_RPC'):
        rpc_urls.append(os.environ['HASH256_RPC'])
    rpc_urls += args.rpc
    rpc_urls += DEFAULT_RPCS
    rpc = Rpc(rpc_urls)

    mode = 'SUBMIT' if args.submit else 'NO-SUBMIT'
    eprint(f'[config] .env={"loaded" if env_loaded else "not found"}; rpc=public-default+{len(rpc_urls) - len(DEFAULT_RPCS)} override(s)')
    eprint(f'[mode] {mode}; wallet={wallet}; contract={CONTRACT}')
    rounds = 0
    submitted = 0
    while True:
        rounds += 1
        state = get_state(rpc, wallet)
        print(json.dumps({'event': 'state', **state}, indent=2), flush=True)
        out = run_miner(args, state, rpc, wallet)
        print(json.dumps({'event': 'miner', **out}, indent=2), flush=True)
        if not out.get('found'):
            if args.max_rounds and rounds >= args.max_rounds:
                break
            continue

        ok, got_hash, reason = verify_hit(state['challenge'], state['target'], out['nonce'], out.get('hash'))
        print(json.dumps({'event': 'verify_initial', 'ok': ok, 'hash': got_hash, 'reason': reason}, indent=2), flush=True)
        if not ok:
            continue

        # Re-read state immediately before submit to avoid stale epoch/difficulty.
        fresh = get_state(rpc, wallet)
        ok2, got_hash2, reason2 = verify_hit(fresh['challenge'], fresh['target'], out['nonce'], None)
        print(json.dumps({'event': 'verify_fresh', 'ok': ok2, 'hash': got_hash2, 'reason': reason2, 'epoch': fresh['epoch'], 'epochBlocksLeft': fresh['epochBlocksLeft']}, indent=2), flush=True)
        if not ok2:
            eprint('[stale] nonce no longer valid under fresh challenge/target; restarting')
            if args.max_rounds and rounds >= args.max_rounds:
                break
            continue

        nonce_u256 = int(strip0x(out['nonce']), 16)
        if not args.submit:
            print(json.dumps({'event': 'found_no_submit', 'nonce_uint256': nonce_u256, 'nonce': out['nonce'], 'hash': got_hash2}, indent=2), flush=True)
            if args.once:
                break
            if args.max_rounds and rounds >= args.max_rounds:
                break
            continue

        tx, meta = build_tx(rpc, account, nonce_u256, args)
        print(json.dumps({
            'event': 'tx_built',
            'nonce_uint256': nonce_u256,
            'tx_nonce': tx['nonce'],
            'gas': tx['gas'],
            'maxFeePerGas': tx['maxFeePerGas'],
            'maxPriorityFeePerGas': tx['maxPriorityFeePerGas'],
            **meta,
        }, indent=2), flush=True)
        tx_hash = send_tx(rpc, account, tx)
        submitted += 1
        print(json.dumps({'event': 'tx_sent', 'tx_hash': tx_hash}, indent=2), flush=True)
        receipt = wait_receipt(rpc, tx_hash, args.receipt_timeout)
        print(json.dumps({'event': 'receipt', 'tx_hash': tx_hash, 'receipt': receipt}, indent=2), flush=True)
        if args.once:
            break
        if args.max_rounds and rounds >= args.max_rounds:
            break

    eprint(f'[done] rounds={rounds} submitted={submitted}')


if __name__ == '__main__':
    main()
