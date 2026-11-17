# ADR-0019: Tiered testing with a replay cache

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

Groq's free tier allows 8k tokens/minute and 1,000 requests/day **per model**, and the owner's own testing uses the same budget. A full benchmark run (~200–300 requests) stalled on the daily limit. Rule: "nothing may get worse", so every change needs measuring.

## Decision

| Tier | What | LLM calls | When |
|---|---|---|---|
| T0 | Offline tests, fake LLM; routing, state, redaction, status mapping, contract tests | 0 | Every change |
| T1 | **Replay cache**: real responses cached on disk, keyed by a hash of the exact request (model, all messages incl. retrieved passages, schema, settings) | Only changed items | Every prompt change |
| T2 | ~40-item smoke set covering each failure class | ≤ 80 | Before demos, after prompt changes |
| T3 | Golden set + ~150 routing items + 15 multi-turn scripts | ~400 (estimate) | Manually, at most daily |

- **Checks are done by code wherever possible:** counts, order, account, status, language, action confirmed.
- **Results are reported per class:** fraud recall, false-fraud rate, multi-intent recall, scope accuracy, **false action execution (must be 0)**, missed verification (must be 0), variant accuracy, answer correctness.
- Every failure found in real calls becomes a regression test.
- The judge uses a different model from the agent, to spread the quota.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Tiers + replay cache** | Most runs cost nothing; still measured | Cache invalidation must be exact (solved by hashing the whole request) |
| Run the full benchmark each time | Simple | Blocked by daily limits |
| A 1,100-utterance suite | Statistical power | Far beyond the free quota for a hackathon |
| Paid Groq tier | Removes limits | Cost; postponed until caching is tried |

## Consequences

- **Easier:** frequent, cheap, honest measurement.
- **Harder:** keeping the smoke set representative.
- **Revisit when:** limits still block testing after caching (then a paid tier), or the production LLM changes (re-baseline).

## Links

`brain/evals/`, `brain/app/adapters/replay.py`, `PHASE-4-PLAN.md` §10.

## Implementation notes (6 Nov 2026)

- Built: `ReplayLLM` (`LLM_REPLAY_DIR`), the 40-item smoke set (`evals/smoke.yaml`, `python -m evals.smoke`), `evals/golden_run.py` (paced), the judge on `JUDGE_MODEL`, and `evals/stt_ab.py`. The first real smoke run found a provider rejection of an out-of-list enum value, which is now retried with a lenient schema.
