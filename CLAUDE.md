# AU Small Finance Bank: inbound voice agent (hackathon → production POC)

An inbound AI voice agent for AU Small Finance Bank. It answers product questions from the bank's documents (RAG), verifies callers with a keypad code, reads their own bank data, does a few controlled card actions with confirmation, raises tickets, and hands over to human agents with context. It works in English, Hindi and Hinglish.

- `brain/`: Python FastAPI service (text and voice), Postgres via pgserver with pgvector, bge-m3 embedder and reranker on the GPU, Groq LLMs.
- `ui/`: Next.js + shadcn test console. Pages: `/` (console), `/kb`, `/calls`, `/desk`, `/fixes`, `/bench` (speed and quality per setup).
- **Production target (proposed, 13 Nov 2026):** callers → AU's Cisco → our app on AWS Mumbai (ECS), Sarvam for speech, gpt-oss-120b on Bedrock Mumbai. Details: `docs/deployment-options-and-costs.md`.

## Read before doing anything

1. `context/progress-tracker.md`: **where we are**: what's built, what's live, the current step, open questions, and how the owner's laptop differs from the company laptop. Always read it first. The full dated history (4–22 Nov 2026) is `context/progress-archive.md` (read only when you need the why; not updated).
   Then `context/pending-work.md`: **what is left**, for the local demo and for production on AWS.
2. `reference/PROJECT-GUIDE.md`: **the one detailed document**. How the whole system works (§1–24; §23 Phase 3, §24 Phase 4), what each phase built and what the calls showed (§25), going live on Cisco / AWS / Sarvam (§26), every Indian language (§27), lessons from other voice agents (§28), production research (§29), open questions (§30). §33 explains every code file (why it exists, what it does), §34 how a reply is made (templates, journeys, the LLM). §31 maps the sections of the merged documents (`PHASE-4-PLAN.md`, `ROADMAP.md`, the deep dive, the playbook) to their new place, so old references in ADRs and code comments still resolve.
3. `brain/README.md` and `ui/README.md`: how to run, configure and test each part.
4. `adr/`: **why** each major decision was made (Architecture Decision Records). Check them before changing something they cover. For a new decision that is expensive to reverse, add an ADR from `adr/template.md`. Never rewrite an accepted one; supersede it.
5. Only when needed:
   - `docs/deployment-options-and-costs.md` + `docs/deployment-costs-and-options.xlsx`: AWS design proposals and the cost estimate;
   - `deploy/README.md`: Docker images (`brain/Dockerfile`, `ui/Dockerfile`) and how to run them on AWS ECS;
   - `reference/aws-primer.md` (AWS from scratch) and `reference/briefing-security-state-scale.md` (long-form review answers);
   - `reference/LEARNING-GUIDE.md`: a beginner-to-expert course on the code (audio basics, asyncio and threads, startup, a live call byte by byte, one turn, the database, caches, concurrency, where every file/log/row is stored, failure handling, patterns, FAQ);
   - `Sample Questions/`: problem statement, sample questions, dummy data, KB.

**Removed on 6 Nov 2026** (old plans, replaced by the guide, the ADRs and the tracker; they are in git history): `mvp2-plan.md`, `phase-a-plan.md`, `platform-build-plan.md`, `gaps-and-next-steps.md`, `scenario-map.md`, `tech-architecture.md`, `PHASE-3-PLAN.md`.
**Merged into `reference/PROJECT-GUIDE.md` and removed on 14 Nov 2026:** `PHASE-4-PLAN.md`, `ROADMAP.md`, `reference/voice-agent-playbook.md` (in git history) and `reference/system-deep-dive-and-go-live.md` (never committed; its content is guide §26–27).

## Rules (from the project owner; follow them exactly)

**Git and the running demo**
- **Never commit or push to git** unless asked in that message.
- The owner often runs a demo: `python app.py` (brain on :8000) and `bun run dev` (UI on :3000).
  - Ask before restarting, stopping or replacing them.
  - The UI hot-reloads, so editing `ui/` changes a running demo immediately.
  - The brain does not reload; it needs a restart to pick up changes. Say so when it does.

