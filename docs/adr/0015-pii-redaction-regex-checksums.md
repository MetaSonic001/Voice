# ADR-0015: PII redaction with rules and checksums, not an ML model

- **Status:** Accepted
- **Date:** 2026-11-04; extended 2026-11-06
- **Deciders:** project owner

## Context

Transcripts can contain card numbers, Aadhaar, PAN, phone numbers, emails, UPI IDs and account numbers. Text goes to an external LLM and into logs, traces, caches, eval data, the desk and the judge. Amounts and "ending 4521" must stay, because they're the facts of the conversation.

## Decision

One `redact()` function with three classes of data:
- **identifiers** (masked);
- **financial facts** (kept);
- **secrets** (OTP and PIN are never stored at all; [0010](0010-verification-keypad-otp.md)).

| Identifier | How it's detected |
|---|---|
| Card | Luhn check |
| Aadhaar | Verhoeff checksum |
| PAN, email, UPI | Format |
| Phone | Indian format |
| Account number | Context ("account number …") plus the customer's known accounts |

It applies at every exit point: LLM input, stored turns, logs, traces, the replay cache, eval data, handoff payloads, the judge. It's treated as **defence-in-depth, not a guarantee**.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Rules + checksums (stdlib)** | Microseconds; precise for Indian IDs; no dependency | Can miss free-form PII (names, addresses) |
| Presidio / spaCy NER | Catches names | A model per call (latency); a large dependency; weak on Indian IDs |
| LLM-based redaction | Flexible | Sends the PII to an LLM to find it; latency |

## Consequences

- **Easier:** fast, testable masking everywhere.
- **Harder:** names and addresses aren't masked; raw audio sent to the STT provider isn't covered.
- **Revisit when:** masking names becomes a requirement (offline NER on stored transcripts), or a cloud STT is chosen.

## Links

`brain/app/services/privacy.py`, `PHASE-4-PLAN.md` §9.

## Implementation notes (6 Nov 2026)

- Built: Luhn (card), Verhoeff (Aadhaar), phone, PAN, email, UPI id and "account number ..." context. Not built: matching a customer's known account numbers (the generic 9+ digit rule already masks real account numbers). UUIDs are left alone so logs stay correlatable.
