"""The statute corpus every validator and miner loads, pinned by hash.

A corpus is a JSON list of sections: ``{id, statute, statuteShort, statuteName, section,
title, text, url, jurisdiction}``. ``id`` is ``"<statute>:<section>"``. Validators refuse to
run against a corpus whose SHA-256 differs from the pinned one, so every participant scores
and answers against exactly the same text.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
DEFAULT_CORPUS = CORPUS_DIR / "ng-statutes-v1.json"
# SHA-256 of corpus/ng-statutes-v1.json (1,460 entries, 1,459 unique sections, 8 statutes).
PINNED_SHA256 = "68c53bf6a074219b261dbc1916fb3c2db0bc951ec85596d2965dbc19ac7967c0"


@dataclass(frozen=True)
class Section:
    id: str
    statute: str
    statute_name: str
    number: int
    title: str
    text: str
    url: str

    @property
    def cite(self) -> str:
        return f"{self.statute} s.{self.number}"


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalise(text: str) -> str:
    """Lower-case, NFKC, strip punctuation, collapse whitespace.

    Used on both sides of the verbatim-quote check so that line breaks, curly quotes and
    stray punctuation from PDF extraction don't decide a score.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


class Corpus:
    def __init__(self, sections: list[Section], sha256: str):
        self.sections = sections
        self.sha256 = sha256
        self.by_id = {s.id: s for s in sections}
        self._norm = {s.id: normalise(s.text) for s in sections}

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CORPUS, *, pinned: str | None = PINNED_SHA256) -> "Corpus":
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if pinned is not None and digest != pinned:
            raise ValueError(f"corpus hash {digest} does not match the pinned {pinned}")
        seen: dict[str, Section] = {}
        for row in json.loads(raw):
            if row["id"] in seen:
                continue  # the source has one exact duplicate (child-rights:278)
            seen[row["id"]] = Section(
                id=row["id"],
                statute=row["statute"],
                statute_name=row["statuteName"],
                number=int(row["section"]),
                title=row["title"].strip(),
                text=row["text"],
                url=row.get("url", ""),
            )
        return cls(list(seen.values()), digest)

    def get(self, statute: str, number: int) -> Section | None:
        return self.by_id.get(f"{statute}:{number}")

    def quote_in(self, section_id: str, quote: str) -> bool:
        """True if ``quote`` appears verbatim (after normalisation) in the section."""
        body = self._norm.get(section_id)
        q = normalise(quote)
        return bool(body) and bool(q) and q in body
