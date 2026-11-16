#!/usr/bin/env bash
# One-command demo on a local subtensor that already has the Sabi subnet and hotkeys registered
# (see README). Starts four miners of different quality, runs the validator, then prints what the
# chain paid each miner and re-scores the validator's log.
#
#   scripts/demo.sh <wallet> <netuid> [lan-ip|auto] [steps]
set -euo pipefail
WALLET=${1:?wallet name}; NETUID=${2:?netuid}; IP=${3:-auto}; STEPS=${4:-60}
# The chain rejects 127.0.0.1, so publish this machine's current non-loopback address.
[ "$IP" = auto ] && IP=$(hostname -I | tr ' ' '\n' | grep -vE '^(127\.|$)' | head -1)
echo "miners will publish $IP"
NET=ws://127.0.0.1:9944
PY=.venv/bin/python
mkdir -p logs

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

port=8091
for mode in honest weak random liar; do
  $PY -m sabi.miner --wallet "$WALLET" --hotkey "miner-$mode" --netuid "$NETUID" --network $NET \
      --port $port --external-ip "$IP" --mode $mode > "logs/demo-miner-$mode.log" 2>&1 &
  pids+=($!)
  port=$((port + 1))
done
echo "waiting for 4 miners..."
for _ in $(seq 1 60); do
  [ "$(grep -l listening logs/demo-miner-*.log 2>/dev/null | wc -l)" -ge 4 ] && break
  sleep 2
done

$PY -m sabi.validator --wallet "$WALLET" --hotkey owner --netuid "$NETUID" --network $NET \
    --steps "$STEPS" --interval 1 --weights-every 10

echo
echo "== what the chain paid =="
$PY - "$NETUID" <<'EOF'
import asyncio, sys, bittensor as bt
netuid = int(sys.argv[1])
async def main():
    async with bt.Subtensor("ws://127.0.0.1:9944") as c:
        print("validator weights:", {v: {u: round(x, 3) for u, x in row.items()} for v, row in (await c.weights.weights(netuid)).items()})
        for n in await c.neurons.all(netuid):
            print(f"uid {n.uid}  {n.hotkey[:10]}  incentive {n.raw.get('incentive')}  emission {n.raw.get('emission')}")
asyncio.run(main())
EOF

echo
echo "== anyone can re-check the validator =="
$PY -m sabi.rescore "$(ls -t logs/validator-*.jsonl | head -1)"
