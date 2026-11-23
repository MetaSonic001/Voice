# Progress tracker

**Where we are now.** Read this first, then `pending-work.md` (what is left). Update it after every meaningful change:
what was done, the numbers, what is live and what needs a restart, and the next step.

The full dated log from 4 to 22 Nov 2026 (about 1,500 lines: every change, its reason, its numbers) is kept word for
word in [`progress-archive.md`](progress-archive.md). Look there for the "why" behind something; it is not updated any more.
The story of each phase is also in `reference/PROJECT-GUIDE.md` §25. The old issue table A–P is in the archive
("Issue tracker").

## Where we are (22 Nov 2026)

- **Built and working:** text and voice agent in English, Hindi and Hinglish; RAG over the bank's documents with a figure
  check; keypad verification (OTP never through speech-to-text, the LLM or logs); bank-data answers from the mock bank;
  card actions with an explicit yes; complaints; fraud first steps; several questions in one sentence (the call agenda);
  hand-over to the `/desk` page; post-call review and judge; tamper-evident call audit with encrypted recordings; test
  console (`/`, `/kb`, `/calls`, `/desk`, `/fixes`).
- **Phase:** Phase 4 (the "good first-line representative" work) is built. Since 12 Nov the work is mostly production
  planning (AWS Mumbai, Cisco, Sarvam, Bedrock) and making the demo run on the company laptop.
- **Next steps:** "Next" at the end of the latest entry below, and `pending-work.md`.

## The two laptops (read this if you are Claude on the company laptop)

Code is written on the owner's own laptop and **run for real on the company laptop**. Don't compare numbers between them.

