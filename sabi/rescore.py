"""Re-score a validator's log and check every number it reported.

    python -m sabi.rescore logs/validator-*.jsonl

Scoring is deterministic (except the optional answer judge, off by default), so anyone with
the pinned corpus can recompute each logged score from the question's hidden target and the
miner's raw answer. Prints per-miner averages and any line where the recomputed score differs.
"""

import json
import sys
from collections import defaultdict

from .corpus import Corpus
from .protocol import Answer, Citation
from .scoring import score
from .validator import TIMEOUT_S


def main() -> None:
    corpus = Corpus.load()
    per_uid: dict[int, list[float]] = defaultdict(list)
    mismatches = 0
    lines = 0
    for path in sys.argv[1:]:
        for raw in open(path):
            row = json.loads(raw)
            lines += 1
            a = row["answer"]
            ans = Answer([Citation(**c) for c in a["citations"]], a.get("answer", "")) if a else None
            s = score(corpus, row["target"], ans, row["latency_s"], TIMEOUT_S)
            per_uid[row["uid"]].append(s.total)
            if abs(s.total - row["score"]["total"]) > 1e-6:
                mismatches += 1
                print(f"MISMATCH step {row['step']} uid {row['uid']}: logged {row['score']['total']}, recomputed {s.total}")
    print(f"{lines} scored answers re-checked, {mismatches} mismatches")
    for uid in sorted(per_uid):
        v = per_uid[uid]
        print(f"uid {uid}: mean {sum(v) / len(v):.3f} over {len(v)} queries")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
