# Sabi: a Bittensor subnet for grounded legal answers

*Subnet proposal, Bittensor Global Subnet Hackathon (checkpoint 1).*

## 1. Problem and use case

More than 200 million Nigerians are governed by statutes that almost none of them have read. When police stop someone at a checkpoint, a landlord locks them out, or an employer withholds wages, the law that protects them is real but out of reach: lawyers are expensive, and the statutes are long PDFs in formal English.

General-purpose chatbots make this worse, not better. Asked "Can police search my phone without a warrant in Nigeria?", they answer confidently and cite sections that don't exist or don't say what is claimed. In legal questions an invented citation is worse than no answer, because people act on it.

What is missing is not another chatbot. It is a **retrieval commodity**: given a plain-language question, return the exact section of the exact statute that answers it, with a quote that is provably in that section. Every legal-aid app, NGO hotline, and civic-tech product could sit on top of that.

Sabi ("to know", in Nigerian Pidgin) is a subnet that produces this commodity and rewards it only when it is verifiably correct.

## 2. The digital commodity

A **grounded legal answer**:

```json
{
  "question": "Police arrested my brother last night. Where are they supposed to take him?",
  "citations": [
    {"statute": "acja", "section": 14,
     "quote": "A suspect who is arrested, whether with or without a warrant, shall be taken immediately to a police station"}
  ],
  "answer": "Straight to a police station (or another place for receiving suspects), and he must be told promptly what he is accused of."
}
```

The value is in the citation and the quote, which can be checked mechanically. The answer text is a convenience layer on top.

## 3. Subnet architecture

```
              ┌──────────────── corpus (versioned, public) ───────────────┐
              │ 1,460 sections · 8 statutes · Constitution, ACJA 2015,     │
              │ Police Act 2020, Child's Rights Act, Labour Act, NDPA 2023,│
              │ Cybercrimes Act 2015, Lagos Tenancy Law (hash-pinned)      │
              └──────────────┬─────────────────────────────┬──────────────┘
                             │                             │
                    ┌────────▼────────┐  query (synapse)  ┌▼──────────────┐
                    │   Validator     │ ────────────────▶ │    Miners     │
                    │ samples hidden  │                   │ retrieve +    │
                    │ section, writes │ ◀──────────────── │ quote + answer│
                    │ question, scores│   ranked citations└───────────────┘
                    └────────┬────────┘
                             │ weights (moving average of scores)
                    ┌────────▼────────┐
                    │  Bittensor chain│ → emissions
                    └─────────────────┘
```

- **Corpus**: a public, versioned JSON file of statute sections (id, statute, section number, title, text), with its SHA-256 hash recorded in the subnet's code and repo. Validators and miners load the same version. The initial corpus already exists: 1,460 sections parsed from official PDFs.
- **Synapse**: `LegalQuery(question: str, k: int = 3)` → `LegalAnswer(citations: list[{statute, section, quote}], answer: str)`.

## 4. Miner responsibilities

For each query, a miner returns up to `k` ranked citations, each with a short verbatim quote from the cited section, and an optional plain-language answer.

How a miner does this is up to them, which is where competition and improvement happen: BM25, dense embeddings, rerankers, fine-tuned small models, query rewriting for Pidgin and informal English. The reference miner ships a BM25 + embedding hybrid so anyone can start.

## 5. Validator responsibilities and evaluation

Each step, the validator:

1. **Samples a hidden target.** It picks a section `s` from the corpus, weighted towards sections people actually ask about (rights, arrest, bail, tenancy, wages) and away from procedural boilerplate.
2. **Writes a question whose answer is in `s`**, without copying its wording. A small instruction-tuned LLM paraphrases the section into a lay question ("My landlord changed the locks while I was out, is that legal?"), plus Pidgin and informal variants. Questions that share long word sequences with `s` are discarded, so lexical copying is not enough. Template questions are the fallback when no LLM is available.
3. **Queries a sample of miners** with the same question and a timeout.
4. **Scores each response** (section 6). Every check except the optional answer judge is deterministic, so two honest validators give the same score.
5. **Sets weights** from an exponential moving average of scores.

