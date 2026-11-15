"""Wire format between validators and miners.

Validators POST a JSON :class:`Query` to ``QUERY_PATH`` on the miner's served axon address,
signed with ``btauth/1`` (``bittensor.http_auth``). Miners answer with a JSON :class:`Answer`.
Everything is plain JSON so miners can be written in any language.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

QUERY_PATH = "/sabi/v1/query"
MAX_CITATIONS = 3
PROTOCOL_VERSION = 1


@dataclass
class Query:
    question: str
    k: int = MAX_CITATIONS
    corpus_sha256: str = ""
    version: int = PROTOCOL_VERSION

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Query":
        d = json.loads(raw)
        return cls(
            question=str(d["question"])[:2000],
            k=max(1, min(int(d.get("k", MAX_CITATIONS)), MAX_CITATIONS)),
            corpus_sha256=str(d.get("corpus_sha256", "")),
            version=int(d.get("version", PROTOCOL_VERSION)),
        )


@dataclass
class Citation:
    statute: str
    section: int
    quote: str


@dataclass
class Answer:
    citations: list[Citation] = field(default_factory=list)
    answer: str = ""

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Answer":
        """Parses leniently: malformed citations are kept as unscorable rather than raising,
        so a sloppy miner scores zero instead of crashing the validator."""
        d = json.loads(raw)
        cites = []
        for c in (d.get("citations") or [])[: MAX_CITATIONS * 2]:
            try:
                cites.append(Citation(str(c["statute"]), int(c["section"]), str(c.get("quote", ""))))
            except (KeyError, TypeError, ValueError):
                cites.append(Citation("", -1, ""))
        return cls(citations=cites, answer=str(d.get("answer", ""))[:4000])
