# ADR-0017: One agent for all departments; departments are queues for people

- **Status:** Accepted
- **Date:** 2026-11-05
- **Deciders:** project owner

## Context

Banks organise service by department (cards, payments, loans, fraud…). Most caller needs (balance, block a card, a rate) can be resolved without a person. When a person is needed, the right team must get the call **with context**, so the caller doesn't repeat themselves.

## Decision

- **One agent** handles every domain through the task registry. There are no per-department bots.
- **10 departments are transfer queues for human agents:** fraud, credit cards, debit cards, payments, loans, deposits, digital, sales, priority, general.
- **Code picks the queue:** task + risk + customer segment. The LLM never chooses it.
- **Transfer:**
  - consent is asked only when *we* propose it (an explicit request transfers directly);
  - hold music, with a hold message every 30 s;
  - a callback ticket on timeout or after hours;
  - a case record is written **before** the transfer.
- The **agent desk** shows:
  - a deterministic brief;
  - an AI summary, labelled as AI and figure-checked;
  - the full redacted conversation.

  Human agents can talk over the live audio bridge or by chat.
- ATM cash-not-dispensed is a dispute subtype. Grievance escalation levels (Nodal Officer → Principal Nodal Officer → RBI Ombudsman) are part of the complaint flow, not extra queues.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **One agent + human queues** | One brain to test and secure; queues match how contact centres work | The registry grows with tasks |
| One bot per department | Separation | Duplicated logic; transfers between bots; more to test |
| Plain "I'll connect you" | Trivial | No context, no routing, no human side |

## Consequences

- **Easier:** adding a domain is a registry entry; human agents get context.
- **Harder:** the queue table must match AU's real teams and hours (unconfirmed).
- **Revisit when:** AU confirms its departments, hours, NRI or business banking scope.

## Links

`brain/app/services/desk.py`, `journeys.py`, `ui/app/desk/`, `PROJECT-GUIDE.md` §23, `PHASE-4-PLAN.md` §12.