**Scope and quality**
- **Only build what is needed. No over-engineering.**
  - If something isn't needed, don't build it; say what was skipped and why.
  - If stuck, stop and say so.
- **Nothing may get worse.** Accuracy and latency must improve or stay the same.
  - Measure before and after (offline tests plus the smoke set).
  - Report numbers honestly, including failures.
- **Research before building**, especially for UI changes and anything production-facing.

**Libraries**
- Use few, well-known libraries the bank's security team would approve. Prefer the standard library.
- Justify any new dependency before adding it.

**Security**
- The Groq API key lives only in `brain/.env` (a `SecretStr` setting). **Never print, log or echo it.**
- **OTP and PIN never go through speech-to-text, the LLM or the logs.** They are keypad-only and masked everywhere.
- Mask PII in logs and in what is sent to the LLM.
- Customer data comes only from the mock bank. The dummy workbook `Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx` is **never modified**, and its customer sheets are **never indexed** into the KB.

**Who decides what**
- The LLM only understands and phrases. **Code decides** routing, permissions, which bank calls to make and whether an action is allowed.
- Card actions need a verified caller and an explicit "yes".
- Closing an FD, payments, limits, address or mobile changes, and refunds go to people, not the agent.
- Answers must never invent figures. Every number comes from a KB source or bank data, and the figure check enforces it.

**Groq free tier**
- The limits are 8k tokens per minute and **1,000 requests per day per model**, and the owner's own testing shares that budget.
- Don't run the golden set or the full regression pack casually. Prefer offline tests and the small smoke set (`evals.smoke`, replay-cached); see PROJECT-GUIDE §20.

**Production-only (don't build now)**
- Sarvam STT/TTS, Cisco telephony, SSO, India-hosted LLM (Bedrock) and the AWS deployment are production items. Code switches already exist (`STT_PROVIDER`, `TTS_PROVIDER`, `LLM_PROVIDER`, `DATABASE_URL`, `FILE_STORE`); what is missing is in `context/pending-work.md` B2.

## Commands (Windows; run from `brain/` or `ui/`)

```bash
uv run python app.py                 # brain on http://127.0.0.1:8000 (/docs); ~1 min to load models
uv run pytest                        # offline tests (fake LLM/voice, throwaway Postgres), ~1.5 min, no Groq calls
uv run python -m evals.smoke         # real LLM, 24 items, replay-cached (a second run with nothing changed makes 0 requests)
uv run python -m evals.smoke --pack regression --keep  # whole conversations (33), real LLM, cached; --only id ... for a few
uv run python -m evals.golden_run    # rarely: 105-question golden set, paced, cached (about an hour on a cold cache)
uv run python -m app.cli clean-slate # delete all recorded calls (KB kept, mock bank re-seeded); asks for DELETE
bun run dev                          # UI on http://localhost:3000
bun run build                        # UI production build; must pass after UI changes
docker compose up -d db              # (repo root) Postgres 16 + pgvector in Docker; DATABASE_URL in brain/.env points at it
docker compose up -d --build         # (repo root) database + brain + UI in Docker (brain/README "Setup with Docker Desktop")
```

Demo callers (mock core bank): mobiles `9000000101`–`9000000107`. For example, Ravi is `…101` and Imran is `…105`. Their stories are in `brain/app/adapters/mock_corebank.py`.

## Keep the docs in sync

- After each meaningful change, update `context/progress-tracker.md`: what was done, test and benchmark numbers, what's live vs needs a restart, and the next step.
- If a change alters architecture, scope or rules, update `reference/PROJECT-GUIDE.md`, the relevant README, or this file in the same piece of work.
- New unresolved decisions go under **Open questions** in the tracker. Don't invent product behaviour; ask.

## Writing style for docs and replies

- Plain, simple English. Short sentences. No jargon without a one-line explanation.
- Tables for comparisons.
- Say what was skipped or couldn't be verified.
