# ADR-0021: No Redis or Kafka for now; when to add them

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

Today one process serves everything. Call state is in Postgres; live call sessions (`LIVE_SESSIONS`) are an in-memory dict; background jobs use a Postgres `jobs` table. Production will run many voice workers (each call stays on one worker for its whole duration) and many stateless brain copies.

## Decision

- **Now: no Redis, no Kafka.** Postgres is enough for one process.
- **First multi-server deployment: add Redis for short-lived shared data:**
  - per-turn call state;
  - the map of which call is on which voice worker (the desk's audio bridge needs it);
  - OTP codes and attempt counters (with expiry);
  - rate limits and the LLM fallback switch;
  - live desk updates (publish/subscribe);
  - short-term idempotency records.

  Postgres stays the permanent record.
- **Background jobs** (judge, recording upload, alerts, CRM sync) move to **Redis Streams** when the Postgres table isn't enough.
- **Kafka: only AU's own.** If the bank runs an event bus, publish call events to it. Never run our own Kafka.
- **No separate vector database** (see [0003](0003-postgres-pgvector-single-store.md)).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Postgres now, Redis at scale, bank Kafka if present** | Least to run today; a clear path | Migration work when scaling |
| Redis now | Ready for scale | An extra service with no benefit on one machine |
| Our own Kafka | High-volume events | Far beyond our event volume; heavy to operate and approve |

## Consequences

- **Easier:** a simple demo stack.
- **Harder:** `LIVE_SESSIONS` and polling-based desk updates must move to Redis before running more than one voice worker.
- **Revisit when:** deploying more than one voice or brain instance.
- **Also per process today** (found 11 Nov 2026), to fix at the same time:
  - the OTP signing secret (`services/auth.py`) and the bank token secret, both random when unset. These go to **Secrets Manager / KMS**, not Redis.
  - the rate limiter (`main.py`), which goes to Redis.

## Links

`PHASE-4-PLAN.md` §13.
