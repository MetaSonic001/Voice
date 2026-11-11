# AU Small Finance Bank: inbound voice agent (hackathon → production POC)

An inbound AI voice agent for AU Small Finance Bank. It answers product questions from the bank's documents (RAG), verifies callers with a keypad code, reads their own bank data, does a few controlled card actions with confirmation, raises tickets, and hands over to human agents with context. It works in English, Hindi and Hinglish.

- `brain/`: Python FastAPI service (text and voice), Postgres via pgserver with pgvector, bge-m3 embedder and reranker on the GPU, Groq LLMs.
- `ui/`: Next.js + shadcn test console. Pages: `/` (console), `/kb`, `/calls`, `/desk`.

## Read before doing anything

1. `context/progress-tracker.md`: **where we are**: what's built, what's live, the current step, open questions. Always read it first.
2. `PHASE-4-PLAN.md`: the Phase 4 design (built 6 Nov 2026; the tracker lists where the build differs). `docs/adr/` records why each decision was made.
3. `PROJECT-GUIDE.md`: how the whole system works (architecture, data, voice, security, tests); §23 is Phase 3.
4. `brain/README.md` and `ui/README.md`: how to run, configure and test each part.
5. `docs/adr/`: **why** each major decision was made (Architecture Decision Records). Check them before changing something they cover. For a new decision that is expensive to reverse, add an ADR from `docs/adr/template.md`. Never rewrite an accepted one; supersede it.
6. Only when needed:
   - `ROADMAP.md`: production research;
   - `Sample Questions/`: problem statement, sample questions, dummy data, KB.

**Removed on 6 Nov 2026** (old plans, replaced by the guide, the ADRs and the tracker; they are in git history): `mvp2-plan.md`, `phase-a-plan.md`, `platform-build-plan.md`, `gaps-and-next-steps.md`, `scenario-map.md`, `tech-architecture.md`, `PHASE-3-PLAN.md`. Decisions live in `docs/adr/`.

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
- Don't run the full benchmark casually. Prefer offline tests and the small smoke set (`evals.smoke`, replay-cached); see PHASE-4-PLAN §10.

**Production-only (don't build now)**
- Sarvam STT/TTS, Cisco telephony, SSO, India-hosted LLM and Kubernetes are production items.

## Commands (Windows; run from `brain/` or `ui/`)

```bash
uv run python app.py                 # brain on http://127.0.0.1:8000 (/docs); ~1 min to load models
uv run pytest -m "not live"          # offline tests (fake LLM/voice, throwaway Postgres), ~1 min, no Groq calls
uv run python -m evals.smoke         # real LLM, 40 items, replay-cached (a second run with nothing changed makes 0 requests)
uv run python -m evals.benchmark     # real LLM: routing, decisions, journeys (uses a lot of Groq quota)
uv run python -m app.cli clean-slate # delete all recorded calls (KB kept, mock bank re-seeded); asks for DELETE
bun run dev                          # UI on http://localhost:3000
bun run build                        # UI production build; must pass after UI changes
```

Demo callers (mock core bank): mobiles `9000000101`–`9000000107`. For example, Ravi is `…101` and Imran is `…105`. Their stories are in `brain/app/adapters/mock_corebank.py`.

## Keep the docs in sync

- After each meaningful change, update `context/progress-tracker.md`: what was done, test and benchmark numbers, what's live vs needs a restart, and the next step.
- If a change alters architecture, scope or rules, update `PROJECT-GUIDE.md`, the relevant README, or this file in the same piece of work.
- New unresolved decisions go under **Open questions** in the tracker. Don't invent product behaviour; ask.

## Writing style for docs and replies

- Plain, simple English. Short sentences. No jargon without a one-line explanation.
- Tables for comparisons.
- Say what was skipped or couldn't be verified.
