# ADR-0020: After-call review: code checks always, LLM judge selectively

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

The owner wants a quality rating for calls (how the agent did). Scoring every production call with an LLM costs one request per call. The research consensus: machine checks on all interactions, deeper review on a risk-weighted slice, with the judge calibrated against human labels.

## Decision

- **Code checks on every call** (free):
  - disclosure given;
  - verification before data;
  - unredacted PII;
  - action outcomes;
  - errors and latency;
  - repeated questions.
- **LLM judge**, run after the call (zero added latency), on a different model from the agent:
  - **`JUDGE_MODE=all`** in development (now): every call.
  - **`JUDGE_MODE=selective`** in production. Always judged:
    - failed code checks;
    - fraud and risk calls, actions, complaints and disputes, handoffs and callbacks;
    - the caller repeated themselves, asked for a human, got "I don't know" twice, or hung up mid-task;
    - slow or rate-limited calls.

    Plus **10% random** of clean calls, spread across languages.
- **Judge output:** per-dimension scores (task completion, correctness, conversation, compliance, handoff), evidence that points at real turn and source IDs (verified in code), `critical_failure`, `needs_human_review`, failure categories, recommended fix.
- **People** review every critical failure and every flagged call. Calibration tracks agreement and **critical-failure recall**.
- **UI:** a review panel plus a "needs review" filter only.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Code checks always + selective judge** | Catches most problems for free; judge where risk is | Clean calls only sampled |
| Judge every production call | Full coverage | Cost scales with volume; quota |
| Human QA sampling only | Trusted | Tiny coverage; slow |
| No review | Nothing to build | No way to know quality |

## Consequences

- **Easier:** a quality signal per call; the owner's labels improve the judge.
- **Harder:** keeping the rubric calibrated as the agent changes.
- **Revisit when:** production volume and cost are known (adjust the sample rate).

## Links

`PHASE-4-PLAN.md` §11.

## Implementation notes (6 Nov 2026)

- Built (`services/review.py`): every quote the judge gives is checked against the transcript and invented ones are dropped; a critical claim with no real evidence goes to a person. People's labels feed `/v1/reviews/calibration`. Reviews run in the background when a live call ends; chat sessions are reviewed on demand. The desk also gets a figure-checked AI summary (`services/summary.py`).
- Transcripts sent to the judge and summary models keep amounts and counterparty names (identifiers are masked). Use an India-hosted model in production.
