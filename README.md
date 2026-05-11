# hash256 CUDA miner prototype

Status: CUDA benchmark + no-submit + guarded submit coordinator.

## Protocol

- contract: `0xAC7b5d06fa1e77D08aea40d46cB7C5923A87A0cc` on Ethereum mainnet
- challenge = `getChallenge(wallet)` = `keccak256(abi.encode(chainId, contract, wallet, epoch))`
- hash = `keccak256(abi.encode(challenge, nonce))`
- valid iff `uint256(hash) < currentDifficulty`
- nonce = 24-byte prefix || 8-byte big-endian low counter

`currentDifficulty` is really a target: lower means harder.

## Build

```bash
cd /root/hash256-gpu-miner
make
```

## Selftest

```bash
./hash256-cuda --selftest
```

## Fetch live state

```bash
./fetch_state.py 0xYourWallet
```

## No-submit mining test

Config is read from project-local `.env` first. Create it from the template:

```bash
cd /root/hash256-gpu-miner
cp .env.example .env
nano .env   # or vim; set HASH256_WALLET / fees / CUDA params
```

RPC config is optional; if `HASH256_RPC` is absent, public Ethereum RPCs are used.

Then run:

```bash
.venv/bin/python mine_submit.py
```

Equivalent explicit one-off command:

```bash
.venv/bin/python mine_submit.py --wallet 0xYourWallet --round-seconds 35 --once
```

This finds and verifies a nonce but does not sign or broadcast anything unless `HASH256_SUBMIT=true` in `.env` or `--submit` is passed.

## Real submit mode

Use a fresh low-balance wallet. Do not paste private keys into chat/history.

```bash
cd /root/hash256-gpu-miner
cp .env.example .env
nano .env
# set HASH256_PRIVATE_KEY=0x...
# set HASH256_SUBMIT=true
# keep HASH256_RPC empty/missing to use public RPCs

.venv/bin/python mine_submit.py
```

Or override from CLI:

```bash
.venv/bin/python mine_submit.py --submit --once --priority-fee-gwei 3 --round-seconds 20
```

## Current observed RTX 5090 performance

Around `5.8 GH/s` with the initial unoptimized kernel:

```text
threads=128 blocks=5440 iters=256
```

At target `0x000000000ffffffff...`, expected work is about `2^36` hashes, so the rough mean time is about 12 seconds at 5.8 GH/s.

## Notes / risks

- Ethereum Keccak padding is used, not FIPS SHA3.
- The challenge is wallet-bound and epoch-bound; stale nonce is possible near epoch rotations.
- The contract caps mint count per block; transaction may revert despite a valid hash if it lands too late or block cap is reached.
- Public RPCs may rate-limit; use a paid/private RPC for real submit.
