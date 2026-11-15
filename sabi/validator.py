"""Sabi validator: hides a section, asks every miner about it, scores, and sets weights.

    python -m sabi.validator --wallet sabi-test --hotkey owner --netuid 2 \\
        --network ws://127.0.0.1:9944 [--steps 0] [--weights-every 10]

Each step: pick a section and write a question it answers (``sabi.questions``), POST it to
every miner that has served an address (signed with ``btauth/1``), score each answer
(``sabi.scoring``), update a per-miner moving average, and every ``--weights-every`` steps
set weights from those averages. Every scored exchange is appended to ``logs/*.jsonl`` with
the question, the hidden target and the raw answer, so anyone can re-score it
(``python -m sabi.rescore``) and check the validator's numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import bittensor as bt
from bittensor._generated import calls

from .corpus import Corpus
from .protocol import QUERY_PATH, Answer, Query
from .questions import LLMGenerator
from .scoring import score

log = logging.getLogger("sabi.validator")
ALPHA = 0.1  # moving-average weight of each new score
TIMEOUT_S = 12.0


def axon_url(raw: dict) -> str | None:
    info = raw.get("axon_info") or {}
    ip, port = int(info.get("ip") or 0), int(info.get("port") or 0)
    if not ip or not port:
        return None
    addr = ipaddress.IPv4Address(ip) if int(info.get("ip_type") or 4) == 4 else ipaddress.IPv6Address(ip)
    host = f"[{addr}]" if addr.version == 6 else str(addr)
    return f"http://{host}:{port}{QUERY_PATH}"


def query_one(wallet, miner_hotkey: str, url: str, body: bytes) -> tuple[Answer | None, float, str]:
    headers = bt.http_auth.sign(wallet, method="POST", path=QUERY_PATH, body=body, receiver_ss58=miner_hotkey)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read(256_000)
        return Answer.from_bytes(raw), time.perf_counter() - t0, ""
    except urllib.error.HTTPError as e:
        return None, time.perf_counter() - t0, f"http {e.code}"
    except Exception as e:
        return None, time.perf_counter() - t0, type(e).__name__


def to_weights(ema: dict[int, float]) -> tuple[list[int], list[float]]:
    """Normalised averages, with the lowest-ranked quarter of miners set to zero."""
    if not ema:
        return [], []
    ranked = sorted(ema, key=lambda u: ema[u])
    kept = dict(ema)
    for u in ranked[: len(ranked) // 4]:
        kept[u] = 0.0
    total = sum(kept.values())
    if total <= 0:
        return [], []
    uids = sorted(kept)
    return uids, [kept[u] / total for u in uids]


async def set_weights(client, wallet, netuid: int, uids: list[int], weights: list[float]):
    """SDK ``SetWeights`` intent, falling back to the plain ``set_weights`` call when the
    intent's pre-checks read storage a local (older) runtime doesn't have yet."""
    try:
        return await client.execute(bt.SetWeights(netuid=netuid, uids=uids, weights=weights), wallet)
    except Exception as e:
        if "not found" not in str(e):
            raise
        top = max(weights)
        u16 = [round(w / top * 65535) for w in weights]
        pairs = [(u, w) for u, w in zip(uids, u16) if w > 0]
        call = calls.SubtensorModule.set_weights(
            netuid=netuid, dests=[u for u, _ in pairs], weights=[w for _, w in pairs], version_key=0
        )
        return await client.submit_call(call, wallet, signer="hotkey")


async def run(args) -> None:
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    me = wallet.hotkey.ss58_address
    corpus = Corpus.load()
    rng = random.Random(args.seed)
    gen = LLMGenerator.from_env(corpus, rng)
    logdir = Path(args.logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"validator-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    ema: dict[int, float] = {}
    pool = ThreadPoolExecutor(max_workers=32)
    log.info("validator %s, corpus %s, generator %s, log %s", me[:8], corpus.sha256[:12], gen.name, logfile)

    async with bt.Subtensor(args.network) as client:
        if await client.neurons.uid(me, args.netuid) is None:
            raise SystemExit(f"{me} is not registered on netuid {args.netuid}")
        step = 0
        last_set_block = -10**9
        try:
            rate_limit = int(await client.query(("SubtensorModule", "WeightsSetRateLimit"), [args.netuid]))
        except Exception:
            rate_limit = 100
        log.info("weights rate limit: %d blocks", rate_limit)
        while args.steps == 0 or step < args.steps:
            step += 1
            neurons = await client.neurons.all(args.netuid)
            miners = [(n.uid, n.hotkey, axon_url(n.raw)) for n in neurons if n.hotkey != me]
            miners = [m for m in miners if m[2]]
            if not miners:
                log.info("no miners have served an address yet")
                await asyncio.sleep(args.interval)
                continue

            task = gen.next()
            body = Query(task.question, corpus_sha256=corpus.sha256).to_bytes()
            loop = asyncio.get_running_loop()
            results = await asyncio.gather(*[
                loop.run_in_executor(pool, query_one, wallet, hk, url, body) for _, hk, url in miners
            ])

            line = []
            with logfile.open("a") as f:
                for (uid, hk, _), (ans, lat, err) in zip(miners, results):
                    s = score(corpus, task.target.id, ans, lat, TIMEOUT_S)
                    ema[uid] = (1 - ALPHA) * ema.get(uid, 0.0) + ALPHA * s.total
                    f.write(json.dumps({
                        "step": step, "time": time.time(), "uid": uid, "hotkey": hk,
                        "question": task.question, "generator": task.generator, "target": task.target.id,
                        "answer": asdict(ans) if ans else None, "latency_s": round(lat, 4), "error": err,
                        "score": asdict(s),
                    }) + "\n")
                    line.append(f"uid{uid}={s.total:.2f}" + ("!" if s.fabricated else ""))
            log.info("step %d [%s] %r -> %s", step, task.target.id, task.question[:60], " ".join(line))

            block = await client.block()
            if step >= args.weights_every and block - last_set_block > rate_limit:
                uids, weights = to_weights(ema)
                if uids:
                    res = await set_weights(client, wallet, args.netuid, uids, weights)
                    ok = getattr(res, "success", False)
                    if ok:
                        last_set_block = block
                    log.info("block %d set weights %s: %s", block, {u: round(w, 3) for u, w in zip(uids, weights)},
                             "ok" if ok else str(getattr(res, "message", res))[:120])
            await asyncio.sleep(args.interval)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wallet", required=True)
    p.add_argument("--hotkey", required=True)
    p.add_argument("--netuid", type=int, required=True)
    p.add_argument("--network", default="test")
    p.add_argument("--steps", type=int, default=0, help="0 = run forever")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between steps")
    p.add_argument("--weights-every", type=int, default=10, help="steps to warm up before the first weights")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--logdir", default="logs")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
