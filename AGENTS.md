# AU Small Finance Bank: inbound voice agent

Instructions for any AI coding agent working in this repo (Codex / ChatGPT reads `AGENTS.md`; Claude Code reads it
through `CLAUDE.md`). Follow the owner's rules below exactly.

## What this is, and for whom

An AI voice agent that answers **AU Small Finance Bank's** inbound customer calls. It is a hackathon build (2 Nov 2026)
growing into a production proof of concept for the bank. The owner builds it; AU's infrastructure team hosts it.

- **What it does:**
  - answers product questions from the bank's documents (RAG);
  - verifies callers with a keypad code;
  - reads their own bank data (a mock bank for now);
  - does a few card actions, only after an explicit "yes";
  - raises tickets;
  - hands over to people with context.
- **Languages:** English, Hindi and Hinglish.
- **Now (25 Nov 2026):** getting ready for **UAT on AWS EKS** (Mumbai): one CPU node, no GPU, RDS PostgreSQL with
  pgvector, S3, Secrets Manager, CloudWatch, and Bedrock GPT-5.6 Luna. See ADR 0031 and `deploy/README.md`.
- **Later:** callers → AU's Cisco → this app, with Sarvam for speech. That needs the Cisco details from AU.

| Part | What |
|---|---|
| `brain/` | Python 3.12 FastAPI service: text and voice agent, RAG (bge-m3 embeddings, pgvector + Postgres full-text, RRF; reranker only with a GPU), mock bank, call audit, evals. All API routes under `/api/v1/...` |
| `ui/` | Next.js + shadcn test console, built as static files served by nginx: `/` console, `/kb`, `/calls`, `/desk`, `/fixes`, `/bench` |
| `deploy/` | `eks/` (Kubernetes files), `database/` (RDS SQL), `README.md` (UAT steps, IAM role, updates) |
| `compose.yaml` | Laptop: Postgres + pgvector, brain and UI in Docker (`compose.gpu.yaml` adds an NVIDIA GPU) |

## Read before doing anything

1. `context/progress-tracker.md`: where we are, what is live, the numbers, the next step, open questions. Then
   `context/pending-work.md`: what is left.
2. `reference/PROJECT-GUIDE.md`: the one detailed document.
   - How everything works: §1–24. Phases: §25. Going live: §26. Languages: §27. Research: §28–29. Open questions: §30.
   - Every code file: §33. How a reply is made: §34. Old document names: §31.
   - Read only the sections you need.
3. `brain/README.md`, `ui/README.md`: how to run, configure and test.
4. `adr/`: why each major decision was made. Check before changing what one covers. A new decision that is expensive
   to reverse gets a new ADR (`adr/template.md`). Never rewrite an accepted one: write a new ADR and mark the old
   one Superseded (keep it: it records why the earlier option was dropped).
5. Only when needed:
   - `context/architecture.md` (the short version and the invariants), `context/ui-context.md` (UI theme and tokens);
   - `reference/LEARNING-GUIDE.md` (the code explained from scratch);
   - `reference/aws-primer.md`, `reference/briefing-security-state-scale.md`;
   - `context/progress-archive.md` (dated history to 23 Nov, not updated);
   - `Sample Questions/` (problem statement, sample questions, dummy data, KB);
   - the cost estimate: the Google Sheet "AU voice agent - AWS and Sarvam cost estimate" (tab UAT).

## The two laptops

