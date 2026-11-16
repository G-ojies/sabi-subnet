# Sabi

A Bittensor subnet that rewards **grounded legal answers**: given a plain-language question, return the exact statute section that answers it, with a quote that is provably in that section. Inventing law never pays.

Design and rationale: [PROPOSAL.md](PROPOSAL.md).

## How it works

- **Validator** (`sabi/validator.py`) picks a hidden section from a hash-pinned corpus of 1,459 Nigerian statute sections, writes a question that section answers (`sabi/questions.py`), sends it to every miner over hotkey-signed HTTP (`btauth/1`), scores the answers (`sabi/scoring.py`), and sets weights from a moving average. Every exchange is logged with its hidden target.
- **Miner** (`sabi/miner.py`) answers with up to 3 ranked citations, each with a verbatim quote. The reference miner is BM25 (`sabi/retrieval.py`); miners compete by doing better.
- **Scoring**: `0.55 × reciprocal rank + 0.30 × quote fidelity + 0.05 × latency (+ 0.10 optional answer judge)`, with two hard rules:
  - *Relevance gate*: if the hidden section isn't cited, the answer scores 0.
  - *Hallucination penalty*: a citation to a section that doesn't exist, or a quote that isn't in the cited section, zeroes the whole answer.
- **Re-scoring** (`python -m sabi.rescore logs/*.jsonl`): scoring is deterministic, so anyone can recompute every logged score and check the validator.

## Results on a local subtensor

Four miners of different quality, one validator, 150 questions, weights set every 100 blocks (local chain, `subtensor-localnet`, netuid 2, 15 Nov 2026):

| Miner | Mean score | On-chain incentive | Emission (α, rao) |
|---|---|---|---|
| `honest`: BM25 + verbatim quotes | 0.914 | 48,583 | 3,039,467,424 |
| `weak`: indexes only the first 30 words of each section, 1 citation | 0.240 | 16,951 | 1,060,555,453 |
| `random`: real quotes from random sections, no retrieval | 0.000 | 0 | 0 |
| `liar`: right section, invented quote | 0.000 | 0 | 0 |

`sabi.rescore` re-checked all 600 logged answers: 0 mismatches.

Two mechanism flaws found and fixed while building, both covered by tests:

1. **Title leakage.** Questions written from section titles let a miner that only indexes titles beat full-text retrieval (95% vs 88% top-1). The offline generator now builds questions from distinctive words in the section body that are not in its title (title-only top-1 fell to 0%), and the LLM generator paraphrases into everyday English or Pidgin and discards anything sharing 6 consecutive words with the statute.
2. **Paid for doing nothing.** Before the relevance gate, a miner returning random genuine quotes earned 0.39 of the maximum from the quote and latency terms. With the gate it earns 0.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install bittensor==11.3.2 pytest
.venv/bin/python -m pytest -q          # 12 tests: scoring, corpus, generator, incentive ordering

# local chain
podman run -d --name sabi-localnet -p 9944:9944 ghcr.io/opentensor/subtensor-localnet:main
# create a subnet and register hotkeys (scripts/localnet_register.py works around
# the published localnet images lagging the 11.3 SDK; not needed on testnet)

python -m sabi.miner --wallet W --hotkey miner1 --netuid 2 --network ws://127.0.0.1:9944 \
    --port 8091 --external-ip <LAN IP> --mode honest
python -m sabi.validator --wallet W --hotkey owner --netuid 2 --network ws://127.0.0.1:9944
python -m sabi.rescore logs/validator-*.jsonl

# or everything at once (4 miners + validator + on-chain payout + re-score):
scripts/demo.sh <wallet> <netuid>
```

Pitch and demo-video script: [docs/PITCH.md](docs/PITCH.md).

Set `GROQ_API_KEY` (or `SABI_LLM_API_KEY` + `SABI_LLM_BASE_URL`) on the validator to use LLM-paraphrased questions.

## Corpus

`corpus/ng-statutes-v1.json`, SHA-256 `68c53bf6…967c0`: Constitution of Nigeria 1999, Administration of Criminal Justice Act 2015, Police Act 2020, Child's Rights Act 2003, Labour Act, Nigeria Data Protection Act 2023, Cybercrimes Act 2015, Lagos Tenancy Law 2011. Parsed from public PDFs for [Wetin](https://wetin.vercel.app).

## Licence

MIT
