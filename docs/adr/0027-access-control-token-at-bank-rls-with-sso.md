# ADR-0027: Customer data is guarded at the bank's APIs; database row-level security comes with staff SSO

- **Status:** Accepted
- **Date:** 2026-11-11
- **Deciders:** project owner

## Context

Reviewers asked how row-level security (RLS) is done. Two kinds of data need "who may see which row":

1. **Customer bank data** (accounts, cards, loans, FDs). It lives in AU's systems; today the mock bank plays that role. One customer must never see another's.
2. **Our call records** (`sessions`, `traces`, `auth_events`, `recordings`, `call_reviews`, `handoffs`…). Staff must see only what their role needs, for example a desk agent sees their own department's handoffs.

Today:

- Every customer-data call carries a signed customer token (`app/tokens.py`), made only after the keypad code. Each bank system checks it and rejects another customer (`mockbank/core.py` `caller()` / `owns()`, ADR [0009](0009-ports-and-adapters-mock-bank.md)).
- Our Postgres has no RLS policies and one database user.
- The staff pages (`/calls`, `/desk`) have no login. SSO is a production item.

## Decision

- **Customer isolation is enforced where the data lives: at the bank's APIs, by the customer-scoped token.** The CIF comes from the verified session, never from the LLM. In production, AU's API gateway issues or checks these tokens, and any RLS inside AU's databases is AU's.
- **No RLS on the mock bank.** It would only demonstrate a control AU builds itself.
- **Postgres RLS on our call tables is added together with staff SSO, before go-live:**
  - roles `brain_runtime` (only the current session's rows), `qa_reviewer`, `desk_agent` (own department), `admin`;
  - `FORCE ROW LEVEL SECURITY`;
  - `SET LOCAL app.session_id` / `app.department` per transaction (pool-safe);
  - the app connects as a non-owner role.
- **Not now.** Without staff logins there is no user or role to filter on.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Token at the bank API now; DB RLS with SSO** | Protects the real risk (customer data) today; RLS when roles exist | Our call tables rely on code until SSO |
| RLS on our tables now | Earlier defence in depth | Nothing to key on without staff logins; rework when SSO arrives |
| A database or schema per department | Strong separation | More to run; RLS gives the same in one database |
| Trust code only, forever | Simplest | A query bug could expose rows; banks expect database-level control |

## Consequences

- **Easier:** customer data is safe even if brain code is wrong; the RLS work is small once SSO exists.
- **Harder:** until SSO, anyone who can reach the staff pages can read call records. This is acceptable only on a closed demo machine with dummy data.
- **Revisit when:** SSO is added, or the real AU gateway defines its token format (then narrow token scopes per request, with [0025](0025-selective-bank-reads.md)).

## Links

`brain/app/tokens.py`, `brain/app/adapters/http_bank.py`, `brain/app/mockbank/core.py`, `docs/briefing-security-state-scale.md` §1–2, [0010](0010-verification-keypad-otp.md).
