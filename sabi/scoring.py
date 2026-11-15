"""Scoring: how much a miner's answer is worth, given the section the validator hid.

    score = 0.55 * retrieval + 0.30 * quote_fidelity + 0.05 * latency + 0.10 * answer

* retrieval: reciprocal rank of the target section among the first ``k`` citations.
* quote_fidelity: share of citations whose quote is 8-60 words and appears verbatim in the
  section it cites.
* latency: 1 under ``FAST_S``, falling linearly to 0 at the timeout.
* answer: optional judge score in [0, 1]; ``None`` (the default) re-weights the other three.

Relevance gate: if the hidden section is not among the first ``k`` citations, the response
scores 0. Real quotes from the wrong law, returned quickly, are worth nothing to the person
asking, so they must earn nothing (otherwise a miner returning random genuine quotes would
collect the quote and latency shares without doing any retrieval).

Hallucination penalty: if any citation names a section that doesn't exist, or quotes text
that isn't in the cited section, the whole response scores 0. Inventing law must never pay.

Every term except the optional judge is deterministic, so anyone can re-score a validator's
logged (question, target, response) triples and get the same number.
"""

from __future__ import annotations

from dataclasses import dataclass

from .corpus import Corpus, normalise
from .protocol import Answer

W_RETRIEVAL, W_QUOTE, W_LATENCY, W_ANSWER = 0.55, 0.30, 0.05, 0.10
MIN_QUOTE_WORDS, MAX_QUOTE_WORDS = 8, 60
FAST_S = 2.0


@dataclass
class Score:
    total: float
    retrieval: float
    quote: float
    latency: float
    fabricated: bool
    reason: str = ""


def _quote_ok(corpus: Corpus, section_id: str, quote: str) -> bool:
    n = len(normalise(quote).split())
    return MIN_QUOTE_WORDS <= n <= MAX_QUOTE_WORDS and corpus.quote_in(section_id, quote)


def score(
    corpus: Corpus,
    target_id: str,
    answer: Answer | None,
    latency_s: float,
    timeout_s: float,
    k: int = 3,
    answer_judge: float | None = None,
) -> Score:
    if answer is None or not answer.citations:
        return Score(0.0, 0.0, 0.0, 0.0, False, "no answer")

    cites = answer.citations[:k]
    # Hallucination check runs over every citation returned, not only the first k.
    for c in answer.citations:
        sid = f"{c.statute}:{c.section}"
        if sid not in corpus.by_id:
            return Score(0.0, 0.0, 0.0, 0.0, True, f"cited {sid}, which does not exist")
        if c.quote and not corpus.quote_in(sid, c.quote):
            return Score(0.0, 0.0, 0.0, 0.0, True, f"quote not found in {sid}")

    retrieval = 0.0
    for rank, c in enumerate(cites, start=1):
        if f"{c.statute}:{c.section}" == target_id:
            retrieval = 1.0 / rank
            break

    if retrieval == 0.0:
        return Score(0.0, 0.0, 0.0, 0.0, False, "target section not cited")

    quote = sum(_quote_ok(corpus, f"{c.statute}:{c.section}", c.quote) for c in cites) / len(cites)

    if latency_s <= FAST_S:
        lat = 1.0
    elif latency_s >= timeout_s:
        lat = 0.0
    else:
        lat = 1.0 - (latency_s - FAST_S) / (timeout_s - FAST_S)

    if answer_judge is None:
        scale = 1.0 / (W_RETRIEVAL + W_QUOTE + W_LATENCY)
        total = scale * (W_RETRIEVAL * retrieval + W_QUOTE * quote + W_LATENCY * lat)
    else:
        total = W_RETRIEVAL * retrieval + W_QUOTE * quote + W_LATENCY * lat + W_ANSWER * answer_judge

    return Score(round(total, 6), retrieval, quote, lat, False)
