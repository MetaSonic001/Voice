# Architecture Context

Full detail, including the Phase 4 design (§24–25): `reference/PROJECT-GUIDE.md`. This file is the short version.

## Stack

| Layer | Technology | Role |
|---|---|---|
| Brain | Python 3.12, FastAPI; every API route under `/api/v1` | Text and voice conversation service (`brain/`) |
| Database | PostgreSQL 16 + pgvector: Docker locally, RDS on AWS (pgserver on the owner's laptop as a fallback) | Calls, audit chain, KB chunks, mock bank |
| Retrieval | bge-m3 embedder; pgvector + Postgres full-text, merged by RRF; bge-reranker only with a GPU (`RERANK=auto`, ADR 0031) | Hybrid search; without a GPU 6 RRF passages go to the answer model |
| LLM | Bedrock GPT-5.6 Luna, India profile `in.openai.gpt-5.6-luna` (company laptop, UAT); Groq gpt-oss on the owner's laptop (ADR 0007, 0026) | Understands and phrases; never decides |
| Voice | Silero VAD, Whisper STT (Groq), Edge TTS; Sarvam in production | Streaming, barge-in; browser playback via a WebRTC loopback for echo cancellation (ADR 0012) |
| UI | Next.js, shadcn/ui, Tailwind 4; static files served by nginx | Test console (`ui/`) |
| Deploy | UAT: AWS EKS, 1 CPU node, 1 brain + 1 UI pod, internal ALB (`/api/*` → brain), S3, Secrets Manager, CloudWatch (ADR 0031, `deploy/`) | |

## Request path (per caller turn)

guard (code) -> understand (LLM, list of requests + risk flags) -> task registry (code: access level, handler) ->
tools (code, parallel bank calls) -> validate (code) -> respond (template or LLM phrasing, then figure check).

## System boundaries (`brain/app/`)

- `services/conversation.py`: the turn pipeline and event stream. Channel-agnostic.
- `services/understanding.py`, `registry.py`, `state.py`: what the caller wants, what code allows, per-call state (Phase 4).
- `services/personal.py`, `journeys.py`, `actions.py`: bank-data answers, workflows, card actions.
- `services/privacy.py`, `guard.py`: redaction and the injection / risk screens. Pure functions, no I/O.
- `services/auth.py`: registered mobile + keypad code. Verification is server-side state.
- `services/audit.py`, `recordings.py`, `desk.py`: hash-chained turns, encrypted recordings, handoff desk.
- `ports.py`: `Bank`, `ServiceDesk`, `LLM`, `STT`, `TTS`, ... Services depend on these only.
- `adapters/`: mock core bank, HTTP bank client, LLM clients, replay cache, parsers, storage.
- `mockbank/`: the mock bank systems as a separate HTTP API (own port, own service and customer tokens).
- `voice/`: call session, turn-taking, player, hold music.
- `evals/`: smoke and regression packs, golden set.

## Storage model

- Postgres: sessions (incl. per-call `state` jsonb), traces, call events, alerts, recordings metadata, tickets, handoffs,
  call reviews, KB documents / versions / chunks / embeddings, the mock bank.
- Files (local folder, or S3 with `FILE_STORE=s3`): encrypted call recordings (AES-GCM), uploaded KB files, and one call record JSON per call (`call_records/`).

## Auth and access model

- Caller identity comes only from the verified session, never from the LLM or from what the caller says.
- Every task in the registry has an access level: open, verified, verified + confirm, people only. Code enforces it.
- Bank APIs get a service token plus a short-lived, customer-scoped token issued after the keypad code.

## Invariants (never violate)

1. OTP and PIN never go through speech-to-text, the LLM, the history or the logs. A lone 6-digit chat message while a
   code is pending is stored as `[code]`.
2. Code decides routing, permissions, bank calls and whether an action is allowed. The LLM only understands and phrases.
3. No figure is spoken that is not in a KB source or bank data (figure check).
4. Card actions need a verified caller and an explicit yes, and "done" is said only after the state is read back.
5. A bank timeout never means "failed": re-check, then say what is confirmed and what is not.
6. The dummy workbook is never modified and its customer sheets are never indexed into the KB.
7. API keys (Groq, Bedrock, Sarvam) only in `brain/.env` (Secrets Manager on AWS); never printed or logged.
8. No request in a multi-request sentence is silently dropped.
