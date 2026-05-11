#!/usr/bin/env python3
import argparse, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    p = argparse.ArgumentParser(description='Fetch live HASH256 state and run CUDA miner without submitting tx.')
    p.add_argument('wallet', help='0x wallet address used for address-bound challenge')
    p.add_argument('--seconds', type=int, default=60)
    p.add_argument('--device', type=int, default=0)
    p.add_argument('--threads', type=int, default=128)
    p.add_argument('--blocks', type=int, default=5440)
    p.add_argument('--iters', type=int, default=256)
    p.add_argument('--binary', default=str(ROOT / 'hash256-cuda'))
    args = p.parse_args()

    state_raw = subprocess.check_output([sys.executable, str(ROOT / 'fetch_state.py'), args.wallet], text=True)
    state = json.loads(state_raw)
    print(json.dumps(state, indent=2), flush=True)
    cmd = [
        args.binary, '--benchmark', '--seconds', str(args.seconds), '--device', str(args.device),
        '--threads', str(args.threads), '--blocks', str(args.blocks), '--iters', str(args.iters),
        '--challenge', state['challenge'], '--target', state['target'],
    ]
    print('\n[no-submit] running:', ' '.join(cmd), flush=True)
    rc = subprocess.call(cmd)
    raise SystemExit(rc)

if __name__ == '__main__':
    main()
