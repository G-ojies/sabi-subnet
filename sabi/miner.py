"""Sabi miner: answers signed legal queries over HTTP and publishes its address on chain.

    python -m sabi.miner --wallet sabi-test --hotkey miner1 --port 8091 --netuid 2 \\
        --network ws://127.0.0.1:9944 [--mode honest|weak|random|liar]

``--mode`` exists so a demo network can show the incentive mechanism working:

* ``honest``: the reference BM25 retriever with verbatim quotes.
* ``weak``: a lazy index (title + first 30 words of each section) and one citation.
* ``random``: real sections and real quotes, chosen at random (no retrieval).
* ``liar``: the honest top section, but with an invented quote, which the hallucination
  penalty should zero.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bittensor as bt

from .corpus import Corpus
from .protocol import QUERY_PATH, Answer, Citation, Query
from .retrieval import BM25, answer, best_quote

log = logging.getLogger("sabi.miner")


class Engine:
    def __init__(self, corpus: Corpus, mode: str, seed: int = 0):
        self.corpus, self.mode = corpus, mode
        self.bm25 = BM25(corpus)
        self.lazy = BM25(corpus, head_words=30) if mode == "weak" else None
        self.rng = random.Random(seed)

    def respond(self, q: Query) -> Answer:
        if self.mode == "honest":
            return answer(self.bm25, q.question, q.k)
        if self.mode == "weak":
            hits = self.lazy.search(q.question, 1) or [self.rng.choice(self.corpus.sections)]
            s = hits[0]
            return Answer([Citation(s.statute, s.number, best_quote(s, q.question))], "")
        if self.mode == "random":
            picks = self.rng.sample(self.corpus.sections, q.k)
            return Answer([Citation(s.statute, s.number, best_quote(s, "")) for s in picks], "")
        if self.mode == "liar":
            a = answer(self.bm25, q.question, q.k)
            if a.citations:
                a.citations[0].quote = "the police may hold any suspect for as long as they consider necessary without charge"
            return a
        raise ValueError(f"unknown mode {self.mode}")


def make_handler(engine: Engine, self_hotkey: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def do_POST(self):
            if self.path != QUERY_PATH:
                return self._send(404, b'{"error":"not found"}')
            raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 64_000))
            try:
                caller = bt.http_auth.verify(
                    dict(self.headers.items()), raw, method="POST", path=self.path, self_hotkey_ss58=self_hotkey
                )
            except bt.http_auth.AuthError as e:
                return self._send(401, f'{{"error":"{type(e).__name__}"}}'.encode())
            try:
                q = Query.from_bytes(raw)
            except Exception:
                return self._send(400, b'{"error":"bad query"}')
            t0 = time.perf_counter()
            body = engine.respond(q).to_bytes()
            log.info("answered %s in %.0f ms: %r", caller.hotkey_ss58[:8], 1000 * (time.perf_counter() - t0), q.question[:70])
            self._send(200, body)

        def _send(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


async def trusted_validators(client, netuid: int) -> dict[str, int]:
    """Hotkeys allowed to query us: every neuron on the subnet holding a validator permit."""
    neurons = await client.neurons.all(netuid)
    return {n.hotkey: bt.CRYPTO_SR25519 for n in neurons if n.validator_permit}


async def run(args) -> None:
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    hotkey = wallet.hotkey.ss58_address
    corpus = Corpus.load()
    engine = Engine(corpus, args.mode, seed=args.port)

    async with bt.Subtensor(args.network) as client:
        uid = await client.neurons.uid(hotkey, args.netuid)
        if uid is None:
            raise SystemExit(f"hotkey {hotkey} is not registered on netuid {args.netuid}")
        if not args.no_serve:
            res = await client.execute(bt.ServeAxon(netuid=args.netuid, ip=args.external_ip, port=args.port), wallet)
            log.info("served axon %s:%d on chain: %s", args.external_ip, args.port, getattr(res, "success", res))
        bt.http_auth.configure_verifier(hotkey, trusted_sender_schemes=await trusted_validators(client, args.netuid))

        server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(engine, hotkey))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("miner uid %d (%s mode) listening on :%d, corpus %s", uid, args.mode, args.port, corpus.sha256[:12])
        while True:  # refresh who may query us
            await asyncio.sleep(120)
            bt.http_auth.configure_verifier(hotkey, trusted_sender_schemes=await trusted_validators(client, args.netuid))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wallet", required=True)
    p.add_argument("--hotkey", required=True)
    p.add_argument("--netuid", type=int, required=True)
    p.add_argument("--network", default="test")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--external-ip", default="127.0.0.1")
    p.add_argument("--mode", default="honest", choices=["honest", "weak", "random", "liar"])
    p.add_argument("--no-serve", action="store_true", help="skip publishing the axon address (already served)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
