# ADR-0009: Ports and adapters; a realistic mock bank now, AU's APIs later

- **Status:** Accepted
- **Date:** 2026-11-05 (mock core bank); 2026-11-06 (separate mock APIs)
- **Deciders:** project owner

## Context

We have no access to AU's systems yet: customer profile (CIF), core banking, cards, payments, loans, deposits, CRM. The demo still needs realistic customers and actions. Production will use AU's real APIs, whose shapes are unknown. The given workbook must stay untouched.

## Decision

- The conversation code talks to **ports** (interfaces): `Bank`, `ServiceDesk`, `LLM`, `STT`, `TTS`, `Embedder`, `Reranker`. Implementations are **adapters** chosen in config.
- Phase 3: an in-process **mock core bank** (Postgres `corebank` schema), seeded from the workbook plus 7 scenario customers.
- Phase 4:
  - one **`mockbank` service** (its own process and port) with a router and schema **per bank system**;
  - realistic behaviour: own IDs and status words, injected failures, idempotency keys, customer-scoped signed tokens, OpenAPI contracts;
  - the brain calls it over HTTP through an **`HttpBank`** adapter, in parallel.
- Going live means writing AU adapters and re-validating workflows, status mappings and tests against AU's real APIs. Changes to the conversation layer should be small, but we don't promise zero.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **One mock service, router + schema per system** | Real HTTP behaviour (timeouts, partial failures, separate IDs) without running 8 services | Schemas share one DB (fine for a mock; not real isolation) |
| One in-process class (Phase 3) | Simplest | Hides timeouts, partial failures and ID mismatches |
| Eight microservices | Most realistic | Heavy to run on a laptop; ops noise |
| Read the workbook directly | Zero work | Too little data; can't model actions or failures |

## Consequences

- **Easier:** demos with real-looking journeys; a clean swap to AU's systems; contract tests.
- **Harder:** keeping the mock plausible. If AU shares API specs early, the mock should be built to match them.
- **Revisit when:** AU provides its API catalogue or a sandbox.

## Links

`PHASE-4-PLAN.md` §3.3, §3.5; `brain/app/ports.py`, `brain/app/adapters/mock_corebank.py`; memory "dummy data unchanged".

## Implementation notes (6 Nov 2026)

- Built as **one** HTTP process with eight routers (cif, cbs, payments, cards, cc, lms, deposits, crm), run inside the brain on a free port (`MOCKBANK_PORT=8100` to browse `/docs`) or separately (`BANK_BASE_URL`). Separate Postgres schemas per system were **not** built: they add realism, not security.
- Brain -> bank auth: a service token plus a signed, short-lived customer token minted only after the keypad code. Writes carry an `Idempotency-Key`; a timeout on a write is "unconfirmed", never "failed". Fault injection per system. Measured: one customer read 110 ms p50 over HTTP, the same as the in-process mock.
- `BANK_PROVIDER=mock` (in-process) still works.
