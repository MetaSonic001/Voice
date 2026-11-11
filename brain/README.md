# AU SFB Brain + Voice (MVP 1 + MVP 2)

The AU Small Finance Bank inbound voice agent, by text **or by voice** in English, Hindi or Hinglish:
- **MVP 1, general questions:** short, cited, checked answers from the knowledge base (rates, fees, products).
- **MVP 2, personal questions:** the caller is verified (registered mobile number + one-time code). Questions about their own accounts, FD, loan and cards are then answered from bank data.

Every answer gets a trace, plus evaluations, logs and metrics. Account actions (block a card…) and human transfer come later; today the agent says so and offers a transfer.

Two ways to run it:

| Mode | Set | What runs |
|---|---|---|
| **Brain only** (RAG bot, text) | `VOICE_ENABLED=false` | documents, `/v1/ask`, evals. No voice models or routes. |
| **Brain + voice** (default) | `VOICE_ENABLED=true` | everything above + `/v1/voice/*` (REST + live WebSocket call) |

## What it does

| Capability | How |
|---|---|
| **Ingest documents** | PDF (pdfplumber: text PDFs, tables cell by cell), DOCX (native reader that keeps bold headings, lists and tables), XLSX/CSV (optionally selected sheets), Markdown, TXT. Type checked from the file's first bytes; same file never stored twice. Scanned PDFs are rejected with a clear error. |
| **Manage documents** | Versions, replace (atomic swap), update details, disable/enable, soft delete, permanent purge, roll back, re-index. `purpose=data` files are stored but **never** searchable (e.g. customer data). |
| **Structure-aware chunks** | Sections grouped per product. Table rows carry their column headers, including bank rate sheets with stacked or merged headers: each chunk starts with its context ("Term Deposits › Domestic › …"), then rows like "Tenure: 12 Months 1 Day to 15 Months \| Less than 3 Crore (Callable) / Senior citizen: 7.60%". Each chunk has a path "Document › Product › Section"; duplicates are dropped. Checked: every word of every KB file is indexed (only row numbers like "8." are dropped). |
| **Hybrid retrieval** | Dense (bge-m3, multilingual, pgvector HNSW) + keyword (Postgres full-text), run in parallel → Reciprocal Rank Fusion → cross-encoder reranker (bge-reranker-v2-m3). |
| **Understanding** | One fast strict-JSON call (Groq `gpt-oss-20b`): language (en/hi/hinglish), request type, follow-up resolution, English search keywords, the personal topic and filters, and **call facts**. |
| **Conversation memory** | Every turn is stored (the full call record). The understanding step sees the last 6 turns, with long answers trimmed. **Call facts** last the whole call however long it runs: senior citizen yes/no, product, tenure, amount, chosen account. They're kept in the session row, updated by the same LLM call (no extra call), merged in code (a new value replaces the old, silence keeps it), and given to both the understanding and the answer step. So "I'm a senior citizen" in turn 1 still applies in turn 20. Account data is never put back into the LLM's history. |
| **Routing** | General → knowledge answer. Personal → verify, then answer from bank data. Action (block, enable…) / issue (fraud, failed payment) / human → transfer offer. Small talk / out of scope / manipulation → fixed replies. |
| **Caller verification** | The caller's number is matched to the registered mobile number (RMN). If it doesn't match, the agent asks for the RMN. Then a 6-digit one-time code goes to the RMN: `secrets` module, stored only as an HMAC, 5-minute expiry, 3 tries then lock. The caller types the code on the keypad; it never passes through speech-to-text, the LLM, traces or logs. Verification lives server-side and ends after 10 minutes idle. Every step is recorded in `auth_events`. SMS delivery is simulated in development (shown in the test UI). |
| **Personal answers** | Balance, transactions (last N, debit/credit, period, amount, "salary credited?", UPI spend totals), FD, loan/EMI, credit card, debit card controls, summary. **Fixed templates filled from bank data** (en/hi/hinglish), with no LLM writing the answer, so no invented figures. Only the last 4 digits of any account or card are spoken. Several accounts → "which one?" ("salary wala" works: the salary account is the one with salary credits). Data that isn't available → "I can see X, not Y" + transfer offer. Answers with account data are never sent back to the LLM in later turns. |
| **Grounded answers** | Groq `gpt-oss-120b`, 1–3 spoken-style sentences in the caller's language, citation markers, or `CLARIFY` when a needed detail is missing. |
| **Safety checks in code** | An answer is refused if it cites nothing, if **any number in it doesn't appear in the cited sources** (1,00,000 / 1 lakh / 100000 count as equal), or if it **states a figure in words** ("saat point paanch percent", "सात दशमलव छह प्रतिशत"). Words can't be checked, so answers must use digits: a rate or amount word (percent, lakh, crore, rupees, प्रतिशत…) must follow a digit. The caller then hears "I don't have approved information… I can connect you to a specialist". |
| **Conflicting sources** | What banks do: rates come from one authoritative source (the rate sheet / rate master), and contradictions are fixed in the content, not guessed at by the assistant. At answer time, if the sources give different figures for the same thing, the assistant doesn't pick one: it says it can't confirm and offers a specialist. What it can't catch is a document *stating* a figure that another contradicts. Example: the product KB says senior-citizen FD rates go "up to 7.90%" (3 places), while the 23 Apr 2026 rate sheet's highest senior rate is 7.75%. Retrieval finds both with high confidence (0.97+), and the model quotes the explicit sentence. Fix such contradictions in the documents (Knowledge base page); answers marked ✗ Wrong in the UI show where. |
| **Voice-ready replies** | Every reply has `answer` (display) and `speech_text` (no markers, numbers as Indian words in English). |
| **Traces** | Every question stores: understanding, both search lists with ranks/scores, reranker scores, chunks used, citations, check results, model and prompt versions, timings per step, tokens. |
| **Evaluations** | Golden set (105 questions: EN/HI/Hinglish, answer/clarify/refuse/verify/handoff) → pass rate, fact accuracy, retrieval hit@k/MRR, latency p50/p95 per step, cost, Markdown report, run comparison. |
| **Voice call** | Streaming pipeline: Silero VAD → STT (Groq Whisper / Sarvam) → brain (streamed sentence by sentence) → TTS (Edge / Sarvam) → paced audio. Details below. |
| **Security** | Prompt-injection defence: retrieved text and caller words are data, a `manipulation` intent gets a fixed reply, and the agent never reveals its instructions. One masking rule set (`app/services/privacy.py`) is applied before the LLM, in traces and in every log line: OTP/PIN after a keyword, account/card/phone/Aadhaar numbers in digits *or spoken as words* (English, Hinglish, Devanagari), PAN, e-mail and UPI IDs. API key and rate limits (`/v1/auth` included). Unverified callers never get account data. |
| **Test UI** | `../ui`: chat, voice note, live call (with a "focus on my voice" gate for background voices), keypad, simulated SMS, timings and trace links. |
| **Ops** | JSON logs (`logs/app.jsonl`, masked), Prometheus `/metrics`, `/health`, `/health/ready`, `/v1/stats`. |

