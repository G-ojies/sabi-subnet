import random

import pytest

from sabi.corpus import Corpus, normalise
from sabi.miner import Engine
from sabi.protocol import Answer, Citation, Query
from sabi.questions import TemplateGenerator, eligible, shares_run
from sabi.scoring import score

C = Corpus.load()
ACJA14 = C.get("acja", 14)
REAL = "A suspect who is arrested, whether with or without a warrant, shall be taken immediately to a police station"


def ans(*cites):
    return Answer([Citation(*c) for c in cites])


def test_corpus_is_pinned_and_deduplicated():
    assert len(C.sections) == 1459
    assert ACJA14.title.startswith("Arrested suspect")
    with pytest.raises(ValueError):
        Corpus.load(pinned="0" * 64)


def test_quote_check_ignores_pdf_noise_but_not_words():
    assert C.quote_in("acja:14", REAL)
    assert C.quote_in("acja:14", REAL.upper().replace(",", " ,\n"))
    assert not C.quote_in("acja:14", REAL.replace("immediately", "eventually"))
    assert normalise("Don't  stop—now.") == "don t stop now"


def test_right_section_first_with_real_quote_scores_full_marks():
    s = score(C, "acja:14", ans(("acja", 14, REAL)), latency_s=0.3, timeout_s=12)
    assert s.total == pytest.approx(1.0)


def test_rank_decays():
    other = C.get("acja", 15)
    q2 = " ".join(other.text.split()[:12])
    first = score(C, "acja:14", ans(("acja", 14, REAL), ("acja", 15, q2)), 0.3, 12)
    second = score(C, "acja:14", ans(("acja", 15, q2), ("acja", 14, REAL)), 0.3, 12)
    assert first.retrieval == 1.0 and second.retrieval == 0.5
    assert first.total > second.total > 0


def test_real_quotes_from_the_wrong_law_earn_nothing():
    other = C.get("labour-act", 1)
    q = " ".join(other.text.split()[:12])
    s = score(C, "acja:14", ans(("labour-act", 1, q)), 0.1, 12)
    assert s.total == 0 and not s.fabricated and s.reason == "target section not cited"


def test_fabricated_quote_zeroes_everything():
    s = score(C, "acja:14", ans(("acja", 14, "police may detain a suspect for as long as they like without charge")), 0.3, 12)
    assert s.total == 0 and s.fabricated


def test_nonexistent_section_zeroes_everything():
    s = score(C, "acja:14", ans(("acja", 14, REAL), ("acja", 9999, "")), 0.3, 12)
    assert s.total == 0 and s.fabricated


def test_quote_must_be_long_enough_to_count():
    s = score(C, "acja:14", ans(("acja", 14, "A suspect who is arrested")), 0.3, 12)
    assert s.retrieval == 1.0 and s.quote == 0.0 and not s.fabricated


def test_slow_answers_lose_only_the_latency_share():
    fast = score(C, "acja:14", ans(("acja", 14, REAL)), 0.5, 12)
    slow = score(C, "acja:14", ans(("acja", 14, REAL)), 12, 12)
    assert fast.total - slow.total == pytest.approx(0.05 / 0.90, abs=1e-6)


def test_empty_answer_scores_zero():
    assert score(C, "acja:14", None, 0.1, 12).total == 0
    assert score(C, "acja:14", Answer(), 0.1, 12).total == 0


def test_generator_skips_boilerplate_and_never_copies_six_words():
    gen = TemplateGenerator(C, random.Random(1))
    for _ in range(200):
        t = gen.next()
        assert eligible(t.target)
        assert t.question.strip()
    assert shares_run("taken immediately to a police station or other place", ACJA14.text)
    assert not shares_run("Where must police take someone they just arrested?", ACJA14.text)


def test_honest_miner_beats_weak_random_and_liar():
    """The incentive in miniature: over the same 150 tasks, honest retrieval earns most."""
    gen = TemplateGenerator(C, random.Random(7))
    tasks = [gen.next() for _ in range(150)]
    totals = {}
    for mode in ["honest", "weak", "random", "liar"]:
        eng = Engine(C, mode, seed=3)
        totals[mode] = sum(score(C, t.target.id, eng.respond(Query(t.question)), 0.2, 12).total for t in tasks) / len(tasks)
    assert totals["liar"] == 0
    assert totals["random"] < 0.05
    assert totals["honest"] > 2 * totals["weak"] > totals["random"]
    assert totals["honest"] > 0.6, totals
