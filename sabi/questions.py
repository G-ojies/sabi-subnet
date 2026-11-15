"""Validator-side task generation: pick a hidden section, write a question it answers.

The validator knows the ground truth because it chose the section first. Two generators:

* ``TemplateGenerator`` (default, no network): turns the section's title into a lay
  question using a handful of English and Pidgin templates.
* ``LLMGenerator`` (optional): asks an OpenAI-compatible chat API (Groq's free tier works)
  to write the question a worried non-lawyer would ask, in English or Pidgin. Any question
  sharing a run of ``MAX_SHARED_WORDS`` words with the section text is discarded, so
  copying the statute's wording into a search box isn't enough; it falls back to templates.

Sections that nobody asks about (citation, interpretation, repeal, schedules) are skipped,
and the rest are weighted towards the statutes people actually need day to day.
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.request
from dataclasses import dataclass

from .corpus import Corpus, Section, normalise

SKIP_TITLES = re.compile(
    r"^(citation|short title|interpretation|definitions?|repeals?|savings?|commencement|"
    r"schedule|arrangement of sections|transitional|application of|extent of)",
    re.I,
)
# Relative chance of drawing a section from each statute.
STATUTE_WEIGHT = {
    "constitution": 1.0, "acja": 1.2, "police-act": 1.5, "child-rights": 0.8, "labour-act": 1.5,
    "ndpa": 1.0, "cybercrimes": 1.0, "tenancy-lagos": 2.0,
}
MAX_SHARED_WORDS = 6

TEMPLATES = [
    "What does Nigerian law say about {a}, {b} and {c}?",
    "Is there any law in Nigeria on {a} and {b}, especially {c}?",
    "I have a problem involving {a}, {b} and {c}. Which law covers it?",
    "My friend asked me about {a} and {b} when {c} is involved. What does the law say?",
    "Abeg, wetin law talk about {a}, {b} and {c}?",
]
@dataclass
class Task:
    target: Section
    question: str
    generator: str


def eligible(s: Section) -> bool:
    return len(s.text) >= 200 and not SKIP_TITLES.match(s.title) and len(s.title) > 8


def shares_run(question: str, text: str, n: int = MAX_SHARED_WORDS) -> bool:
    q = normalise(question).split()
    body = " " + normalise(text) + " "
    return any(" " + " ".join(q[i : i + n]) + " " in body for i in range(len(q) - n + 1))


class TemplateGenerator:
    """Offline generator. Builds the question from words that are distinctive to the section's
    body and absent from its title, so matching section titles is not enough: a miner has to
    search the text itself. Weaker than LLM paraphrase (the words are the statute's own), but
    needs no network and still rewards real retrieval."""

    name = "template"

    def __init__(self, corpus: Corpus, rng: random.Random):
        import math
        from collections import Counter

        from .retrieval import tokens

        self.rng = rng
        self.pool = [s for s in corpus.sections if eligible(s)]
        self.weights = [STATUTE_WEIGHT.get(s.statute, 1.0) for s in self.pool]
        self._tokens = tokens
        df: Counter = Counter()
        for sec in corpus.sections:
            df.update(set(tokens(sec.text)))
        n = len(corpus.sections)
        self.idf = {t: math.log(n / c) for t, c in df.items()}

    def pick(self) -> Section:
        return self.rng.choices(self.pool, weights=self.weights, k=1)[0]

    def keywords(self, s: Section, k: int = 3) -> list[str]:
        from collections import Counter

        title = set(self._tokens(s.title))
        tf = Counter(t for t in self._tokens(s.text) if t not in title and len(t) > 3 and not t.isdigit())
        ranked = sorted(tf, key=lambda t: tf[t] * self.idf.get(t, 0), reverse=True)[: k * 3]
        self.rng.shuffle(ranked)
        return ranked[:k]

    def question_for(self, s: Section) -> str:
        kw = self.keywords(s)
        while len(kw) < 3:
            kw.append(kw[-1] if kw else "rights")
        return self.rng.choice(TEMPLATES).format(a=kw[0], b=kw[1], c=kw[2])

    def next(self) -> Task:
        s = self.pick()
        return Task(s, self.question_for(s), self.name)


class LLMGenerator(TemplateGenerator):
    """Paraphrases with an OpenAI-compatible endpoint; falls back to templates on any failure."""

    name = "llm"
    PROMPT = (
        "You write realistic questions that ordinary Nigerians ask about their legal rights. "
        "Read the statute section below and write ONE short question (under 30 words) that a "
        "worried non-lawyer would ask, which this section answers. Describe their situation in "
        "everyday words. Do not quote the section, do not name the Act or section number. "
        "{style} Reply with the question only.\n\nSection ({title}):\n{text}"
    )

    def __init__(self, corpus: Corpus, rng: random.Random, *, api_key: str, base_url: str, model: str):
        super().__init__(corpus, rng)
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model

    @classmethod
    def from_env(cls, corpus: Corpus, rng: random.Random) -> "TemplateGenerator":
        key = os.environ.get("SABI_LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
        if not key:
            return TemplateGenerator(corpus, rng)
        return cls(
            corpus, rng, api_key=key,
            base_url=os.environ.get("SABI_LLM_BASE_URL", "https://api.groq.com/openai/v1"),
            model=os.environ.get("SABI_LLM_MODEL", "llama-3.1-8b-instant"),
        )

    def _ask(self, s: Section) -> str:
        style = self.rng.choice(["Write it in plain English.", "Write it in Nigerian Pidgin English."])
        body = json.dumps({
            "model": self.model,
            "temperature": 0.9,
            "max_tokens": 80,
            "messages": [{"role": "user", "content": self.PROMPT.format(style=style, title=s.title, text=s.text[:3000])}],
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "sabi-validator"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.load(r)["choices"][0]["message"]["content"]
        return out.strip().strip('"').splitlines()[0][:300]

    def next(self) -> Task:
        s = self.pick()
        try:
            q = self._ask(s)
            if len(q.split()) >= 4 and not shares_run(q, s.text):
                return Task(s, q, self.name)
        except Exception:
            pass
        return Task(s, self.question_for(s), "template")