| | Owner's laptop | Company (office) laptop |
|---|---|---|
| LLM | Groq free tier (few tokens left: **don't run the smoke or golden sets here**) | Bedrock, `in.openai.gpt-5.6-luna` (API key in `brain/.env` as `AWS_BEARER_TOKEN_BEDROCK`) |
| GPU | NVIDIA RTX 3050 → reranker on (`RERANK=auto`) | none → reranker off |
| Database | Docker Postgres (pgvector) or the old private one in `brain/data/pg` | Docker Postgres (pgvector) |
| Python | 3.12 venv, `pip install -r requirements-dev.txt` | Anaconda's 3.14 (no other Python allowed), `pip install -e ".[dev]"`; proxy needs `USE_SYSTEM_CERTS=true` |

Don't compare speed numbers between the two. Real-LLM accuracy runs happen on the company laptop.

## Rules (from the owner)

**Git and the running demo**
- Never commit or push unless asked in that message.
- The owner often runs a demo: the brain on :8000, the UI on :3000. Ask before restarting, stopping or replacing them.
- The UI hot-reloads. The brain does not: say when a change needs a brain restart.

**Scope and quality**
- Only build what is needed. No over-engineering. If something isn't needed, don't build it, and say what was skipped and why. If stuck, stop and say so.
- Nothing may get worse: accuracy and latency must improve or stay the same. Measure before and after (offline tests, the smoke set on Bedrock). Report numbers honestly, including failures.
- Research before building, especially UI changes and anything production-facing.

**Libraries:** few, well-known ones the bank's security team would approve. Prefer the standard library. Justify any new dependency.

**Security**
- API keys (Groq, Bedrock, Sarvam) live only in `brain/.env` as `SecretStr` settings. Never print, log or echo them.
- OTP and PIN never go through speech-to-text, the LLM or the logs. They are keypad-only and masked everywhere.
- Mask PII in logs and in what is sent to the LLM.
- Customer data comes only from the mock bank. `Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx` is never modified, and its customer sheets are never indexed into the KB.

**Who decides what**
- The LLM only understands and phrases. Code decides routing, permissions, which bank calls to make and whether an action is allowed.
- Card actions need a verified caller and an explicit "yes".
- Closing an FD, payments, limits, address or mobile changes, and refunds go to people.
- Answers never invent figures. Every number comes from a KB source or bank data, and the figure check enforces it.

**Production-only (don't build now):** Cisco telephony, staff SSO, a real AU bank adapter. The code switches exist
(`STT_PROVIDER`, `TTS_PROVIDER`, `LLM_PROVIDER`, `DATABASE_URL`, `FILE_STORE`, `SEARCH_PROVIDER`). What is missing is in `context/pending-work.md`.

## Commands (Windows; run from `brain/` or `ui/` unless noted)

```
python app.py                          # brain on http://127.0.0.1:8000 (API /api/v1, docs /docs); ~1 min to load models
python -m pytest                       # offline tests (fake LLM/voice, throwaway Postgres), ~1.5 min, no LLM calls
python -m evals.smoke                  # real LLM, 24 items, replay-cached (company laptop / Bedrock)
python -m evals.smoke --pack regression --keep   # 33 whole conversations, real LLM, cached
python -m evals.golden_run             # rarely: 105 questions, paced, cached
python -m app.cli clean-slate          # delete all recorded calls (KB kept, mock bank re-seeded); asks for DELETE
python scripts/download_models.py      # the search models (--embed-only without a GPU)
bun run dev                            # UI on http://localhost:3000
bun run build                          # UI production build; must pass after UI changes
docker compose up -d db                # (repo root) Postgres 16 + pgvector; DATABASE_URL in brain/.env points at it
docker compose up -d --build           # (repo root) database + brain + UI (add -f compose.gpu.yaml with a GPU)
```

No uv: plain `python` and `pip` everywhere (the office laptop blocks uv). Demo callers (mock bank): mobiles `9000000101`–`9000000107`
(Ravi `…101`, Imran `…105`; stories in `brain/app/adapters/mock_corebank.py`).

## How to work

- One unit at a time, small and verifiable, with a test. Don't mix boundaries in one step (conversation core, mock bank, UI, evals).
- Before moving on:
  - it works end to end;
  - `pytest` passes, and `bun run build` passes if the UI changed;
  - no invariant in `context/architecture.md` is broken;
  - accuracy and latency are no worse, or are stated as not measured;
  - the tracker is updated.
- Every bug from a real call becomes a regression test: a unit test when code decides, a smoke item when the LLM decides.
- **Protected:**
  - `Sample Questions/` is never edited (corrected copies go under `brain/seed/`);
  - `ui/components/ui/*` comes from the shadcn CLI;
  - migrations only add, and old migration files are never edited.

## Code standards

- **Structure:**
  - plain code, small single-purpose modules;
  - services depend on ports (`app/ports.py`), never on an adapter; wiring only in `app/container.py`;
  - `services/` logic, `adapters/` implementations, `api/` thin routers, `voice/` calls, `mockbank/` the mock bank.
- **Python:**
  - type hints on public functions; plain data as dataclasses in `domain.py` (no I/O there);
  - blocking work through `asyncio.to_thread`;
  - validate at boundaries (FastAPI models; LLM JSON through the strict schema, then code checks).
- **Logs and privacy:**
  - log through `app.logs.log` (every line is redacted);
  - caller text is `redact()`ed before the LLM, traces, history and logs.
- **UI:**
  - server components by default; shadcn components; Tailwind tokens only (`context/ui-context.md`);
  - every page fits at 390, 768, 1024 and 1440 px with no horizontal page scroll.
- **API:**
  - validate first; enforce auth and ownership before any change;
  - consistent response shapes; destructive endpoints need an explicit confirmation.

## Keep the docs in sync

- After each meaningful change, update `context/progress-tracker.md`: what was done, test numbers, what needs a restart, the next step. Keep it short; move old entries to `progress-archive.md`.
- If a change alters architecture, scope or rules, update `reference/PROJECT-GUIDE.md`, the relevant README, or this file in the same piece of work.
- New unresolved decisions go under "Open questions" in the tracker. Don't invent product behaviour: ask.

## Writing style for docs and replies

Plain, simple English. Short sentences. No jargon without a one-line explanation. Tables for comparisons. Say what was
skipped or couldn't be verified.