| | Owner's laptop (writing, small checks) | Company laptop (real runs and demos) |
|---|---|---|
| LLM | Groq, gpt-oss-120b (`LLM_PROVIDER=openai`) | **Bedrock, GPT-5.6 Luna, `in.openai.gpt-5.6-luna`** (India profile; `LLM_PROVIDER=bedrock`; API key in `.env` as `AWS_BEARER_TOKEN_BEDROCK`) |
| Speech | Groq Whisper / Edge, or Sarvam | Sarvam (Edge / Whisper only as the fallback) |
| Vector search | pgvector (pgserver's Postgres) | **Chroma** (`VECTOR_STORE=chroma`, `uv sync --extra chroma`): Windows Application Control blocks pgvector's DLL. Postgres 16 (pgAdmin) still holds all data and every vector; Chroma is only the index |
| Bank | `MOCK_BANK=true` | `MOCK_BANK=true` |
| Python | 3.12 with uv (`uv sync`) | **Anaconda's Python 3.14 in a conda environment, packages with `pip install`** (since about 21 Nov). `pyproject.toml` says `>=3.12,<3.13` and the Docker image is 3.12, so the company laptop runs a newer Python than AWS will |
| Laptop limits | none known | `.bin` downloads blocked (models are `.safetensors` only, `scripts/download_models.py`). **Solved (22 Nov, owner):** the DLL blocks stopped after moving to Anaconda (they happened with a separately installed Python 3.12 + uv); the office proxy (Zscaler) works with `USE_SYSTEM_CERTS=true` (`truststore` reads the Windows certificate store) |

Other settings that exist for this (all in `brain/.env.example`): `VECTOR_STORE=numpy` (in-memory exact search; a fallback
if Chroma's DLL is blocked again), `SEARCH_RUNTIME=onnx` (CPU search models; the ONNX model files were deleted from the
owner's laptop on 22 Nov, re-download with `download_models.py --onnx`), `UNDERSTAND_REASONING_EFFORT` / `ANSWER_REASONING_EFFORT` (23 Nov; compare levels on `/bench`).

## History in brief

| When | What |
|---|---|
| 2–3 Nov | Hackathon build: RAG, figure check, voice engine, test console; golden set 72.4% pass |
| 4–5 Nov | Phase 3: mock core bank, card actions, complaints, fraud journey, hand-over desk; first call review (issues A–P) |
| 6–7 Nov | Phase 4: security first (OTP, redaction), understanding v2 (several requests), call state, mock bank as separate APIs, workflows, dialogue layer, KB checks, post-call judge, `/fixes` |
| 9–11 Nov | Briefing, outgoing-speech filter, latency metric, everyday Hindi, deep dive and go-live plan |
| 12–15 Nov | AWS deployment design and cost estimate (documents), echo fix in the browser (ADR 0012), one project guide |
| 16 Nov | GPU search server, live-call limit, recordings via the file store (S3-ready), Sarvam speech with a fallback |
| 17 Nov | Four Sarvam test calls reviewed; spoken-form fixes; PM reviews; regression pack run; smoke 19 / 24 |
| 18–19 Nov | Tests and evals trimmed; the call agenda (several questions in one sentence) |
| 20 Nov | Company-laptop support: `MOCK_BANK`, Bedrock API key, `.safetensors` models, ONNX option, Chroma and numpy vector stores, `USE_SYSTEM_CERTS`; LLM output caps removed; mute button; word-count limits replaced by `says.new_request` |
| 21 Nov | Storage analysis: Postgres vs S3 for the call record (not decided, nothing built) |
| 22 Nov | Edge-era number speech removed; Bedrock reasoning effort; the opener replaces the timed filler; this file shortened |
| 23 Nov | Fixes from the 20–21 Nov calls: 20 Nov word-count change reverted (except the account pick), agenda "which account?" status, problem-report check, copy check, language in code, amounts, team from the product, card answers, mock ATM record, Hindi grammar |

## Live state (check before testing)

- **Brain:** code changed on 20, 22 and 23 Nov. A brain started before that needs a restart (`uv run python app.py` in `brain/`).
- **UI:** hot-reloads; no UI change since 20 Nov (mute button).
- **Offline tests:** last full run **418 pass** (20 Nov, before the word-count change). Not run since, by the owner's
  choice (no tests on the owner's laptop): the 20 Nov word-count change and everything on 22 Nov are untested.
  Run `uv run pytest` on the company laptop.
- **Real-LLM sets:** smoke set last run 17 Nov (19 / 24, Groq). Never run on Bedrock Luna. The prompts changed since,
  so the replay cache misses.

## Latest numbers

All on the owner's laptop with Groq; none on Bedrock Luna yet. Every measured number is in `docs/evaluation-report.md`.

| What | Result | Date and notes |
|---|---|---|
| Golden set (105 questions) | 72.4% pass, 78.1% facts right, hit@3 91.1%, MRR 0.81 | 3 Nov, older prompt |
| Routing (partial) | 32 / 38 (84%) | 5 Nov, older prompt |
| Smoke set (24 items) | 19 / 24 (79.2%) | 17 Nov; the 5 misses got code fixes on 18 Nov, not re-run |
| First audio of the real answer (voice) | 3.6 s typical, 5.8 s slow; the filler at 2.3 s | 12 Nov, 22 turns, Groq + Edge. Parts: end-of-speech wait 1.1 s, understanding 1.35 s, search 0.8 s |
| Search on CPU | reranking 16 passages 13–20 s; one query embedding ~0.2 s | 20 Nov; a GPU is needed for voice-fast KB answers |

Re-measure after any voice or prompt change (pending-work A3), on the company laptop.

## Latest changes

### 23 Nov (3): UI on EC2 instead of Fargate (owner's decision; docs and deploy files only)

- New ADR 0030 (replaces the UI part of ADR 0028): `au-ui` runs on ECS with EC2 capacity, on its own small Managed Instances capacity provider `au-ui-ec2` with `t3.small` servers (1 for the pilot, 2 in production). It doesn't go on the brain's `c7i` servers, because in production those are full.
- Changed:
  - `deploy/ecs/ui-task-definition.json` (`EC2`, `MANAGED_INSTANCES`);
  - `deploy/aws-pilot-guide.md` and `deploy/README.md`, which now also say Compose is for the laptop only;
  - diagrams (`diagrams.md`, draw.io 01 and 02) and `LEARNING-GUIDE.md`.
- Cost sheet:
  - The UI line went from Fargate ₹1,780 to 2 × t3.small with 20 GB disks: ₹3,417. t3.small is $0.0224/h in Mumbai, from DoiT, Holori and aws-pricing.com; check it in the AWS calculator.
  - Total ₹28,42,640 → **₹28,44,277**. The Managed Instances management fee is not in the sheet, for the brain or the UI.

### 23 Nov (2): call record file, Docker Postgres for development (code; brain needs a restart)

| What | Done |
|---|---|
| Measured first (fake models, throwaway DB) | A turn runs 14–20 DB statements, 10–24 ms. Call state is ~12 of them (~8–15 ms locally, est. 20–35 ms on RDS Multi-AZ): ~1–2% of a 1–2.5 s voice turn. **Too small for the risk** of moving verification and yes/no state to memory, so that and sticky chat were **not built** |
| Call record file | `services/callrecords.py`: after each call, one JSON file per call (every turn with its hash, verification steps, timeline, alerts, recording details; never the keypad code) in `data/call_records/` or S3 `call_records/YYYY/MM/DD/`. A 5-minute background sweep exports dropped calls and chats quiet for `CALL_IDLE_MIN` (30). The turn path is unchanged |
| Optional row removal | `CALL_ROWS_DAYS` (default 0 = keep everything, as before). >0: that many days after export, turns, timeline and verification events are deleted, `facts/state/pending/auth` cleared; the `sessions` row stays as the call index; `/calls` rebuilds the call from the file (same page, integrity re-checked). Alerts, recordings, reviews, fix list stay |
| Migration | `013_call_records.sql`: `sessions` + `record_key, exported_at, purged_at, summary`; time indexes on `call_events`, `auth_events`. Additions only. `deploy/database/02_schema.sql` now has 001–013 |
| Docker for development | `compose.yaml` (repo root): `pgvector/pgvector:0.8.0-pg16`, brain (GPU, models and data mounted) and UI. Root `.env.example` (`PG_PASSWORD`). Steps: brain/README "Setup with Docker Desktop". Not started or built here |
| Kept for testing (owner) | `tickets`, `handoffs`, `handoff_messages`, `corebank.*`, `eval_runs`, `benchmark_baseline`; not needed in production. All 13 migration files kept (existing databases, incl. the company laptop's, need them) |
| Tests | New `tests/test_call_records.py` (6) pass. Full run: **427 pass, 5 fail**. `test_benchmark` and `test_personal` fail on their own (from earlier uncommitted edits to `benchmark.py` / `personal.py`). `test_review::…sampled` and two `test_voice_api` tests pass alone but fail in the full run (order effect; not yet known whether older than this change: the full suite was last run 20 Nov). Smoke set not run (the turn path didn't change) |
| AWS (open) | `CALL_ROWS_DAYS` value (e.g. 7) and the bucket's Object Lock + versioning + retention period: owner / bank to decide. ECS task definition unchanged (default keeps rows) |

### 22 Nov (1): Edge-era speech code removed, ONNX models deleted (code; brain needs a restart)

| What | Done |
|---|---|
| English number-to-words (`numbers.speech_text`: "₹10,000" → "ten thousand rupees", "%" → "percent"; `indian_words`) | Removed. Amounts stay as digits for Sarvam (bulbul v3+ reads numbers itself). Kept: marker and markup stripping, reference numbers digit by digit, `Rs` / `INR` → ₹, `p.a.` → "per annum", `say()` (pronunciation). Edge (dev / fallback) now also gets digits. |
| `PhraseCache.clear()` | Removed (unused since the `/pronunciation` page went). |
| ONNX model copies `brain/models/bge-m3-onnx`, `bge-reranker-v2-m3-onnx` (≈ 4.9 GB) | Deleted on the owner's laptop. The `SEARCH_RUNTIME=onnx` code stays. |
| Tests | `test_numbers.py`, `test_api_flow.py` expect digits. **Not run.** |

### 22 Nov (2): Bedrock reasoning effort, the opener, this file shortened (code; brain needs a restart; tests written, NOT run)

**Bedrock reasoning effort** (`BEDROCK_REASONING_EFFORT`, default `low`; **replaced on 23 Nov** by one setting per step, see 23 Nov (2)):
- AWS's GPT-6 Luna model card: reasoning effort `none | low | medium | high | xhigh | max`, **default medium**. Converse
  takes model-specific fields in `additionalModelRequestFields`; for Luna the shape is `{"reasoning": {"effort": "low"}}`
  (a flat `reasoning_effort` is rejected, per a community bug report). Before this change the Bedrock adapter sent
  nothing, so Luna reasoned at medium on every understanding and answer call. Groq has always run at low, and every eval
  number above was measured at low.
- `adapters/bedrock.py`: sent only to OpenAI models (`openai.` in the model ID). gpt-oss gets the flat field (community
  example only). A model that rejects the field is remembered and called without it, like `temperature` (which Luna also
  rejects). New test in `test_adapters.py`.
- **Expected:** a shorter wait before the first word of understanding and of the answer. **Not measured.** Check on the
  company laptop: `/v1/stats` (understanding p50, LLM first token), then the smoke set. If routing gets worse, try
  `medium` for understanding only (not built: one setting for both today).
- **Resolved 23 Nov:** `in.openai.gpt-5.6-luna` is AWS's India profile (Mumbai + Hyderabad). Earlier note: the Luna model card lists only `us.` and `global.` inference profiles, and
  ap-south-1 / ap-south-2 as "Global only". The owner uses an `in.…` ID. Confirm in the Bedrock console that it is an
  India-only profile: a `global.` one can process requests outside India.

**The opener** (replaces the timed "one moment" filler):
- Problem seen in demos: the filler ("Let me check that.") played at 1.2 s, then the answer started with nearly the same
  words. The pool had only four lines, so a long call heard each one several times.
- Now: the understanding call also writes `opener`, a 3–8 word line fitting the question ("Sure, let me check the FD rates
  for you."). Code says it only when the first request is a product question answered from the KB (the only slow path:
  search, then the answer model). Not for bank data, card actions, fraud, hand-overs or guided "help me choose" flows (those
  replies are templates, ready at once), not when the agenda intro is said (3+ questions), not after code changed the
  reply language. Code also checks it: 2–12 words; no digits, ₹, %, "?", outcome words (block, refund, approved, done) or
  personal data. It is spoken the moment understanding ends; the answer model is then told "You have just said: ...
  continue, don't acknowledge again", so the caller hears one continuous reply.
- Removed: the filler pool in `voice/phrases.py`, `_filler_after` in `voice/session.py`, the `FILLER_AFTER_MS` setting.
  `phrases.py` stays: it holds the opening disclosure, re-prompt, code reminder, hold, "didn't catch that" and goodbye.
- **Latency:** the time to the real answer is the same (the opener adds about 10–20 output tokens to the understanding
  call, not measured). What changes: on a KB turn the caller hears a fitting line as soon as understanding ends (on Groq
  about 1.35 s after the end-of-speech wait, close to the old 1.2 s filler timer); on every other turn nothing plays before
  the reply. One more Sarvam TTS request per KB turn. `voice_to_voice_ms` now counts the opener as first audio.
- Files: `services/understanding.py` (schema, prompt, parse), `domain.py`, `services/conversation.py` (`opener_for`,
  `_answer`, `PROMPT_VERSION` 2026-11-22.1), `voice/session.py`, `voice/phrases.py`, `config.py`, `store.py`, `main.py`.
  Tests: opener rules in `test_text_rules.py`; the filler test removed and the after-verification test updated in
  `test_voice_session.py`. **Not run.** The understanding schema and both prompts changed: the replay cache misses.
- **Not built (on purpose):** streaming the understanding call so the opener plays even earlier. AWS doesn't document
  streamed tool calls for Luna, and the model reasons before it writes the JSON, so the gain is unclear. Revisit after
  measuring.

**This file:** shortened from about 1,550 lines; the full old version is `progress-archive.md`.

### 22 Nov (3): what the 21 Nov calls show; replay cache and reasoning effort (code; brain needs a restart)

- **21 Nov calls on the owner's laptop** (6 calls, 28 turns, 09:43–09:58, read-only from the local database): only
  **1 of 28** turns was a KB answer. The rest were bank data, verification, card block, complaint and hand-over replies:
  templates that start with "जी, ज़रूर।", "Sure.", "Okay, let's…", "Sure, I can do that." Understanding took
  **1.5–4.0 s** (Groq), so the 1.2 s filler played on almost every voice turn. **The filler was voice only:** it never
  reached the chat, the transcript or the call events, which is why the records don't show it. What the caller heard:
  "जी, मैं चेक करती हूँ। … जी, ज़रूर। …". Removing the filler removes that repetition on every turn type.
- **Trade-off now (not measured):** on bank-data turns nothing plays until understanding ends (on Groq that was
  1.5–4.0 s after the end-of-speech wait). With Luna at low effort it should be shorter; measure on the company laptop
  (`understand_ms` in `/v1/stats`). If it is still over ~2 s, a fallback is possible: one neutral line after ~2 s, and the
  template's own "जी, ज़रूर।" / "Sure." dropped when it played. **Owner's decision; not built.**
- **Other repetition seen (not fixed, owner to decide):** the card-block confirmation was read again in full after a
  side question ("Yes. With a permanent block … Just to confirm, I'll permanently block …"); a follow-up answer repeated
  the previous turn's sentence ("Pichhle 7 din mein … ₹3,940 …" again before the new list); "I found 3 on your account
  ending 9001 …" read the same list again for a narrower question.
- **Replay cache fix:** the cache key did not include the reasoning effort, so a smoke run at `medium` after one at
  `low` would have replayed the `low` answers and the comparison would have been wrong. Now `ReplayLLM(variant=…)` adds
  `bedrock-effort=<level>` to the key when `LLM_PROVIDER=bedrock` (`adapters/replay.py`, `container.py`).
- **Reasoning effort and accuracy:** not proven for Luna. To check on the company laptop: smoke set and regression pack
  with `BEDROCK_REASONING_EFFORT=low`, then `medium`; keep `low` only if it is no worse. Reasoning models usually bill their thinking as
  output tokens, so `low` is likely cheaper too (not checked for Luna).
- During the check a private Postgres was started on the owner's laptop (`brain/data/pg`, crash recovery from an
  unclean shutdown on 21 Nov completed); it was left running (stopping it was not allowed by the tool permissions). The
  brain reuses it on the next start.

**Next:** on the company laptop: restart the brain; `uv run pytest`; a few voice calls with product questions (listen
for the opener, and that the answer doesn't repeat it); `/v1/stats` before and after `BEDROCK_REASONING_EFFORT=low`;
then `uv run python -m evals.smoke`.

### 23 Nov: fixes from the 20–21 Nov calls (code; brain needs a restart; checked by direct function calls, pytest NOT run)

The owner listed what to fix after a review of the 10 calls of 20–21 Nov. Nothing else was changed.

| What went wrong (call) | Cause | Fix | Where |
|---|---|---|---|
| "Please raise a complaint" to "Shall I raise a complaint?" sent the caller to a person; no ticket (3eab4732) | The 20 Nov word-count replacement read "complaint" as a new request | **Reverted** the 20 Nov replacements of word counts in `confirm`, `journeys`, `smalltalk`, `dialogue`, `turntaking` and two in `understanding`, and the 53 regex gaps changed to "same sentence". Kept: the account pick ("which account?" resumes the waiting question; `says.new_request`, now used only there). `guard.py` is back to the last commit exactly | `confirm.py`, `journeys.py`, `smalltalk.py`, `dialogue.py`, `voice/turntaking.py`, `understanding.py`, `guard.py`, `personal.py`, `conversation.py`, `says.py` |
| "In how many days will I get my replacement card?" got "Yes." and the confirmation again (ca288778) | Same 20 Nov change | Same revert (a question of 7+ words goes to the LLM again) | `journeys.py` |
| A question waiting on "which account?" counted as answered (a6545287, de568948) | `agenda.settle` marked every waiting item done at the next turn | A bank-data item waiting on "which account?" stays waiting: done when the reply resumes it; back to open (offered later, counted by the review) when the caller moves on | `agenda.py` (`settle`, `after_account_choice`), `conversation.py` |
| The ATM complaint answered as the balance (a6545287) | The LLM copied the last turn's balance question | (1) A code check for reported problems in the caller's own words (ATM no cash, failed but debited, sent but not received, charged twice), like the fraud check: the complaint leads, team = payments. (2) A copy check: a bank-data request asking for exactly what was answered last turn, with no word naming its topic, is dropped; a "rider" (same topic and period, nothing new) is dropped while another request remains; duplicates are merged; if nothing is left, the sentence is read once more without the conversation (used only if that finds a real request) | `understanding.py` (`with_issue`, `issue_screened`), `conversation.py` (`_without_copies`, `copied`, `rider`, `without_duplicates`), `state.py` (`answered`) |
| English answered in Hinglish (a6545287, d42aa7ed, 3eab4732) | Code kept the LLM's label for Latin letters | Latin letters: any romanised Hindi word = Hinglish, else English (word list extended) | `conversation.reply_language` |
| "10K" not read as an amount | The amount reader had no units | k / thousand / hazaar / lakh | `understanding.amount_said` |
| "When was my last EMI?" answered twice (a911ca54) | The duplicate check compared wording only | Also by the data asked for | `understanding.parse` (`personal_key`) |
| "Talk to someone about my loan" went to new accounts (a911ca54) | The LLM's "sales" label | The team from the product (loan → Loans); sales kept only with "new / apply / open / lena" | `understanding.pick_department` |
| "Does that include money people sent me?" and "how much did Rajesh send?" repeated the list (5884ab26, d42aa7ed) | Copied requests | The copy check above; the "No, that total is only money you paid out" answer once | `conversation.py` |
| A 1 Nov salary listed under "the last 7 days" (d42aa7ed) | The copied follow-up lost the period | Copy check; a follow-up that refers back ("those", "isme") keeps the period of the list just given | `conversation.carry_period` |
| "₹3,940 in 4 payments", then "I found 3" (5884ab26) | A filtered list kept only its first 3 matches | Every match counted (up to 10): "I found 4", three read, "Want the other 1?" | `personal._transactions` |
| "Do I have any credit or debit cards?" answered with account status (de568948) | Detail "status" was moved to the account topic | Card status stays a card question and lists the cards; "credit or debit" asks both | `conversation._TOPIC_KEEPS`, `personal.py` (`cards_have`), `understanding.both_cards` |
| "No money was deducted … the ₹10,000 will come back" (3eab4732) | Mock record: status "failed" plus a refund note | Mock ATM withdrawal recorded as debited (status success) with the refund note. The dummy workbook is untouched. Takes effect when the mock bank is re-seeded (it re-seeds on the first start of a new day, or `clean-slate`) | `adapters/mock_corebank.py` |
| "Payments & Disputes" spoken with "&" | Team name | Said as "and" | `desk.department_name` |
| "आपके 4521 वाले Value अकाउंट चालू है" (a6545287) | One Hindi form for "of" and subject | Subject form "आपका … वाला … अकाउंट" for the status line; also "वाला … डेबिट कार्ड जुड़ा है" | `personal.py` |
| "Salary account balance" said as "your Value account" (fbe5b9aa) | Template didn't say "salary" | "your salary account, the Value account ending 4521" | `personal.py` (`acct_salary`) |

- **Regex decision:** regex is used only where the words are a small fixed set and it works next to the LLM: the
  problem-report check, the team words, "credit or debit", amounts. The copy check compares data fields, not words.
- **Checked:** 49 direct checks pass (the two reverted cases, language, amounts, problem reports and a how-to that must not
  trigger, ATM leads with team payments, loan team, both cards, card status, duplicates, the copy check on the readings of
  calls a6545287, 5884ab26 and d42aa7ed, the agenda, the templates). `tests/test_agenda.py` updated for the new agenda rule.
- **Not run:** `pytest` and the smoke set (owner's laptop). Not checked live: the re-read without the conversation (one
  extra LLM call, only when every request was a copy).

### 23 Nov (2): the /bench page, reasoning effort per step, Luna research (code + UI; brain needs a restart; tests written, NOT run)

**The `/bench` page ("Speed & quality"):** the running setup against a benchmark to beat.
- A **setup** is: LLM provider, understanding and answer model and reasoning effort, speech-to-text, text-to-speech,
  search (provider, runtime, device), vector store, prompt version. Every turn now records `data.setup_id` + `data.setup`
  (`services/benchmark.setup_of`). Turns are grouped by setup, never by time, so numbers change only when the setup changes.
- **The benchmark:** pinned by a person ("Keep this setup as the benchmark"; its numbers are a frozen snapshot, table
  `benchmark_baseline`, migration 012) or, when nothing is pinned, the last other setup with at least 20 real turns.
  Replayed eval answers (`cached`) are left out of every number.
- **Speed (p50 / p95, ms):** caller stops → first audio (voice), **turn start → first sentence ready** (new,
  `first_reply_ms`, timed on every path in `Conversation.turn`), understanding, answer LLM first token, embedding, reranking,
  bank data, speech to text, end-of-speech wait, brain time per turn.
- **Quality:** latest smoke / regression / golden pass rate for that setup (smoke and the golden runner now write
  `eval_runs` with the setup id; a smoke run with `--only` doesn't), post-call judge score, share of high grounding,
  **KB answer confidence** (the top passage's reranker score behind a KB answer: there is no model-reported confidence),
  figure-check refusals, errors, refusals, testers' thumbs, share of the understanding prompt served from the
  provider's cache, understanding output tokens (includes reasoning).
- API: `GET /v1/bench`, `POST /v1/bench/baseline` (`setup_id`, `label`; default the current setup), `DELETE /v1/bench/baseline`.
  UI: `ui/app/bench/page.tsx`, nav "Speed & quality". Loads on open; no polling.
- Checked: `tsc --noEmit` passes; Python compiles. **Not run:** `pytest` (new `tests/test_benchmark.py`), `bun run build`
  (not run, so a running dev server isn't disturbed), the page in a browser.

**Reasoning effort per step** (replaces `BEDROCK_REASONING_EFFORT` from 22 Nov): `UNDERSTAND_REASONING_EFFORT` and
`ANSWER_REASONING_EFFORT`, both default `low` (what every eval ran with). The LLM port takes `effort` per call; Bedrock
sends `{"reasoning": {"effort": …}}` to OpenAI models; Groq's gpt-oss gets low / medium / high (`none` is sent as low).
The replay cache key includes the effort (the 22 Nov `variant` is gone). The hand-over summary and the judge use low.

**Luna research (GPT-5.6 Luna, `in.openai.gpt-5.6-luna`):**

| Claim (owner's research) | Verdict for us | Done |
|---|---|---|
| `reasoning_effort: none` cuts TTFT | True: AWS lists none … max, default medium. But understanding writes a complex routing JSON (several requests, risk flags, messy speech): exactly where light reasoning helps. The KB answer is figure-checked, so it is the safer place to try `none` first | Per-step setting built; defaults stay `low` until `/bench` shows `none` is no worse |
| Prompt caching (implicit and explicit) | Implicit caching works on Converse when the prompt prefix is ≥ 1,024 tokens and identical. Our understanding prompt (system + schema, ~3k tokens) is static, so it should already be cached; the answer prompt is too short (~700). Explicit `cachePoint` is not documented for these models on Converse (AWS's Luna card: explicit caching not supported with Converse) | Cached tokens now recorded per call and shown on `/bench`; no `cachePoint` (it may be rejected) |
| Use ConverseStream | Already: answers stream sentence by sentence. Understanding is one structured call (its result is needed whole) | Nothing |
| Avoid `global.` | `in.openai.gpt-5.6-luna` is AWS's India profile (Mumbai + Hyderabad only). **The 22 Nov "to check" note is resolved** | Docstring corrected |
| Keep the client warm | One boto3 client per process, reused, with keep-alive | Nothing |
| 1M-token context | Not useful: our prompts are a few thousand tokens | Nothing |
| "~110–280 ms TTFT for Luna at none" | From a blog, not AWS; unverified. Our time to first sentence also includes understanding and search | Measure on `/bench` |

**Faster models in Mumbai (data must stay in India, ADR 0026):**

| Model | Mumbai route | Usable for customer calls? |
|---|---|---|
| GPT-5.6 Luna (now) | `in.` India profile | Yes |
| GPT-5.6 Terra | `in.` India profile | Yes; AWS says nothing on its speed vs Luna; test on `/bench` |
| Kimi K3 | `in.moonshotai.kimi-k3` (India) | Yes, but a large model: unlikely to be faster |
| Nova Micro / Nova Lite | only `apac.` (can process in other APAC countries) | No (data leaves India) |
| Nova 2 Lite | only `global.` | No |
| A small model on our own GPU in Mumbai (EC2 / SageMaker) | ap-south-1 | Possible later; weaker on our routing JSON and Hindi; a project of its own |

**Next:** on the company laptop: restart the brain (migration 012 runs); a few calls; open `/bench`, press "Keep this setup
as the benchmark"; then try `ANSWER_REASONING_EFFORT=none` (restart, the same calls, `uv run python -m evals.smoke`)
and compare; then `UNDERSTAND_REASONING_EFFORT=none` the same way; optionally `in.openai.gpt-5.6-terra`.

### 23 Nov (3): reranker on / off switch (code; brain needs a restart; tests written, NOT run)

**Why:** the company laptop has no GPU, and reranking 16 passages on its CPU takes ~20 s. Until a GPU comes, the owner
wants to run without the reranker and turn it back on later. The passages to the answer model stay at 5 (owner).

**Research behind it (no run):**

| Question | Answer |
|---|---|
| Is the 20 s the query embedding? | No. One query embedding (bge-m3, CPU) is ~0.2 s (20 Nov). Reranking 16 (question + passage) pairs through a 568M model is almost all of it |
| One KB turn, step by step | Understanding (Bedrock LLM: intent + English search query) → embed the query (bge-m3, local) → vector search + keyword search (Postgres) in parallel → RRF merge → rerank the top 16 (local; skipped when off) → top 5 → answer LLM (Bedrock, streamed) → figure check → speech |
| Elasticsearch instead of a reranker? | Not a replacement: it is a first-stage search like our hybrid search. Its own reranking (Elastic Rerank, an English-only DeBERTa cross-encoder) runs on Elasticsearch ML nodes (compute again; paid tier; a `.pt` model file) |
| Bedrock's rerank models (Cohere Rerank 3.5, Amazon Rerank 1.0)? | Not in Mumbai: data would leave India (ADR 0026) |
| Expected effect of "off" (a guess, not measured) | Time to first sentence ~23 s → ~3-4 s on the company laptop; golden pass rate maybe 5-15 points lower; weakest on rate-table rows and near-identical products (a real but wrong figure passes the figure check) |

**What changed:**

| Where | Change |
|---|---|
| `config.py`, `.env.example` | `RERANK` (default `true`, so nothing changes until it is set) and `MIN_DENSE_SCORE` (default 0.35) |
| `container.build_search`, `adapters/models.NoReranker` | `RERANK=false`: the reranker model is not loaded (~2.2 GB of memory freed, faster start); search server / SageMaker: no rerank warm-up |
| `retrieval.py` (`without_reranker`, `warm`) | The 16 candidates keep the RRF order; each gets `rerank_score` = its RRF score scaled to 0..1 (1.0 = first in both searches), so the passage trim and source checks after search work unchanged. No `rerank_ms` (the step did not run). `warm()` skips the reranker |
| `conversation.py` (refusal gate) | With the reranker off, the RRF score can't say "the KB has nothing on this", so the gate uses the best embedding similarity of the 5 passages: below `MIN_DENSE_SCORE` → refuse. The trace records `retrieval.reranker = "off"` and `closest_dense` |
| `audit.grounding` | With the reranker off, an answer's grounding is at most "medium", basis "no reranker: closest passage by meaning 0.xx" |
| `benchmark.setup_of`, `ui/app/bench/page.tsx` | `/bench` setup gets `rerank: off` only when off (setups recorded before keep their id), so its turns are a separate setup; the page shows "Reranker: on / off" |
| `tests/test_rerank_off.py` | Answers without calling the reranker, RRF order and scale, refuses when nothing is close, grounding "medium", the stub |

**`MIN_DENSE_SCORE` is not calibrated.** 0.35 only refuses clearly unrelated questions; a related but unanswerable one
("HDFC's FD rate", "the daily ATM limit") reaches the answer model, which must say NO_ANSWER. Watch refusals on `/bench`
and the "closest passage by meaning" values on `/calls`, then adjust.

**Removed the same day (owner):** the reranker comparison lab (`evals/rerank_bench.py`, its test, the `/rerank` page and
nav link, its README sections). It was never run on the company laptop; one result file from a short check on the owner's
laptop was deleted too.

**Not run (owner's choice):** `pytest`, `bun run build`, the page in a browser.

**Next (company laptop):** `python -m pytest tests/test_rerank_off.py` (then the full `pytest`); set `RERANK=false` in
`brain/.env`; restart the brain; a few KB questions (rates, card fees, one the KB can't answer); open `/bench`: the
setup shows "Reranker: off", with its own speed (no reranking line) and refusal numbers. To turn it back on: `RERANK=true`, restart.

## Open questions (for the owner or the PM)

- **Production storage (22 Nov, analysis only, nothing built):**
  - Leaning: no database for conversations. Memory during the call; the call record and the key events go to S3; analytics on Athena.
  - KB on Amazon OpenSearch, with Postgres as the KB-only fallback.
  - Official Mumbai prices:

    | Setup | ≈ $/month |
    |---|---|
    | OpenSearch, 3 × r7g.large.search + 3 masters | 400 |
    | OpenSearch, lean (2 nodes) | 196 |
    | RDS PostgreSQL r7g.large Multi-AZ | 410 |
    | Aurora 2 × r7g.large | 458 + storage |

  - Still open: a Postgres keyword-search benchmark at 5–10 lakh passages; the OpenSearch accuracy run; whether Cisco or we hold the legal call recording; retention.
  - **Revised 22 Nov (owner's challenge):** since the two cost the same, keep **one RDS PostgreSQL (Multi-AZ)** for the KB (pgvector + keyword, unchanged and already proven) and small operational data (call index, reviews, a live-call table if ever needed).
    - Conversations and events stay out of it: memory, then S3 (Object Lock).
    - Size: t4g.large Multi-AZ ≈ $244/month while the KB is up to ~3 lakh passages; r7g.large Multi-AZ ≈ $410 beyond that.
    - OpenSearch only if the scale benchmark shows Postgres keyword search too slow.
  - **Final Postgres scope (22 Nov), all other data stays in memory or S3:**
    - the KB (documents, versions, chunks, embeddings, ingestion jobs);
    - a call index of one row per call (written at start and end, never blocking the call, kept 12 months);
    - post-call reviews and the fix list.
    - Optional, only if asked: a live-call table, shared keypad-code attempt counters.
  - **Expected size and load:**
    - The KB is about 50,000 passages at the start and at most ~3.5 lakh after 5 years (owner's PDF figures).
    - Total size under ~25 GB.
    - About 2 index writes per call; KB searches about 8 queries a second at peak.
  - **Cost Google Sheet updated 23 Nov** ("AU voice agent - AWS and Sarvam cost estimate"):
    - A dropdown on Servers!B52 chooses RDS or Aurora; it is set to RDS (db.m7g.large Multi-AZ, $0.479/h). Aurora is db.r7g.large × 2.
    - The Prices sheet has rows for both (RDS disk $0.262/GB, no per-request charge), plus S3 PUT, Firehose and Athena prices.
    - Storage, section 2: new call-record lines (75 KB per call, 4 files saved, Firehose, Athena).
    - Storage, section 3: rebuilt (KB 5 GB, a 1 KB index row kept 12 months, 50 GB RDS disk, 12 queries per call).
    - Cost, Choices #10, Answers row 13 and the "What we keep in S3" list were updated.
    - Result: the database line goes from ₹43,093 to ₹32,869; the monthly total from ₹28,53,051 to ₹28,42,640.
    - Not changed: the 100 calls at once (the owner plans 200), and the DB_Tables tab (in the Google copy, columns A–F look empty).
  - **Built 23 Nov (see "Latest changes"):** the call record file and the optional row removal. **Not built, by measurement:** reading call state from memory, and sticky text chat. Call state stays in Postgres, so voice and chat already work the same, and any container can serve a chat message.
  - **Revised plan, 23 Nov (owner's challenge; replaces "memory, then S3 only"). Partly superseded by what was built:**
    - During a call, the code **reads** state only from the call's memory. It still **writes** every turn to today's Postgres tables, but in the background, never holding up the reply (with a retry queue if the database is briefly down).
    - Why the change: RDS charges nothing per write, and the KB search already needs Postgres on the live path. So the database stays the crash log, and instant S3 event files are dropped.
    - At hang-up: one locked call record goes to S3 (Object Lock).
      - Production then deletes that call's turn and event rows and clears `auth`, `facts`, `state` and `pending` on its `sessions` row. The row stays as the call index.
      - Locally nothing is deleted.
    - `sessions` gains a few summary columns written at hang-up (turns, outcome, department, S3 location), because the `/calls` list computes them from `traces` today. The change only adds columns; nothing is dropped.
    - `/calls`: the list comes from `sessions`. The detail comes from Postgres while the call is live or the rows exist, otherwise from the S3 record.
    - A sweeper exports calls that have rows but no S3 record (cut off by a crash).
    - Alerts: already logged as `alert` lines, which ECS already sends to CloudWatch Logs (`/ecs/au-brain`). Production needs only a metric filter, alarms and SNS (AWS setup, no code). The alerts table stays.
    - **Kept unchanged for testing (owner, 23 Nov):** `tickets`, `handoffs`, `handoff_messages`, `corebank.*`, `eval_runs`, `benchmark_baseline`.
      - Not needed in production: tickets go to AU's CRM, the hand-over to Cisco, bank data to AU's APIs, and evals stay laptop-only.
    - Infra: an S3 bucket (Object Lock, KMS, lifecycle rules), ALB stickiness, Firehose and Athena, RDS db.m7g.large Multi-AZ, CloudWatch metric filters and alarms.
    - Docs: a new ADR that supersedes the call-storage parts of ADRs 0003, 0014, 0021 and 0026.
    - Measure before and after with the offline tests and the smoke set.

- **Shared OTP (PM review, 17 Nov):** should the agent transfer to the fraud team straight away (no "Shall I?"), and skip
  sending a new code to the same mobile? Today it verifies, checks two days of payments, warns, then offers a transfer.
  Needs AU's approved fraud procedure.
- **"I'm interested in credit cards":** ask first whether they want help choosing, benefits or eligibility (one more
  turn), or keep going straight to "how much do you spend a month, and on what?"

- **Deployment proposals (12 Nov) to confirm before writing ADRs** (Cisco details are for later, per the owner):
  - how Cisco hands calls to us (audio stream, or SIP + a media gateway);
  - a modular monolith;
  - GPU servers for embedding and reranking (1 + a spare);
  - Aurora (RDS for a pilot);
  - no Valkey or SQS at launch;
  - Converse, not Responses.

  See `docs/deployment-options-and-costs.md`.
- **Redis at the first multi-server deployment or not?** ADR 0021 (accepted) says add it then; the 12 Nov AWS proposal
  and pending-work B3 say no Redis at launch (Secrets Manager + Postgres are enough for a few copies). A new ADR should
  supersede 0021 once decided.
- `docs/deployment-options-and-costs.md` (and the local cost workbooks) are missing from the folder but still linked
  from CLAUDE.md, README and the guide. Restore it, or point the links at the Google Sheet and the guide?
- **Calls per container: 9 or 8?** The laptop measurement suggests real calls may use ~6.5–7.5% of an AWS vCPU (the sheet
  plans 6.3%, giving 9). `MAX_LIVE_CALLS` is 9 for now; lower it to 8 if the owner prefers the safer side before the AWS test.
- **For AU's Cisco team:**
  - UCCE/CVP versions;
  - Cloud Connect and the Webex region where the audio is processed;
  - BYOVA licences;
  - SIP trunk details if BYOVA isn't possible.
- **For AU:**
  - peak calls at once and the hourly curve;
  - is the line 24×7?
  - retention for recordings and transcripts;
  - who issues the bank API tokens;
  - EKS or ECS as their standard (we propose ECS);
  - a human agent's cost per call.

- Is sending (redacted) call transcripts to the judge model approved for this data? It is the same provider (Groq) as the
  agent, but a separate model does not mean a separate privacy or compliance boundary. Same for transcript retention.

- English "difference between A and B" makes the router write `kind: "compare"` (repaired in code, no extra call).
  Making the strict path itself reliable needs a routing-set run (wording or a smaller schema) when quota allows.

- FD early closure: the 1% penalty and the "under 7 days, no interest" rule come from our **synthetic** service-policy
  document, not from AU. AU's real rules for callable / non-callable and tax-saver FDs are needed (aggregator sites say
  the 5-year tax-saver FD cannot be closed early).
- May the agent suggest an overdraft against an FD instead of breaking it? (not built until approved)
- The bank's approved fraud wording for the openings and the OTP-shared steps (ours are placeholders).
- Retention and deletion policy for call facts (purpose, spend, travel); today they live only for one call.

- The eight PM questions in PHASE-4-PLAN §19: top call reasons, API catalogue and sandbox, allowed actions, document ownership and effective dates, compliance wording, real queues and hours, languages, target benchmarks.
- Paid Groq tier to remove the daily limit? (PHASE-4-PLAN §10)
- Groq free tier: the daily **token** limit (200k on the 20b model) allows only about 60 understanding calls a day with the v2 prompt. Paid tier, a smaller prompt, or a second key? (PHASE-4-PLAN §10 assumed the request limit binds)
- Highest-rate questions read from a table can pick the wrong row (the figure check passes any real figure). Better table chunking or a deterministic lookup?
- Green FD (AU Planet First) says "up to 7.90%" in the CASA+TD docx; the rate card has no Green FD row. Is 7.90% right for that product? Left unchanged.

## Architecture decisions (keep unless the owner changes them)

| Decision | Why |
|---|---|
| LLM understands; code routes, authorises and calls bank APIs (no LLM function-calling loop) | Each tool hop adds 0.6–1 s, and the model could pick the wrong tool or customer |
| Ports and adapters (`Bank`, `ServiceDesk`); mock now, AU's APIs later | Swapping to real systems = a new adapter, no conversation-code change |
| Same agent for all departments; the 10 departments are human transfer queues, not separate bots | Most requests are resolved by the agent; queues are for people |
| Deterministic handoff brief; any AI summary is labelled and figure-checked | A summary must not invent facts |
| Document-level KB tags, not per-figure policy records | Enough to stop variant mixing without maintaining every figure twice |
| Regex + checksum PII redaction (stdlib) | Precise for Indian IDs, no new dependency |
| One `mockbank` process with per-system routers and schemas, not 8 microservices | Same realism (HTTP, separate IDs, failures) without the ops overhead |
