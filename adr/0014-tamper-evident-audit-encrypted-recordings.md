# ADR-0014: Tamper-evident audit chain and encrypted recordings

- **Status:** Accepted
- **Date:** 2026-11-04
- **Deciders:** project owner

## Context

The problem statement requires auditing intents, confirmations, system calls, outcomes and escalations. Banks must be able to show that call records weren't changed afterwards, and recordings contain personal data.

## Decision

- Each stored turn includes a **hash of the previous turn** (a hash chain per call). `verify_chain` detects any edited, removed or reordered turn.
- Each turn also records its grounding level, sources, timings, prompt version and models.
- **Call events** (disclosure, verification, card actions, tickets, handoffs, interruptions, errors) are stored next to the turns. Every bank operation becomes an audit event with masked identifiers.
- **Recordings** are Opus audio encrypted with **AES-GCM** before they're written; earlier calls stay playable from `/calls`.
- The audit record is separate from operational logs and keeps only the structured evidence.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Hash chain in Postgres + AES-GCM files** | Simple, standard crypto (stdlib + `cryptography`); verifiable | Not a full WORM store; key management is basic locally |
| Plain rows | Simplest | Silent edits undetectable |
| Blockchain / ledger DB | Strong guarantees | Heavy, unnecessary |
| Unencrypted recordings | Simple | Unacceptable for PII |

## Consequences

- **Easier:** answering "what did the agent say and why" for any call; tamper evidence for reviews.
- **Harder:** production needs KMS/HSM-held keys, write-once storage and a retention policy agreed with AU.
- **Revisit when:** moving to production storage.

## Links

`brain/app/services/audit.py`, `store.py`, `recordings.py`, `PROJECT-GUIDE.md` §13–14.
