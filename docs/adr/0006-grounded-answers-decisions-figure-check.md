# ADR-0006: Grounded answers: four answer decisions plus a figure check in code

- **Status:** Accepted
- **Date:** 2026-11-03 (figure check); 2026-11-05 (answer decisions)
- **Deciders:** project owner

## Context

A bank agent must never invent a fee, rate or rule. LLMs can produce plausible but unsupported numbers. Some questions can't be answered as asked: the account variant is missing ("minimum balance for a savings account?"), sources disagree, or the KB has nothing.

## Decision

- The answer model must choose one of four **decisions**:
  - **answer**;
  - **CLARIFY** (ask the one missing detail, optionally giving the range);
  - **CONFLICT** (sources disagree: say so and offer a specialist);
  - **NO_ANSWER**.
- A **figure check in code** runs on each sentence as it streams. Every number, amount, percentage and date must appear in the passages or bank data given to the model; if not, the sentence is replaced by a safe fallback.
- Below the rerank threshold, the agent refuses **without** calling the LLM.
- Personal (bank-data) answers are built from verified facts. Templates are used where speed matters; LLM phrasing is figure-checked the same way (PHASE-4-PLAN §4.2).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Decisions + figure check in code** | Fabricated numbers can't reach the caller; honest "I'm not sure" paths | Some correct paraphrased numbers get blocked (safe failure) |
| Prompt-only "don't hallucinate" | No code | Not enforceable |
| Structured per-figure policy database | Exact | Every figure maintained twice; heavy |
| LLM self-check (second pass) | Catches more than numbers | +1 LLM call per answer: latency and quota |

## Consequences

- **Easier:** safety and auditability. Each answer records its grounding level and sources.
- **Harder:** the figure check proves a number *exists* in the sources, not that it's the *right* one (e.g. the wrong variant). Variant and conflict checks were added in [0018](0018-document-level-kb-metadata.md).
- **Revisit when:** a production LLM with reliable citations makes span-level checking possible.

## Implementation notes (11 Nov 2026)

- **Outgoing-speech filter**, the same per-sentence gate as the figure check (`guard.unsafe_output`, called in `_check_sentence` and on the clarify question). It blocks a sentence that asks the caller to tell or share an OTP, PIN, CVV, password or card number, and a web address that isn't in the cited sources.
- Warnings ("never share your OTP", "kisi ko mat bataiye") and statements ("we'll send the OTP to your mobile") pass.
- A blocked sentence ends the answer with the usual refusal and hand-off (reason `unsafe_output`).
- **Why:** these are exactly what a fraudster's script says. A rule in code holds even if the answer model is jailbroken. Regex only: microseconds, no new library.
- Tests: `tests/test_security.py`.

## Links

`brain/app/services/conversation.py`, `PROJECT-GUIDE.md` §8, `PROJECT-GUIDE.md` §23.
