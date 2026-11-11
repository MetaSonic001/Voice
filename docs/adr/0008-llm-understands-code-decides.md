# ADR-0008: The LLM understands; code decides and acts

- **Status:** Accepted
- **Date:** 2026-11-05 (Phase 3); extended 2026-11-06 (Phase 4: requests list, task registry)
- **Deciders:** project owner

## Context

The agent reads bank data and can block cards. An LLM can be manipulated by prompt injection, can misread a request, and can pick the wrong tool or customer. Each extra LLM round trip also adds 0.6–1 s to a voice turn. Calls showed real misreadings: a balance request was treated as a complaint about a guessed transaction.

## Decision

The pipeline is **understanding (LLM) → task registry (code) → bank calls (code, in parallel) → validation (code) → reply (template or LLM phrasing, figure-checked)**.

- One understanding call returns structured output:
  - a **requests list** (up to 3; extras are queued, never dropped);
  - **scope** per request (general or personal, using references to earlier turns);
  - **risk flags**, separate from the request type;
  - the reply to any pending question.
- A **task registry** in code maps each task to:
  - its handler (KB, bank read, action, workflow, ticket, handoff, lead, verify);
  - its **access level**;
  - which bank operations to call.
- Fraud risk = keyword rules **or** LLM flags. Code makes sure a credible risk is never treated as a plain question.
- Prompt-injection screening and PII masking run in code before the LLM.
- **No LLM function-calling / agent loop.**

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **LLM classifies, code executes** | Permissions enforced outside the model; one LLM call; deterministic routing is testable offline | Each new task needs a registry entry |
| LLM tool-calling loop (agent) | Flexible | +0.6–1 s per hop; model may pick the wrong tool, arguments or customer; hard to audit |
| Keyword/intent rules only | Fast, deterministic | Brittle for Hindi/Hinglish and free speech |

## Consequences

- **Easier:** safety reviews (the "excessive agency" risk in OWASP's LLM Top 10 is addressed by design); offline tests for routing; adding real APIs touches only adapters ([0009](0009-ports-and-adapters-mock-bank.md)).
- **Harder:** the understanding schema must stay small, or latency grows (a measured +190 ms was fixed by making fields nullable).
- **Revisit when:** never for permissions. Open-ended tasks might justify a constrained planner later.

## Links

`PHASE-4-PLAN.md` §3.1–3.4, `brain/app/services/understanding.py`, `registry.py`, `guard.py`.

## Implementation notes (6 Nov 2026)

- Built: the understanding returns up to three requests; `services/registry.py` maps each request kind to a handler and an access level; fraud is made by code from a risk flag (LLM or regex), not chosen by the LLM; an enum value the provider rejects is retried once with a lenient schema.
- Added: injection is caught in code before any LLM call (`services/guard.py`).
