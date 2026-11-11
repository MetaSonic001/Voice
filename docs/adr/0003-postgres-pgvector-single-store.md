# ADR-0003: One Postgres with pgvector for everything

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** project owner

## Context

We need to store documents and versions, chunks, embeddings, keyword indexes, sessions, call turns, the audit chain, tickets, handoffs and the mock bank. The demo runs on one laptop without Docker. Production must use something banks already run and approve.

## Decision

Use **PostgreSQL with the pgvector extension** for all of it:
- vector search via an HNSW index;
- keyword search via full-text `tsvector` + GIN;
- relational data for the rest.

Locally it runs privately through **pgserver** under `brain/data/pg`, with no install and no Docker. In production, set `DATABASE_URL` to a managed Postgres (RDS or Aurora, or the bank's own).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Postgres + pgvector** | One system to secure, back up and approve; transactions across KB, calls and audit; vectors and keywords in one query engine | Not tuned for billions of vectors (we have thousands to ~150k) |
| Separate vector DB (Pinecone, Milvus, Qdrant) | Built for huge vector sets | Another system to approve and run; data leaves Postgres; no joins |
| SQLite + a vector add-on | Simple | Weak concurrency; not what banks run |
| Docker Postgres locally | Same as production | Extra install step for the demo |

## Consequences

- **Easier:** one backup, one access-control model, joins (e.g. filter vectors by active document version).
- **Harder:** large-scale vector search needs Postgres tuning (memory, HNSW settings); see [0022](0022-rag-at-large-scale.md).
- **Revisit when:** vectors pass several million, or measured search latency can't be held under ~50 ms.

## Links

`brain/app/db.py`, `brain/app/migrations/`, `PROJECT-GUIDE.md` §12.
