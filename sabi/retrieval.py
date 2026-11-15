"""Reference retrieval for miners: BM25 over section title + text, and verbatim quote picking.

This is a baseline, not the ceiling. Miners compete by doing better (dense embeddings,
rerankers, query rewriting for Pidgin), and the subnet pays for that improvement.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .corpus import Corpus, Section, normalise
from .protocol import Answer, Citation

STOP = set(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or "
    "our so that the their them there they this to was we what when where which who why will "
    "with you your abeg wetin dey na una wey law nigeria nigerian section say says about any "
    "need know tell actually rule rules covers deals friend asked understand talk".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(normalise(text)) if t not in STOP and len(t) > 1]


class BM25:
    def __init__(self, corpus: Corpus, k1: float = 1.4, b: float = 0.7, title_boost: int = 3, titles_only: bool = False, head_words: int = 0):
        self.corpus = corpus
        self.k1, self.b = k1, b
        self.docs: list[Counter] = []
        for s in corpus.sections:
            body = " ".join(s.text.split()[:head_words]) if head_words else s.text
            toks = tokens(s.title) if titles_only else tokens(body) + tokens(s.title) * title_boost
            self.docs.append(Counter(toks))
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[Section]:
        q = tokens(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [self.corpus.sections[i] for _, i in scores[:k]]


def best_quote(section: Section, query: str, min_words: int = 8, max_words: int = 40) -> str:
    """The run of consecutive words from the section that best overlaps the query.

    Taken word-for-word from the section text, so it always passes the verbatim check.
    """
    words = section.text.split()
    if len(words) <= max_words:
        return " ".join(words)
    q = set(tokens(query))
    best, best_i = -1.0, 0
    for i in range(0, len(words) - min_words + 1, 4):
        window = words[i : i + max_words]
        hits = sum(1 for w in window if any(t in q for t in tokens(w)))
        if hits > best:
            best, best_i = hits, i
    return " ".join(words[best_i : best_i + max_words])


def answer(engine: BM25, query: str, k: int = 3) -> Answer:
    hits = engine.search(query, k)
    cites = [Citation(s.statute, s.number, best_quote(s, query)) for s in hits]
    summary = f"See {hits[0].statute_name}, section {hits[0].number} ({hits[0].title})." if hits else ""
    return Answer(citations=cites, answer=summary)
