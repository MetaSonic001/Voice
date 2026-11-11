# ADR-0016: Bank actions: access levels per task, explicit "yes", read-back

- **Status:** Accepted
- **Date:** 2026-11-05; refined 2026-11-06
- **Deciders:** project owner ("the agent shouldn't end up doing everything; controlled actions go to the CX team")

## Context

The hackathon requires at least one controlled action with explicit confirmation. Some operations are high-risk (account takeover paths, money movement). A timeout doesn't mean a write failed.

## Decision

- **Access level per task** (in the registry, enforced in code):

  | Level | Examples |
  |---|---|
  | Open | Product information |
  | Verified | Balance, transactions |
  | Verified + explicit yes | Card block / unblock, channel controls |
  | **People only** | Closing an FD, payments and transfers, limits, address or mobile changes, replacements, fee reversals, refunds |

- **Flow:** plan → confirm in one sentence → "yes" (a deterministic yes/no reader in en/hi/Hinglish) → execute with an **idempotency key** → **re-read the result** → speak it with a reference number.
- An explicit, unambiguous request ("I lost my debit card, block it", one matching card) counts as the yes. Digits heard by speech recognition are read back first.
- Action status: awaiting confirmation → submitted → succeeded / failed / **unknown**. The agent never says "failed" or "done" without proof.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Access levels + explicit yes + read-back** | Safe; matches bank practice; auditable | One more turn for actions |
| Let the agent do everything after OTP | Fewer transfers | Account-takeover and fraud risk; against the owner's instruction |
| No actions (information only) | Safest | Fails the hackathon requirement |

## Consequences

- **Easier:** clear answers for security review; offline tests can prove "no action without a yes".
- **Harder:** more transfers to people for high-risk requests (intended).
- **Revisit when:** AU defines which actions are allowed by voice and which need step-up verification.

## Links

`brain/app/services/actions.py`, `confirm.py`, `journeys.py`, `PHASE-4-PLAN.md` §3.2–3.4.

## Implementation notes (6 Nov 2026)

- Deviation from the plan: a lost or stolen card is **not** blocked "straight away". CLAUDE.md requires an explicit yes for every card action, so there is one confirmation (lost = permanent, misplaced = temporary), then recent payments and a dispute offer.
- An explicit request for a person is itself the consent: the transfer starts with no "Is that okay?". Consent is asked only when the agent proposes a transfer.
- The registry enforces the four access levels in code; address / mobile changes, refunds, limits and closures go to people.