Because the validator generates the question from a section it chose, **it knows the ground truth without any human labelling**. This is the core of the mechanism: the validator can produce unlimited fresh, labelled tasks from the corpus.

## 6. Scoring methodology

For each response, with target section `s` and returned citations `c_1..c_k`:

| Component | Weight | Rule |
|---|---|---|
| **Retrieval** | 0.55 | Reciprocal rank of `s` in the citations: 1 if first, 1/2 if second, 1/3 if third, 0 if absent. |
| **Quote fidelity** | 0.30 | For each citation, the quote must appear verbatim (after whitespace and punctuation normalisation) in the cited section and be 8 to 60 words long. Score = fraction of citations whose quote is genuine. |
| **Latency** | 0.05 | 1 under 2 s, falling linearly to 0 at the timeout. |
| **Answer** | 0.10 | Optional, off by default on testnet: an LLM judge checks that the answer is supported by the quoted text. |

**Relevance gate.** If the hidden section isn't among the miner's top `k` citations, the response scores 0. Without this gate, a miner returning random genuine quotes quickly would collect the quote and latency shares (about 39% of the maximum in our tests) without doing any retrieval. With it, the random miner's average drops to 0.

**Hallucination penalty.** Any citation to a section that doesn't exist, or with a quote that isn't in that section, zeroes the whole response. Fabrication is the failure mode Sabi exists to remove, so it must never pay.

**Why this measures real value.** The retrieval term rewards finding the law that answers the question. The quote term rewards proving it. Neither can be satisfied by fluent text alone.

## 7. Incentive and reward mechanism

- Scores feed an exponential moving average per miner (α = 0.1), so one lucky or unlucky query doesn't swing weights.
- Weights are the normalised moving averages, with the bottom quartile set to zero to concentrate emissions on useful miners.
- **Anti-gaming:**
  - *Memorising the corpus* is allowed and expected. Indexing the corpus well is the commodity.
  - *Copying other miners* fails because every miner gets the same fresh question at the same time, and the question was written moments earlier.
  - *Keyword stuffing and returning every section* fails because only the top `k` count and reciprocal rank decays.
  - *Fabricated quotes* zero the response.
  - *Validator collusion* is limited because scoring is deterministic and reproducible from the logged question, target and response. Anyone can re-score a validator's logs.

## 8. Expected users and ecosystem value

- **Legal-aid and civic-tech apps** (Wetin, NGO hotlines, journalism tools) call a validator-backed API instead of running their own retrieval stack.
- **Model builders** get a live benchmark for grounded retrieval in a low-resource legal domain with Pidgin queries, a gap in current benchmarks.
- **Bittensor** gets a subnet whose output has a clear real-world consumer and whose quality is measured objectively, the property that makes a subnet hard to game.

The mechanism is not specific to Nigeria. Any jurisdiction's statutes can be added as a corpus version, so the same subnet extends to Ghana, Kenya and beyond.

## 9. Roadmap

| Phase | Deliverable |
|---|---|
| Hackathon, week 1 | Corpus loader + hash pinning, synapse, scoring module with unit tests, reference BM25 miner. |
| Hackathon, week 2 | Validator loop with question generation, local subtensor end-to-end run. |
| Hackathon, week 3 | Testnet subnet: 3 validators, 10 miners of varied quality, score logs showing the better miners earn more. |
| Hackathon, week 4 | Re-scoring tool, docs, demo video, pitch. |
| After | Miner-proposed corpus additions (new statutes verified against an official source URL), more jurisdictions, public API. |

## 10. Prior work by the team

- **Wetin** (live at wetin.vercel.app): a know-your-rights guide grounded in these same Nigerian statutes, which verifies every quote against the source text before showing it. The corpus and the verbatim-quote check come from it.
- Solana programs, Tari Ootle templates and prediction-market tooling shipped in 2026, including on-chain mechanisms with deposits, deadlines and defaults.
