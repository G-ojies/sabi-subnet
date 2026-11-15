"""Register hotkeys on a LOCAL subtensor whose runtime predates the SDK.

bittensor 11.3 reads ``SubtensorModule.CollateralLockShare`` before ``burned_register``. Live
testnet has it; the published ``subtensor-localnet`` images do not yet, so the SDK's
pre-check fails before anything is sent. On such a chain there is no collateral at all, so
this shim reads the missing value as 0 and then registers through the SDK's own
``BurnedRegister`` intent. Use it only against a local chain.

    python scripts/localnet_register.py --wallet sabi-test --netuid 2 validator miner-honest ...
"""

import argparse
import asyncio

import bittensor as bt
from bittensor.balance import Balance
from bittensor.intents import registration
from bittensor._generated import calls


_original = registration.neuron_registration_split


async def _split_or_no_collateral(substrate, netuid):
    try:
        return await _original(substrate, netuid)
    except Exception as e:  # storage function not found on an older local runtime
        if "CollateralLockShare" not in str(e):
            raise
        cost = await substrate.query(*registration.st.SubtensorModule.Burn, [netuid])
        return Balance.from_rao(int(cost or 0)), Balance.from_rao(0)


registration.neuron_registration_split = _split_or_no_collateral


async def main(args):
    async with bt.Subtensor(args.network) as client:
        for hk in args.hotkeys:
            wallet = bt.Wallet(name=args.wallet, hotkey=hk)
            uid = await client.neurons.uid(wallet.hotkey.ss58_address, args.netuid)
            if uid is not None:
                print(f"{hk}: already uid {uid}")
                continue
            call = calls.SubtensorModule.burned_register(netuid=args.netuid, hotkey=wallet.hotkey.ss58_address)
            res = await client.submit_call(call, wallet)
            uid = await client.neurons.uid(wallet.hotkey.ss58_address, args.netuid)
            print(f"{hk}: {'uid ' + str(uid) if uid is not None else 'FAILED ' + str(res)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--wallet", required=True)
    p.add_argument("--netuid", type=int, required=True)
    p.add_argument("--network", default="ws://127.0.0.1:9944")
    p.add_argument("hotkeys", nargs="+")
    asyncio.run(main(p.parse_args()))