## Setup (Windows, no Docker)

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

First time:
```bash
cd brain
uv sync
cp .env.example .env
uv run python scripts/download_models.py
```
- `uv sync` installs everything (CUDA 12.8 PyTorch; runs on CPU if there's no GPU).
- In `.env`, put your Groq key (already done on this machine).
- The model download is about 4.4 GB, once: bge-m3 and the reranker. It resumes and verifies checksums.

Every time:
```bash
python app.py
```
Run it with the venv active (`.venv\Scripts\activate`), or as `uv run python app.py`. It serves http://127.0.0.1:8000, docs at `/docs`; the first start takes about a minute to load the models. Then, in another terminal:
```bash
cd ../ui
bun run dev
```
That's the test UI at http://localhost:3000.

- **Postgres** runs privately from `data/pg` through `pgserver` (pip-installed, includes pgvector). No Docker, no install. Set `DATABASE_URL` to use any other Postgres with pgvector instead.
- **Models**: if `models/bge-m3` and `models/bge-reranker-v2-m3` exist they're loaded from there (offline); otherwise they're downloaded from Hugging Face on first start.
- **Bank data (MVP 2)**: read-only from `../Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx` (the file is never changed or indexed). "Today" is fixed at 31 Dec 2026, because the dummy transactions end then (`DEMO_TODAY`).
- **GPU**: `DEVICE=auto` (default) uses an NVIDIA GPU in fp16 when present (here an RTX 3050: rerank ~0.5 s instead of ~25 s on CPU). `GET /health/ready` and the startup log show the device.

## Use it

### 1. Upload the knowledge base
```bash
uv run brain upload "../Sample Questions/Knowledge Base/"*.docx "../Sample Questions/Knowledge Base/"*.pdf
uv run brain upload "../Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx" --sheets "Acct Debit card Mapping"
uv run brain status
```
The second command ingests only the non-personal account → debit card sheet. Customer sheets are never indexed.

### 2. Ask
```bash
uv run brain ask "What is the annual fee for the Zenith+ credit card?"
uv run brain ask "मैं senior citizen हूँ। एक साल की FD पर सबसे अच्छा rate कितना है?"
uv run brain chat            # interactive, with follow-up questions
```
Each answer prints its outcome, citations, per-step timings and the trace URL.

### Knowledge base (UI)
The UI's **Knowledge base** page (`/kb`) lists every ingested document. It shows each document's passages exactly as stored, version history and parser report. From there you can upload, replace, roll back, set the valid-from date, disable or delete documents, and preview which passages a question would use. On the chat page, a **Knowledge base · this call** box highlights which documents (and bank data) this call's answers used.

### 3. Personal questions (MVP 2)
Easiest in the UI: pick **Calling from** (a test customer, an unregistered number, or withheld), ask "What is my balance?", copy the code from the simulated SMS panel into the keypad. The question that was waiting is then answered.
Through the API:
```bash
curl -X POST localhost:8000/v1/sessions -H "content-type: application/json" -d "{\"caller_number\": \"7107420369\"}"
curl -X POST localhost:8000/v1/ask -H "content-type: application/json" -d "{\"question\": \"What is my balance?\", \"session_id\": \"SESSION\"}"
curl -X POST localhost:8000/v1/auth/verify -H "content-type: application/json" -d "{\"session_id\": \"SESSION\", \"code\": \"123456\"}"
```
- **Step 1** creates the session; its response has `"id": SESSION`.
- **Step 2** asks the question. The response has `auth.simulated_sms` with the code.
- **Step 3** verifies the code and answers the waiting question.

`GET /v1/demo/callers` lists test customers with different product mixes.

### 4. Everything through the API
- **Browser:** <http://127.0.0.1:8000/docs> (interactive API page: try every endpoint there).
- **Request collection:** `requests.http` has every call ready (VS Code "REST Client" or JetBrains HTTP client).

| Area | Endpoints |
|---|---|
| Documents | `POST /v1/documents` · `GET /v1/documents` · `GET /v1/documents/{id}` · `GET /v1/documents/{id}/chunks` · `PATCH /v1/documents/{id}` · `DELETE /v1/documents/{id}[?purge=true]` · `POST /v1/documents/{id}/versions` · `POST /v1/documents/{id}/versions/{n}/activate` · `POST /v1/documents/{id}/reindex` · `POST /v1/reindex` · `GET /v1/jobs` |
| Conversation | `POST /v1/sessions` (`caller_number`) · `GET /v1/sessions/{id}` · `POST /v1/ask` · `POST /v1/search` |
| Verification | `POST /v1/auth/verify` · `POST /v1/auth/resend` · `GET /v1/auth/{session_id}` · `POST /v1/auth/end` · `GET /v1/demo/callers` (dev) |
| Traces | `GET /v1/traces[?outcome=refused]` · `GET /v1/traces/{id}` · `POST /v1/traces/{id}/feedback` (correct / wrong, from testing) |
| Evaluation | `POST /v1/evals/run` · `GET /v1/evals` · `GET /v1/evals/{id}` · `GET /v1/evals/{id}/report` · `GET /v1/evals/compare?a=&b=` |
| Voice | `GET /v1/voice/providers` · `POST /v1/voice/stt` · `POST /v1/voice/tts` · `POST /v1/voice/turn` · `WS /v1/voice/stream` |
| Ops | `GET /health` · `GET /health/ready` · `GET /metrics` · `GET /v1/stats` |

`POST /v1/ask` returns:
```json
{
  "trace_id": "…", "session_id": null, "outcome": "answered", "intent": "general", "language": "en",
  "answer": "The Zenith+ credit card has an annual fee of Rs 4,999 plus taxes [1].",
  "speech_text": "The Zenith+ credit card has an annual fee of four thousand nine hundred ninety nine rupees plus taxes.",
  "route": null, "reason": null,
  "citations": [{"marker": 1, "document": "CC KB", "version": 1, "section": "CC KB › AU Zenith+ Credit Card › Fees & Eligibility; Joining / Annual Fee", "snippet": "…"}],
  "timings": {"understand_ms": 310, "embed_ms": 60, "dense_ms": 6, "keyword_ms": 5, "rerank_ms": 240, "llm_ttft_ms": 280, "llm_ms": 420, "total_ms": 1050}
}
```
`outcome` is one of:
- `answered`, `clarify`, `refused` (`reason`: low_relevance / not_in_sources / uncited / figure_check_failed);
- `auth_required` (with `auth`: status, masked mobile, simulated SMS);
- `needs_verification`, `handoff`, `smalltalk`, `out_of_scope`, `error`.

### 5. Check accuracy (evaluations)
```bash
uv run brain eval                    # runs evals/golden/v1.yaml (105 questions), prints the summary
uv run brain eval --only en-zenith-fee hi-senior-fd-1y
```
- **Where results go:** the report is a Markdown file in `evals/reports/`, also served at `GET /v1/evals/{id}/report`.
- **What it shows:** pass rate, correct outcome, correct facts, retrieval hit@1/3/5 and MRR, answers blocked by the figure check, latency per step (p50/p95), and cost per question. These are broken down **by language** and **by expected behaviour**.
- **Failures:** every failure is listed with its trace link.
- **Comparing configurations:** change a model or setting in `.env`, restart, run again, then `GET /v1/evals/compare?a=OLD&b=NEW` shows the metric deltas and which questions were fixed or broken.
- **Adding questions:** edit `evals/golden/v1.yaml`, or add a new file and run with `--golden <name>`. The format is documented at the top of `app/services/evals.py`.

### 6. Logs, traces and metrics
- **Logs:** the console shows one readable line per event (`LOG_CONSOLE=json` for JSON on stdout in production). `logs/app.jsonl` has the same events as JSON lines (request, turn, ingestion, job, eval), with `request_id`/`trace_id`, rotated at 10 MB × 5. Long digit runs and API keys are masked.
- **Postgres:** the private database stops when the API stops (Ctrl+C). After a crash or a killed terminal, the next start waits for Postgres's crash recovery automatically.
- **Traces:** `GET /v1/traces/{id}` shows *why* an answer came out:
  - the understanding result;
  - the dense and keyword hits, merged ranks and rerank scores;
  - the chunks given to the model;
  - the raw model output, the check results, and timings.
- **Metrics:** `GET /metrics` exposes request counts and latency per route, outcomes by language, and latency per pipeline stage.
- **Stats:** `GET /v1/stats` gives a quick summary for the last 24 h.

### 7. Try the voice agent
- **UI:** use **Voice note** (record, then send) or **Live call** (continuous two-way; use headphones). Verification works the same in calls: the keypad code goes over the socket as data, not speech.
- **CLI:**

```bash
# Make test audio, then play it into the live call at real-time speed; prints every event with a timestamp
uv run brain say "सीनियर सिटीजन के लिए तेरह महीने की एफडी पर ब्याज दर क्या है?" --lang hi -o data/audio/q.wav
uv run brain voice data/audio/q.wav --out data/audio/reply.wav

# One turn over REST (any audio file; also in requests.http and /docs)
curl -F file=@data/audio/q.wav http://127.0.0.1:8000/v1/voice/turn
```
Example `brain voice` output:
```
   0.64s  speech_started  {"barge_in": false}
   5.27s  user            {"text": "Senior citizen के लिए 13 महीने की FD पर ब्याजदर क्या है?", "stt_language": "hi"}
   ...    agent_text      {"text": "सिनियर सिटिजन के लिए 13 महीने की FD पर ब्याजदर 7.60% p.a. है [5]"}
   ...    turn_metrics    {"stt_ms": 422, "endpoint_ms": 721, "voice_to_voice_ms": ...}
```
You can record your own question (phone voice memo; wav/mp3/m4a/ogg all work) and pass it to `brain voice`.
Try it slow with pauses, fast, in Hinglish, with background noise, or by talking over the agent.

### 8. Call audit (recordings, transcripts, reasons, alerts)
Every conversation (typed, voice notes, live calls) is saved as a **call record**. Open **Call audit** in the UI:
- the call list (search by words, number or call id) and, per call, the recording, the conversation with **why the
  agent said each reply** (what it understood, the source and page match, the figure checks, the grounding level),
  verification steps, interruptions, alerts and the audit trail; **Transcript** downloads the record as text and
  **Save as PDF** prints it.
- **Recordings:** live calls are stereo (caller left, agent right), voice notes are the question followed by the
  spoken reply. Opus, encrypted on disk with AES-256-GCM (`RECORDING_KEY`; a local key file in dev), hash-checked on
  playback, deleted after `RECORDING_RETENTION_DAYS`. Keypad codes are typed, so they are never in a recording.
- **Tamper evidence:** each turn stores SHA-256(previous turn's hash + this turn); the call shows **Record intact** or
  **Record altered** (which turn, and whether it was changed or removed).
- **Grounding level** per answer: high / medium / low from the best cited source's reranker score (≥ 0.5 / ≥ 0.15),
  the figure checks and how clearly the caller was heard. A label for reviewers and alerts: it never changes an answer.
- **Opening disclosure:** every live call starts with "This is AU Small Finance Bank's virtual assistant. This call is
  recorded for quality and security." (English or Hindi), and the call record shows when it was played.
- **Transcripts in the log:** one masked line per caller and agent turn (logger `app.transcript`, with the turn hash).
  Account figures never go to the log: the line says which fields were given.
- **Live alerts** (toast in every page, with a link to the call): the caller asks for a person; the agent is
  struggling (2 unanswered or 3 weakly grounded replies in a call); system health (2+ LLM/STT/TTS errors in 5 minutes).
- API: `GET /v1/calls`, `GET /v1/calls/{id}`, `GET /v1/calls/{id}/transcript.txt`, `GET /v1/calls/{id}/recordings/{rid}`,
  `GET /v1/alerts?after=`. They need `X-API-Key` when `API_KEY` is set.

### 9. Phase 3: actions, fraud, complaints, transfers to a person
The same AI agent handles every call; what changes is what it may do and who it hands over to.
- **Mock core banking** (`adapters/mock_corebank.py`, schema `corebank`): the bank's systems, simulated. Seeded once,
  deterministically, from the workbook (never modified) plus synthetic extensions the sample questions need (branch
  type, salary flag, holds, average balance, charges, merchants and status on transactions, several FDs and loans, EMI
  history, two credit cards, card controls) and 7 demo customers built around the sample questions (the test console
  shows each one's story). The agent reaches it only through the `Bank` port, so real bank APIs - one per department
  if needed - replace this adapter without touching anything else. `MOCK_BANK_FAIL=card_update` demos "system down".
- **Routing**: the one understanding call also returns `action` (block / unblock / channel controls / other, which
  card), `issue` (ATM cash, failed payment, double charge, charge review...), `department` and an exact `detail`
  (minimum balance, auto-renew, foreclosure, lounge...). Code decides what follows; the LLM only classifies.
- **Answer decisions**: answer; `CLARIFY:` with the range when the figure depends on something unsaid (account type,
  card, tenure) - "from zero for Digital up to ₹1 lakh for Royale; which account do you have?"; `CONFLICT` when two
  approved figures disagree for exactly the same thing (offer a specialist, never pick one); `NO_ANSWER`.
- **Card actions the agent may do itself** (verified caller, explicit yes, state read back, reference number): block
  temporarily or permanently, unblock a temporary block, switch debit-card channels (ATM / shops / online /
  tap-to-pay / international), credit card international on/off. **Routed to people instead**: closing an FD, paying
  bills, limits, replacement cards, changing mobile / address, fee reversals, refunds.
- **Yes / no is read in code** (`services/confirm.py`, en / hi / Hinglish): "haan kar do" = yes, "mat karo" = no,
  "wait, which card?" = a question (the agent explains and asks again). No LLM call for these turns.
- **Fraud**: empathy, verify, block first (permanently for a stolen card or unrecognised spend, temporarily if the
  caller isn't sure), then offer a dispute ticket and the Fraud & Risk team (priority 1, 24x7).
- **Complaints**: finds the transaction, says what the bank's record shows (e.g. "the ATM recorded the cash wasn't
  dispensed; it comes back automatically by 4 Jan, and you get ₹100 a day if it's late"), then offers a ticket with the
  team and the date they'll reply by.
- **Transfers**: the agent asks first ("Shall I connect you to our cards team?"); on a clear yes the call goes on hold
  (music, "thank you for waiting" every 30 s) and appears on the **agent desk** (`/desk`) with a brief built from the
  record: verified or not, what was asked, what was already done, why. A person accepts: in a live call their
  microphone is bridged to the caller (and recorded); in a chat they type. Nobody within `HANDOFF_WAIT_S`, or out of
  `DESK_HOURS` -> a callback ticket. Ten teams (`services/desk.py`): Fraud & Risk, Credit Cards, Debit Cards,
  Payments & Disputes, Loans, Accounts & Deposits, Digital Banking, New Accounts & Sales, Priority Banking, Customer Care.
- **New customers**: answered from the KB, then offered a callback to help open the account (a lead ticket).
- **Policy pack** (`seed/service-policies.md`, labelled synthetic): what the KB lacks - failed-transaction timelines
  (RBI), zero-liability rule, card controls, complaint escalation, FD early closure. It never restates a figure the
  KB already has.
- **Pronunciation** (`services/pronunciation.py`): spoken text only - "AU" as "A.U." (English, Hinglish) and "एयू"
  (Hindi), fixed in code (the owner's choice, 6 Nov 2026; no listening page), "Zenith+" as "Zenith Plus", "Mohd." as "Mohammad".
- **Dialogue decisions** (`services/dialogue.py`, ADR 0024): the response strategy for each KB request (direct, product
  overview, overview, full, compare, guided, recommend; the router's `scope` is used when code agrees with it), the "help me choose" flows (savings account, FD, credit card), the
  "shall I check yours?" offer after a general answer, and caller facts with a scope (call or task).
- **Passages sent to the answer model** (`kbmeta.trim_context`): relevance (20% of the best, floor 0.05), product identity,
  no duplicates. Re-tune with `uv run python -m evals.context_replay --grid` (replays saved golden runs on the running
  brain, no LLM calls; the number of items that lose evidence must stay 0). A passage about another product that only
  mentions the named one is labelled "[about X; use only what it says about Y]".
- **Router output repair** (`understanding.repair`): when the provider rejects the router's JSON for a value outside a
  list, code repairs the returned generation (no second call); the lenient retry is only for a cut-off generation.
  `/v1/stats` counts both (`router_repaired_in_code`, `router_retried`).
- **Post-call judge** (`services/review.py`): code checks on every call; a hard-rule failure makes the call critical
  whatever the judge's average. The judge (a different model) scores five dimensions as a phone call is heard, with
  verified quotes. `/v1/reviews/calibration` compares it with people's labels (exact, within one point, judge-minus-person,
  missed critical calls). Treat it as uncalibrated until about 50 calls are labelled.
  Each review also lists up to 5 **findings**: the failure label (20 kinds, e.g. unresolved request, lost context,
  unnecessary verification, poor voice response), who caused it (the agent, a bank system, a gap in the documents, an
  unclear caller, not enough evidence - only the agent's own mistakes lower its scores), what happened, a checked quote,
  why it matters, the fix and where to fix it (router, retrieval, answer prompt, bank answer wording, workflow, code,
  knowledge base, voice). `GET /v1/reviews/summary?days=30` counts the recurring problems across calls (biggest first);
  `/calls` shows it as "Quality across calls" when no call is open. Every finding also becomes a to-do on the fix list
  (`fix_items`, `GET /v1/fixes`, `PATCH /v1/fixes/{id}`, UI `/fixes`). The judge diagnoses; a person decides the changes.
- **Eval fixtures**: `evals/fixtures/context-*.json` (saved golden-run passages) are kept in the repo; the clean slate keeps
  turns that belong to no call.

### 10. Tests
```bash
uv run pytest -m "not live"   # 230+ tests: fake models/voice + throwaway Postgres, ~75 s, no network, no Groq calls (T0)
uv run python -m evals.smoke  # T2: 40 items on the real LLM, deterministic checks, per-class report, replay-cached
uv run python -m evals.benchmark   # T3, rarely: routing, answer decisions (old vs new prompt), journeys (uses a lot of Groq quota)
uv run python -m evals.stt_ab # A/B of the speech-recognition vocabulary hint (~70 small Whisper requests)
uv run pytest -m live         # tests against the real Groq API
brain clean-slate [--yes]     # delete every recorded call, ticket and handoff; the KB is kept; the mock bank is re-seeded
```

**Eval tiers (PHASE-4-PLAN §10).** T0 = the offline tests (every change). T1 = a replay cache: with `LLM_REPLAY_DIR` set (the
smoke runner does this, into `evals/cache/`), each real LLM response is stored keyed by the exact request (model, prompts,
schema, limits), so re-running an unchanged item makes no Groq request and a changed prompt is always a miss. T2 = the 40-item
smoke set (`evals/smoke.yaml`). T3 = the full benchmark. The post-call judge uses a different model (`JUDGE_MODEL`) so its
quota is separate from the agent's.

**Phase 4 behaviour covered by tests:** several requests in one sentence and a "no" that doesn't cancel the next request
(`test_understanding`), the mock bank's auth, idempotency, faults and status mapping (`test_mockbank`), the unrecognised-debit
and lost-card workflows (`test_workflows`), merged lists / status words / small talk / repeat (`test_responses`), variant,
authority and expiry checks (`test_kbmeta`), the post-call review and handover summary (`test_review`), the OTP-in-chat and
injection rules and the clean slate (`test_security`), the fallback model and replay cache (`test_resilience`).
The voice tests drive the real `VoiceSession` with synthetic audio (tones stand in for speech, with a scripted STT). They cover:
- slow speakers with mid-sentence pauses;
- fast speakers;
- "aur..." incomplete endings;
- noise bursts and coughs;
- "hmm"/"haan" backchannels;
- the agent's own echo;
- real barge-in;
- corrections while the agent is thinking;
- no-input reprompt and hang-up;
- the greeting;
- text turns.

The audit tests check that an edited or deleted turn breaks the chain, and that the transcript reads as a call record.
The journey tests check: no card change without a clear yes, the state is read back, "which card?" is asked when
needed, fraud blocks first then disputes and transfers (priority 1), an ATM complaint raises a ticket, a transfer asks
first and reaches the desk (accept, chat, complete), and "I can connect you" offers act on a yes.

The MVP 2 tests cover:
- code required before any data is given;
- the waiting question answered after verification;
- lockout after 3 wrong codes;
- an unregistered number → asked for the RMN;
- the code never stored in traces, sessions or the audit table;
- salary-account inference, filtered searches and "not visible" answers.

## How it works

```
POST /v1/ask ──▶ Conversation (channel-agnostic core)
                  1 understand   Groq gpt-oss-20b, strict JSON  ─ language, intent, standalone question, EN keywords
                  2 route        general → 3 ; personal → verified? answer from bank data : send a code
                                 action/issue/human → transfer offer
                  3 retrieve     dense (bge-m3 → pgvector)  ║ keyword (Postgres FTS)   → RRF → rerank (bge-reranker-v2-m3)
                                 top score too low → refuse (no LLM call)
                  4 answer       Groq gpt-oss-120b, streamed, cited, caller's language, or CLARIFY / NO_ANSWER
                  5 check        cites a source? every number present in the cited sources? else refuse
                  6 trace        everything stored in Postgres; metrics + JSON log
```

### Voice call pipeline (`app/voice/`)
```
caller PCM16 16 kHz, 20 ms frames ──▶ VoiceSession
  VAD        Silero (~3 ms/frame); speech must last 200 ms to count (clicks/noise ignored)
  endpoint   speculative STT after 300 ms of silence, decision after 700 ms (adaptive: slow speakers who pause
             mid-sentence get up to 1.5 s; "and…", "aur…", "matlab…" endings wait longer)
  STT        Groq whisper-large-v3-turbo (no_speech prob + hallucination filter) | Sarvam saaras (code-mix)
  brain      Conversation.turn(): streams the answer sentence by sentence; each sentence is figure-checked
  TTS        sentence 1 is synthesized while sentence 2 is generated; Edge (dev) | Sarvam bulbul (prod)
  player     sends audio paced at real time, only 200 ms ahead, so a barge-in stops it within ~0.2 s
  filler     "ek moment…" only if the first sentence isn't ready after 1.2 s
barge-in   while the agent talks, caller sound must score higher on the VAD (0.65 vs 0.5), must not be much quieter
           than the caller's own voice (< -12 dB = a person or TV in the background), and is ignored in the
           first 0.3 s of agent audio (echo). Then ≥350 ms of it → the agent's volume drops (no silent gap) → STT:
           noise / echo of the agent's own words / backchannel ("hmm", "haan", "ok") → back to full volume
           real words ("ruko", "wait", a new question) → {"type":"clear"} to the client, cancel, answer the new turn
correction caller adds words while the agent is still thinking → merged into one question, old answer dropped
no input   reprompt after 8 s, polite goodbye + hang-up after the second
greeting   the disclosure: an AI assistant, and the call is recorded (also stored as a call event)
recording  both sides captured in memory, encoded to Opus and encrypted when the call ends
```
**WebSocket protocol** (`/v1/voice/stream`, header `X-API-Key` or `?key=` when `API_KEY` is set):
- client → `{"type":"start","language":"hi"|"en","greet":true,"session_id":optional,"caller_number":optional}`, then
  binary PCM16 mono 16 kHz frames (any size; 20 ms recommended). Optional messages:
  - `{"type":"otp","code":"123456"}`: the keypad code;
  - `{"type":"flush"}`: end of speech (push-to-talk);
  - `{"type":"text","text":"…"}`;
  - `{"type":"stop"}`.
- server → binary PCM16 agent audio, and JSON events:
  - `ready`, `speech_started`, `speech_ignored`, `user`, `agent_text`, `reply_done`;
  - `auth`: show the keypad / simulated SMS, or "verified";
  - `barge_in_check` and `interrupted`;
  - `clear` (drop buffered audio now);
  - `turn_metrics`: stt / endpoint ms, `first_audio_ms` (speech end → first sound, including the filler) and `voice_to_voice_ms` (→ first answer audio);
  - `end`.

**Latency** (voice-to-voice = the caller stops speaking → the first agent audio):

| Stage | Typical |
|---|---|
| End-of-turn silence | 0.7 s (by design) |
| STT | ~0.4 s |
| Understand, retrieve and rerank (GPU) | ~1.2 s (an idle laptop GPU is woken while the question is understood) |
| LLM first sentence | ~0.5 s |
| Edge TTS first audio | 0.9–1.7 s (dev only) |

With Edge that's about 3–4 s. Sarvam streaming TTS (~0.3 s) and Bedrock in ap-south-1 bring it down to about 2–2.5 s.
Groq's free tier rate-limits under load (it returns 429, and the retries add seconds), so don't measure latency while an eval is running.

### Code layout
```
app/
  config.py        all settings (env / .env)
  container.py     composition root: which adapter implements each port
  ports.py         interfaces: Parser, Embedder, Reranker, LLM, FileStore, STT, TTS, VAD, Bank
  adapters/        provider implementations of the ports: parsers (docx/pdfplumber/xlsx/text), models (embedder,
                   reranker), llm (OpenAI-compatible: Groq/OpenAI/vLLM…), bedrock (AWS), resilient (fallback model +
                   breaker), replay (eval cache), storage (local, S3/MinIO), http_bank (the Bank port over HTTP, one
                   status table), mock_corebank (the in-process mock, BANK_PROVIDER=mock)
  mockbank/        the bank's systems as a separate HTTP service: cif, cbs, payments, cards, cc, lms, deposits, crm
                   (core.py = tokens, idempotency, faults; systems.py = the routers; server.py = runs it on its own port)
  tokens.py        short-lived signed customer tokens for brain -> bank calls (stdlib hmac)
  services/        understanding (one LLM call -> a list of requests + risk flags), registry (what each request needs:
                   open / verified / confirm / people), state (per-call state), guard (injection + risk screens),
                   kbmeta (variants, authority, expiry), workflows (find the transaction a caller means), smalltalk,
                   review (post-call checks + judge), summary (AI handover summary), maintenance (clean slate),
                   documents (lifecycle), worker (ingestion jobs), chunking, retrieval, conversation,
                   auth (RMN + one-time code), personal (answers from bank data), numbers (figure check +
                   numbers as words), privacy (redaction), sentences, store, evals, audit (hash chain,
                   grounding, alerts, call record), recordings (capture, Opus, encryption, retention),
                   journeys (actions, fraud, complaints, transfers, leads), actions (card actions),
                   confirm (yes / no reader), desk (teams, handoffs, tickets), pronunciation
  voice/           session (turn-taking engine), providers (STT/TTS/VAD adapters), turntaking (text rules),
                   player, phrases, audio
  api/             documents, conversation, auth, calls (audit), desk (agent desk), evals, voice, ops
  migrations/      SQL schema
evals/golden/      golden question sets (YAML)       evals/reports/  generated reports
tests/             unit, API end-to-end, voice scenarios, live Groq checks (tests/fakes.py = test doubles only)
```

**Swapping technology** is a `.env` change, with no code changes:

| Piece | Local / dev | Production | Settings |
|---|---|---|---|
| LLM | Groq | AWS Bedrock | `LLM_PROVIDER=bedrock`, `AWS_REGION`, `UNDERSTAND_MODEL`, `ANSWER_MODEL` (AWS credentials from IAM role / env) |
| STT | Groq Whisper | Sarvam saaras | `STT_PROVIDER=sarvam`, `STT_MODEL=saaras:v3`, `SARVAM_API_KEY` |
| TTS | Edge | Sarvam bulbul | `TTS_PROVIDER=sarvam`, `SARVAM_API_KEY`, `SARVAM_SPEAKER` |
| Files | local disk | S3 (or MinIO) | `FILE_STORE=s3`, `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT_URL` (MinIO only) |
| Database | private pgserver | RDS / Aurora Postgres + pgvector | `DATABASE_URL` |
| Embedder | bge-m3 | same, or any | `EMBED_MODEL`, `EMBED_DIM`, then `POST /v1/reindex` (embeddings stored per model) |

Any other OpenAI-compatible LLM (OpenAI, Azure, vLLM, Ollama) works through `LLM_BASE_URL`.
Adding a new kind of provider takes one class that implements the port in `ports.py`, plus one branch in `container.py`.
The `route` field (`auth_required` / `handoff`) tells Phases B–D what to do next.

### Phase 4 settings (all in `.env`; none is needed for the defaults)

| Setting | Default | What it does |
|---|---|---|
| `BANK_PROVIDER` | `http` | `http` = the bank's systems over HTTP (the embedded mock bank), `mock` = in-process, `off` |
| `BANK_BASE_URL` | empty | empty = start the mock bank inside this process; else AU's gateway or a separate mock |
| `MOCKBANK_PORT` | `0` | `8100` to browse the mock bank's OpenAPI at `/docs` |
| `MOCK_BANK_FAIL` | empty | fault injection, e.g. `cards:lost_response,cbs:latency=400` (kinds: latency, timeout, 5xx, malformed, unexpected_status, lost_response) or `read` / `card_update` / `all` |
| `BANK_SERVICE_TOKEN`, `BANK_TOKEN_SECRET` | random per process | brain -> bank auth; set both when the bank runs separately |
| `JUDGE_MODE` / `JUDGE_MODEL` | `all` / `llama-3.3-70b-versatile` | post-call review: `all` (development), `selective` (production), `off` |
| `LLM_FALLBACK` | empty | `{"openai/gpt-oss-120b": "<model>"}`: fallback on 429 / outage. Enable only after that model passes the smoke set |
| `LLM_REPLAY_DIR` | empty | evals only: cache every LLM response on disk |
| `STT_VOCABULARY` | `extended` | `base` / `extended` / `none` hint for speech recognition (A/B tested) |
| `DEMO_TODAY` | today | the mock bank is seeded relative to this date |

Pronunciation of "AU" is fixed in `services/pronunciation.py` (`SPELLING`). The old `data/pronunciation.json` is no longer read.

## Deliberately not done (yet)
- **A separate ingestion process.** Parsing a large PDF in the background thread can briefly hold Python's interpreter lock while a call is live. A separate process would need its own copy of the embedding model (+1.1 GB GPU memory, +30 s startup) and a third command to run. Locally, ingestion rarely overlaps with calls, so it isn't worth it yet. In production, run the API and an ingestion worker as separate containers from the same code (`build(start_worker=False)` + `worker.start()`).
- **Provider racing / fallback.** If the first token is slow, start a second provider and keep the faster one. This needs two providers, so add it with Sarvam and Bedrock.
- **Query cache.** Spoken questions rarely repeat word for word, and cached answers go stale when documents change. Personal answers can never be cached.

## Known limitations
- **The KB doesn't cover some sample questions** (FD premature penalty, personal loan documents, salary accounts). The synthetic policy pack fills these for the demo; it is labelled synthetic and must be replaced by the bank's approved text.
- **The KB contradicts itself in places.** For example, the FD maximum is "up to 7.90%" in the KB but 7.75% in the rates sheet, and the Vikaas monthly credit limit is ₹1 lakh vs ₹5 lakh. The agent says it sees two figures and offers a specialist instead of picking one; fix it in the source documents.
- **Hindi/Hinglish `speech_text` keeps digits** (Indian TTS voices read them natively). Only English numbers are converted to words.
- **Large PDFs** (hundreds of pages): read page by page with memory freed; running headers/footers and contents
  lines are removed so they don't fill passages; embedding runs in batches and a live question waits for at most one
  batch, not the whole file. Uploads up to `MAX_UPLOAD_MB` (100).
- **PDFs must have a text layer.** Scanned PDFs are rejected; production would add OCR (e.g. Amazon Textract). PPTX and HTML aren't supported (convert to PDF or DOCX).
- **Bank data is a mock.** The core-banking schema is seeded from the workbook plus synthetic extensions; values the workbook lacks (merchants, holds, EMI history, second cards...) are generated, deterministic, and labelled simulated. Real bank APIs replace `adapters/mock_corebank.py`.
- **OTP delivery is simulated** (`OTP_DELIVERY=simulated`). Production needs an SMS gateway, and must set `DEMO_CALLERS=false`.
- **Transfers reach a web agent desk**, not a phone queue: in production the contact centre (Cisco) holds the waiting list and plays the hold music; the brief and the record stay the same.
- **Other Indian languages**: English, Hindi and Hinglish today; Sarvam's speech models add 10 Indian languages in production.
- **Edge TTS is for development only** (unofficial, no SLA, adds ~1 s). Use Sarvam in production.
- **Groq free tier** rate-limits (429) under load; production uses Bedrock or a paid tier. Retries get a total budget (`LLM_RETRY_BUDGET_S`, 4 s); after that the caller hears "I'm having trouble right now… I can connect you to our team" instead of silence.
- **Echo:** without headphones, laptop speakers can trigger a barge-in. The echo check resumes the agent, but use headphones, or a phone line with echo cancellation.
- **Third-party services:** Groq (LLM + Whisper API), Edge TTS / Sarvam (speech), Hugging Face models (BAAI bge-m3, bge-reranker-v2-m3, Silero VAD; all run locally), pdfplumber (local).
