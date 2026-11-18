#!/usr/bin/env bash
# Fresh local subtensor with the Sabi subnet ready for scripts/demo.sh.
#
#   scripts/localnet_setup.sh <wallet> [netuid]   # wallet needs hotkeys: owner validator miner-{honest,weak,random,liar}
#   (pass netuid to reuse a subnet already created on this chain)
#
# Starts a throwaway container (the chain database grows by GBs a day with fast blocks, so stop
# it when done: `podman stop sabi-localnet`), funds the wallet from the public //Alice dev key,
# creates the subnet, starts it, disables commit-reveal, and registers the hotkeys.
set -euo pipefail
WALLET=${1:?wallet name}; NETUID=${2:-}
NET=ws://127.0.0.1:9944
B=.venv/bin/btcli
PY=.venv/bin/python
ALICE_SEED=0xe5be9a5092b81bca64be81d212e7f2f9eba183bb7a90954f7b76361f6edb5c0a  # public dev key

podman container exists sabi-localnet || podman run -d --rm --name sabi-localnet -p 9944:9944 ghcr.io/opentensor/subtensor-localnet:main >/dev/null
echo "waiting for the chain..."
for _ in $(seq 1 60); do $B --network $NET subnets create-cost >/dev/null 2>&1 && break; sleep 2; done

[ -d ~/.bittensor/wallets/alice-localnet ] || $B wallet regen-coldkey --wallet alice-localnet --seed $ALICE_SEED --no-password >/dev/null
COLD=$($PY -c "import json,os;print(json.load(open(os.path.expanduser('~/.bittensor/wallets/$WALLET/coldkeypub.txt')))['ss58Address'])")
if [ -z "$NETUID" ]; then
  $B --network $NET wallet transfer --wallet alice-localnet --dest "$COLD" --amount 5000 --yes >/dev/null
  NETUID=$($B --network $NET subnets create --wallet "$WALLET" --wallet-hotkey owner --yes 2>&1 | awk '/netuid/{n=$2} END{print n}')
fi
echo "subnet netuid $NETUID"
$B --network $NET hparams start --netuid "$NETUID" --wallet "$WALLET" --yes >/dev/null 2>&1 || true
# Admin calls are refused inside a protected window around each epoch; with a 10-block tempo
# that is most of the time, so keep trying (it usually lands within 15 tries).
for i in $(seq 1 60); do
  if $B --network $NET hparams set --netuid "$NETUID" --name commit_reveal_weights_enabled --value 0 \
       --wallet "$WALLET" --yes 2>&1 | grep -q "error"; then sleep 3; else echo "commit-reveal off (try $i)"; break; fi
  [ "$i" = 60 ] && { echo "could not disable commit-reveal; rerun this script with netuid $NETUID"; exit 1; }
done
$PY scripts/localnet_register.py --wallet "$WALLET" --netuid "$NETUID" validator miner-honest miner-weak miner-random miner-liar
echo "ready: scripts/demo.sh $WALLET $NETUID"
