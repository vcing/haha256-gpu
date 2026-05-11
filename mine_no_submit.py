#!/usr/bin/env python3
"""Compatibility wrapper for no-submit CUDA mining.

This delegates to mine_submit.py without --submit, so it inherits the live
challenge/target polling and automatic retarget/restart behavior.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description='Run HASH256 CUDA miner without submitting tx; auto-refreshes challenge/target.')
    p.add_argument('wallet', help='0x wallet address used for address-bound challenge')
    p.add_argument('--seconds', type=int, default=60, help='Mining slice duration before normal refresh')
    p.add_argument('--state-poll-seconds', type=float, default=3.0, help='Poll live state while CUDA is running; 0 disables')
    p.add_argument('--device', type=int, default=0)
    p.add_argument('--threads', type=int, default=128)
    p.add_argument('--blocks', type=int, default=5440)
    p.add_argument('--iters', type=int, default=256)
    p.add_argument('--binary', default=str(ROOT / 'hash256-cuda'))
    p.add_argument('--max-rounds', type=int, default=0, help='0 = unlimited')
    p.add_argument('--once', action='store_true', help='Exit after first found nonce')
    args = p.parse_args()

    cmd = [
        sys.executable, str(ROOT / 'mine_submit.py'),
        '--wallet', args.wallet,
        '--round-seconds', str(args.seconds),
        '--state-poll-seconds', str(args.state_poll_seconds),
        '--device', str(args.device),
        '--threads', str(args.threads),
        '--blocks', str(args.blocks),
        '--iters', str(args.iters),
        '--binary', args.binary,
        '--max-rounds', str(args.max_rounds),
    ]
    if args.once:
        cmd.append('--once')
    print('[no-submit] running:', ' '.join(cmd), flush=True)
    raise SystemExit(subprocess.call(cmd))


if __name__ == '__main__':
    main()
