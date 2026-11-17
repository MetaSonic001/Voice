# ADR-0025: Read only the bank data a request needs (build with the real AU adapter)

- **Status:** Accepted (design). To be built together with the real AU bank adapter, not on the mock.
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

Today, for any question from a verified caller, the brain reads **everything** about that customer from the mock bank:
profile, accounts, transactions, charges, debit and credit cards, card transactions, loans with EMIs and rate changes, and
FDs - about 15 HTTP calls in 2 parallel rounds (`adapters/http_bank.py`). It starts this read while the router is still
working (prefetch), then code picks the part the answer needs. With the local mock this takes about 110 ms.

With AU's real systems this pattern is wrong:

- it handles far more customer data than the question needs (a balance question also reads loans and cards);
- real core-banking calls are slower, may be rate-limited or charged, so 15 calls per turn cost time and money;
- one system being down (loans) can break an unrelated answer (balance).

What is already right and stays: the LLM never chooses which bank call to make, and it never receives bank data - bank
answers are code templates (ADR 0024); authorisation is checked in code before any personal answer.

## Decision

When the real adapter is built, bank reads become **selective**:

1. **The router names the request; code chooses the reads.** A fixed table maps each personal topic / detail to the bank
   reads it needs (one or several). The model never picks an API.

   | Request | Reads |
   |---|---|
   | balance | accounts |
   | last N transactions / a payment | accounts + that account's transactions |
   | charges, minimum balance | accounts + charges (+ AMB) |
   | FD maturity, value, renewal | FDs |
   | EMI, outstanding, foreclosure | loans + EMI schedule (+ rate changes for "why did my EMI change") |
   | debit card status, controls | debit cards |
   | credit card due, limit, lounge | credit cards (+ card transactions when asked) |
   | unrecognised debit (fraud) | accounts + transactions + credit card transactions |
   | ATM cash not dispensed | that account's transactions + open complaints |

2. **Fetch after routing**, the needed reads in parallel. The current prefetch-everything is dropped for the real adapter
   (it only made sense because the mock is local and cheap).
3. **Partial failures are handled per read.** A failed loans read never affects a balance answer. When a request needs two
   reads and one fails, the agent says what it could and could not check - never a complete-sounding answer from half the
   data.
4. **Cache within the call only**, keyed by customer, endpoint and parameters, so two steps of one call reuse a read. No
   cross-call or cross-customer cache without a separate security and freshness design.
5. **Authorise at the read boundary.** Before each read, code checks the caller's verified identity, that the account /
   card / loan belongs to them, and what the operation requires. A router saying "personal" never authorises anything.
6. **Only the needed fields leave the adapter** (as today, nothing from the bank goes to the LLM).

Selective reads follow data-minimisation and purpose-limitation principles and prepare the system for applicable DPDP Act
obligations. The exact requirements and their commencement dates must be confirmed by the bank's legal and compliance
teams; this ADR is an architecture decision, not a legal determination.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Topic-to-reads table in code, fetched after routing (chosen, with the real adapter)** | Minimum data, fewer calls, isolated failures, still deterministic | Route-first adds the router's time before the first bank call (the reads themselves run in parallel) |
| Keep reading everything (today) | Simplest; prefetch hides latency on the mock | Too much data and too many calls for real systems |
| LLM tool calling (the model picks APIs) | Flexible | The model could pick the wrong API or customer; each hop adds 0.6-1 s; harder to audit |
| Build it now against the mock | Ready early | Effort spent before the real API catalogue is known (the bank's endpoints will differ); delays validating the agent itself |

## Consequences

- **Easier:** passing a bank security review; adding a system (a row in the table); explaining every read in the audit log.
- **Harder / costs:** the table must be kept complete (a missing row = a request that cannot be answered, caught by tests);
  a small latency cost from routing first.
- **Revisit when:** AU's API catalogue and sandbox are available (PHASE-4-PLAN §19 question 2).

## Links

`brain/app/adapters/http_bank.py` (today's full read), `services/auth.py` (prefetch, verification), `services/personal.py`
(topics and details), ADR 0024 (templates for bank data), `context/progress-tracker.md`.
