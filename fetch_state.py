#!/usr/bin/env python3
import json, sys, urllib.request, hashlib
ADDR='0xAC7b5d06fa1e77D08aea40d46cB7C5923A87A0cc'
RPCS=['https://ethereum.publicnode.com','https://eth.llamarpc.com','https://cloudflare-eth.com']
# Precomputed selectors; avoid pycryptodome dependency on the server.
SEL={
 'miningState()':'0xf06d67bb',
 'getChallenge(address)':'0xf37381ad',
}
def rpc_call(method, params):
    data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    last=None
    for url in RPCS:
        try:
            req=urllib.request.Request(url,data=data,headers={'content-type':'application/json','user-agent':'hash256-cuda-prototype'})
            with urllib.request.urlopen(req,timeout=20) as r:
                j=json.loads(r.read())
            if 'result' in j: return j['result']
            last=j
        except Exception as e:
            last=str(e)
    raise SystemExit(f'RPC failed: {last}')
def eth_call(data):
    return rpc_call('eth_call',[{'to':ADDR,'data':data},'latest'])
def words(hexstr):
    h=hexstr[2:]
    return [int(h[i:i+64],16) for i in range(0,len(h),64)]
def pad_addr(a):
    a=a.lower()
    if a.startswith('0x'): a=a[2:]
    if len(a)!=40: raise SystemExit('bad address')
    return '0'*24+a
if len(sys.argv)<2:
    print('usage: fetch_state.py 0xYourWallet')
    sys.exit(2)
wallet=sys.argv[1]
state=words(eth_call(SEL['miningState()']))
challenge=eth_call(SEL['getChallenge(address)']+pad_addr(wallet))
target='0x'+state[2].to_bytes(32,'big').hex()
print(json.dumps({
    'contract': ADDR,
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
}, indent=2))
