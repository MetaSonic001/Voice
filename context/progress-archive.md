> **Archived on 22 Nov 2026.** The full progress tracker as it was that day, kept word for word as the project's
> history. It is no longer updated: the current file is [`progress-tracker.md`](progress-tracker.md).

# Progress tracker

Update this file after every meaningful change: what was done, the numbers, what's live and what's next.

## Current phase

**Phase 4 is built and live (6–11 Nov 2026); since 12 Nov the work is production planning.** The brain last changed on
16 Nov (GPU search server, B2.1; off by default); the owner's running brain uses the 11 Nov code (restarted 13 Nov). The clean slate ran on 6 Nov. Since 12 Nov:
- deployment design and cost estimate (documents only);
- the browser echo fix (ADR 0012, formerly 0028);
- tunnel hostnames allowed in `ui/next.config.ts`.

**What is left, for the local demo and for AWS: [`pending-work.md`](pending-work.md).** Start there in a new session.

The Phase 4 plan, `ROADMAP.md`, the deep dive and the voice-agent playbook were merged into `PROJECT-GUIDE.md` on 14 Nov 2026 (it now lives at `reference/PROJECT-GUIDE.md`) (§31 maps their old section numbers, so `PHASE-4-PLAN §x` references in this log still resolve). The steps below are kept for the record.

## Current goal

Make the agent behave like a good first-line bank representative:
- understands several requests in one sentence;
- keeps context;
- uses realistic separate bank APIs;
- short, human replies;
- correct product variants;
- reviews every call after it ends;
- testing that doesn't hit Groq limits.

## Phase 4 steps (from PHASE-4-PLAN §17)

- [~] **1.** (code done, tests pass; the live clean slate and the KB upload are deliberately done last, see Phase 4 log) Clean slate (§16; delete all recorded calls, keep the KB, re-seed the mock bank); OTP and PII redaction (§9); KB fix: CASA+TD docx "up to 7.90% / 7.40%" → 7.75% / 7.35% as a new KB version (§5.3)
- [ ] **2.** Eval tiers T0–T2: replay cache, 40-item smoke set, judge on a different model (§10)
- [x] **3.** (code done and tested, 6 Nov) Understanding v2 (requests list, scope, risk flags) + call state + task registry (§3.1, §3.2)
- [x] **4.** (code done and tested, 6 Nov; dates relative to today take effect after the clean slate re-seeds the bank) `mockbank` service (separate APIs and schemas per bank system) + `HttpBank` adapter + dates relative to today (§3.3)
- [x] **5.** (code + tests, 6 Nov) Workflows: unrecognised debit, lost card, charge explanation (§3.4, §5.5)
- [x] **6.** (code + tests, 6 Nov; the LLM phrasing call was deliberately skipped, see log) Response generation + style rules + small talk + fragments + fewer confirmations (§4, §6.2, §6.3)
- [x] **7.** (code + tests, 6 Nov) KB document tags + variant, authority and conflict checks in code (§5). The 7.75 vs 7.90 source was checked on 5 Nov: a stale docx, fixed in step 1.
- [x] **8.** (code + tests + UI, 6 Nov) Post-call judge + review UI on `/calls`; desk transcript + AI summary (§11, §12)
- [x] **9.** (code + tests, 6 Nov; vocabulary A/B run) "AU" pronunciation (§8): the owner chose A.U. / एयू; now fixed in code and the `/pronunciation` page is removed; STT vocabulary + garbage rejection (§6.4); fallback model on 429 (§7.3)
- [~] **10.** (UI pass, docs and the partial real-LLM smoke run done; the clean slate and the golden / routing runs are not, see below) Responsive UI pass (§15); docs; final full benchmark with numbers

## Phase 4 log (newest last)

### 6 Nov: step 1, security first (code done; 134 offline tests pass, was 100)

| Item | What was done | Where |
|---|---|---|
| OTP typed in chat (issue M) | While a code is pending, a lone 6-digit message is the code: verified in code, stored as `[code]`, never in turns, history, logs or LLM input. A code heard by speech recognition is refused ("use the keypad"). After a right code the waiting question is answered in the same turn. | `conversation._code_turn`, `auth.awaiting_code`, `privacy.is_code_entry`, `tests/test_security.py` |
| `redact()` | Luhn card, Verhoeff Aadhaar, phone, "account number ..." context, PAN, email, UPI; amounts and "ending 4521" kept; UUIDs no longer mangled in logs. | `services/privacy.py` |
| Guard | Injection screen and risk flags in code (`injection`, `risk_flags`, `instruction_like`). Wired into the turn in step 3. | `services/guard.py` |
| Clean slate (§16) | `brain clean-slate [--yes]` and `maintenance.clean_slate(confirm=True)`; KB and eval history kept. Tested on a throwaway DB. **Not yet run on the owner's data**: it runs last, after my own test calls, and after a preview of what is deleted. | `services/maintenance.py`, `cli.py` |
| KB fix 7.90 -> 7.75 (§5.3) | Corrected copy built in `brain/seed/`: senior FD "up to 7.75%", general "up to 7.35%", senior benefits "7.75%". Source file in `Sample Questions/` untouched. Upload as a new version is pending (needs the running brain's API). | `brain/seed/CASA+TD KB (rates aligned ...).docx` |

Deviation: the **Green FD ("AU Planet First") "up to 7.90%" was left as is**. The 23 Apr 2026 rate card does not mention the Green FD, so changing it would assert something unsupported either way. Added to open questions.

### 6 Nov: eval plumbing, step 3 (understanding v2, state, registry) and step 4 (mock bank service)

| Item | What was done | Where |
|---|---|---|
| Replay cache (T1) | Every real LLM response is cached on disk keyed by the exact request (model, prompts, schema, limits); any change is a miss. On with `LLM_REPLAY_DIR`. Tested. | `adapters/replay.py` |
| Fallback model + breaker (§7.3) | `LLM_FALLBACK='{"primary":"fallback"}'` wraps the LLM: 429/outage -> fallback; 3 failures in 60 s -> straight to the fallback for 60 s. **Off until a fallback has passed the smoke set** (plan rule). Tested with forced 429s. | `adapters/resilient.py` |
| Understanding v2 (§3.1) | One call returns a list of up to 3 requests (kind, own question, personal/action/issue), `risk` flags, `reply_to_pending`, focus. Kinds: general, personal, action, issue, human, new_customer, smalltalk, out_of_scope. `fraud` is made by code from a risk flag (LLM or regex) and goes first. Per-request scope rule in the prompt. **Latency not yet measured on the real LLM.** | `services/understanding.py`, `domain.py` |
| Registry (§3.1) | Kind -> handler + access level (open / verified / confirm / people), enforced in `_dispatch`. | `services/registry.py` |
| Call state (§3.2) | `sessions.state`: open requests (never dropped), done, declined, focus, last list, greeted. A "no" to a question no longer cancels the request waiting behind it. Migration 006. | `services/state.py` |
| Injection guard (§9) | Caught in code before the LLM; no model call. | `services/guard.py` |
| Mock bank service (§3.3) | 8 systems as routers (cif, cbs, payments, cards, cc, lms, deposits, crm), each with its own paths, ids and status words; service token + customer-scoped signed token (stdlib hmac); idempotent writes (409 on a different request, 24 h); fault injection (latency, timeout, 5xx, malformed, unexpected status, lost response). Runs in the brain process on a real socket (`MOCKBANK_PORT=8100` to browse /docs) or separately (`BANK_BASE_URL`). Migration 007. | `app/mockbank/`, `app/tokens.py` |
| `HttpBank` + `HttpServiceDesk` | The `Bank` port over HTTP, 15 calls in 2 parallel rounds, one retry, same-key retry for writes, one status table to the internal words, unknown -> "unknown". A timeout on a write is "unconfirmed", never "failed". Default `BANK_PROVIDER=http`; `mock` still works. | `adapters/http_bank.py` |

Numbers: offline tests 100 -> 164, all pass (the whole journey suite now runs through the HTTP bank). One customer read: **110 ms p50 over HTTP vs 110 ms in-process before** (no worse; simulated 5-25 ms per call).

Deviations: separate Postgres schemas per bank system were **not** built (same tables behind separate APIs; the plan says schemas are for realism, not security). CRM keeps using the same `tickets` table.

### 6 Nov: steps 5, 6 and 7 (workflows, human feel, KB checks). Offline tests 164 -> 214.

| Item | What was done | Where |
|---|---|---|
| Unrecognised debit (§3.4, issues A, K) | The payment is **never guessed**: only matched on amount / period / merchant the caller gave. Nothing matches -> says so and asks when, which account, who; several match -> lists up to 3 and asks which ("the second one" or the amount, read in code); pending -> says it may reverse; reversed -> money is back; card payment -> block offer, then dispute + fraud team; **UPI / IMPS -> no card block**, a fraud complaint, 1930 and the fraud team. Bank down -> still offers the fraud team. | `services/workflows.py`, `journeys.fraud` |
| Lost card | Which card (if two), then **one** yes (lost / stolen = permanent, misplaced = temporary), then what was spent on that card in the last 7 days and an offer to dispute. Deviation: the plan said "blocked straight away"; CLAUDE.md requires an explicit yes, so one confirmation stays. | `journeys._lost_card` |
| Charge explanation (§5.5) | A charge found in bank data is explained from the charges document too (bank facts first, KB how-it-is-worked-out, then the review offer). If the KB has nothing, only the bank facts are said. | `personal._charges`, `conversation._personal` |
| Bank-data answers (§4.2) | Last N across accounts = one merged list, newest first, cut to exactly N, each line with its account ending; read three at a time with "Want the other 2?" ("go on" continues). One status table: "paid" / "received" only after success; pending, failed, reversed and unknown have their own words in en / hi / hinglish. | `personal._transactions`, `_txn_line` |
| Human feel (§4.4, §6.2, §6.3) | Fragments ("so, you know,", "hmm") get no reply; thanks / bye / "oh dear" / "I'm waiting" get one fitting line; the full greeting is said once per call (not again after the voice disclosure); "repeat that" / "phir se bolo" repeats the last answer with no LLM call; an explicit request for a person transfers directly (no "Is that okay?"). The endpointer also waits on filler-only speech. Answer prompt: length fits the question, top 3 then ask, answer exactly what was asked, no unsupported negatives. Replies over 80 words with no question are flagged (`long_reply`), never cut. | `services/smalltalk.py`, `conversation`, `voice/turntaking.py`, `ANSWER_SYSTEM` |
| KB checks (§5) | Product and variant come from the chunk's heading path; document type and authority from title rules (a person can correct them: `PATCH /v1/documents/{id}` `tags`). In code before the answer model: a generic question naming no variant, whose best passage is variant-specific (or mixes variants), becomes a CLARIFY naming the variants (no LLM call, so faster); expired / superseded / instruction-like passages are dropped; a rate question keeps the dated rate card only; each passage is labelled with its type and date. Documents whose text looks like instructions to the assistant are flagged at ingest. Migration 008. | `services/kbmeta.py` |

Deviations and skips (and why):
- **No LLM phrasing call for lists / explanations (§4.2).** It would add 300-500 ms to exactly the slowest turns, spend quota and risk invented words. The failures it targeted (B, C, O) are fixed by the merged list, the status table and three-at-a-time. Revisit only if the owner finds the wording still robotic.
- **"Slower please" (§4.1 rule 8)** is not done: the TTS providers fix the speaking rate at start-up; it needs per-call rate support. "Repeat that" is done.
- Variant gate and rate-card rule are tested on the real KB's heading paths, **not yet measured on the golden set with the real LLM** (T3 at the end).

### 6 Nov: steps 8, 9 and the UI pass. Offline tests 214 -> 235.

| Item | What was done | Where |
|---|---|---|
| Post-call review (§11) | When a live call ends (or `POST /v1/calls/{id}/review`), a background job builds the redacted call packet, runs the free checks (disclosure, verified before account data, no personal data left in text, errors, slow replies, caller repeating, "I don't know" twice, asked for a person, long replies, fallback used) and, per `JUDGE_MODE`, the LLM judge on a different model (5 anchored dimensions 1-5, evidence quotes). **The code verifies every quote against the transcript and drops invented ones**; a critical claim with no real evidence goes to a person. `all` = every call (now, in development); `selective` = failed checks, fraud / action / complaint / transfer, hung up mid-task, plus a 10% sample. People label calls: `/v1/reviews/calibration` shows agreement within one point per dimension, critical-failure recall (must be 100%) and false positives. Migration 009. | `services/review.py`, `api/calls.py` |
| Desk (§12) | The desk shows a one-line headline, the deterministic brief, a figure-checked **AI summary** with the caller's mood (generated in the background after the transfer starts; discarded if any figure is not in the record), and the full read-only transcript. The case record exists before the transfer (the handoff row is created first). | `services/summary.py`, `api/desk.py`, `/desk` |
| Unclear speech (§6.4) | "Sorry, I didn't catch that" only when **two** of three signals agree (low Whisper confidence, repetitive text, not sure it was speech); a second time in a row offers "say agent". | `voice/turntaking.unclear_speech` |
| STT vocabulary (§6.4) | A/B on 20 spoken phrases (Edge voices, clean audio, 3 hints): word errors **none 15.7%, base 15.1%, extended 12.0%**; banking terms right 16 / 16 / 18 of 26; "new wrong substitutions" 0 / 2 / 2 (script changes such as Devanagari "बैलेंस" -> "balance", harmless). **`STT_VOCABULARY=extended` is now the default.** Caveat: synthetic clean audio, not phone calls. One Hindi phrase with "Zenith" came back in Urdu script (language ID), not fixed. | `voice/vocab.py`, `evals/stt_ab.py` |
| "AU" pronunciation (§8) | A listening page `/pronunciation`: 5 spellings per voice, play, choose; saved to `data/pronunciation.json`, applied to speech text only, greeting re-made. **Your 2-minute choice is still needed.** | `services/pronunciation.py`, `ui/app/pronunciation` |
| Latency and reliability (§7) | A verified caller's accounts are read **while the LLM understands the turn** (8 s shared read, dropped after a card change); `/v1/stats` now reports p50 / p95 / p99 for first audio, understanding, bank, embed, rerank, LLM first token and total, plus 429 retries and fallback use; 20 simultaneous sessions tested; a provider-rejected enum value in the understanding JSON is retried once with a lenient schema (found in the real smoke run). | `auth.py`, `understanding.py`, `store.stats` |
| UI (§15) | Score chips, review panel and "needs review" filter on `/calls`; AI summary and transcript on `/desk`; `/pronunciation`. **`/calls` 1,557 px wide bug fixed** (the grid needed `minmax(0, 1fr)`); measured **no horizontal page scroll at 390 / 768 / 1024** on all pages; `/kb` table scrolls inside its own container. `bun run build` and `tsc` pass. | `ui/` |

### 6 Nov: first real-LLM smoke run, fixes, docs cleanup. Offline tests: **247 pass** (100 at the start of Phase 4).

**Smoke run on the real LLM (partial, honest).** 40 items / ~58 turns hit Groq's **daily token limit on gpt-oss-20b (200,000 tokens)** after 45 turns: the v2 understanding prompt is about 2.6k to 3.6k tokens, so the free tier allows only about 60 understanding calls a day (the plan assumed the 1,000 requests-per-day limit was the one that binds; the token limit binds first). The set is now **20 items / 21 turns**. Turns after the limit are errors from the limit, not from the product, and are not counted below. Of the 45 turns that ran, 33 passed; the 12 misses, diagnosed:

| Miss | Cause | Status |
|---|---|---|
| "How do I block my card if I lose it?" and "process to report a lost debit card" treated as fraud | The LLM raised a `lost_card` risk for a how-to question | **Fixed in code**: a risk only the LLM raised is doubted for how-to questions and requests for a person (`guard.doubt_llm_risk`); regex screens are never doubted. Tested offline, not re-run on the real LLM |
| "Please connect me to your fraud team" ran the fraud workflow | Same | **Fixed** as above |
| Generic minimum balance (Hindi, Hinglish) refused, not clarified | The rate card ranked first and hid the variants behind it; the answer then failed the figure check | **Fixed**: the variant gate looks at all passages for non-rate questions (`kbmeta.variant_gate`). Tested offline, not re-run |
| UPI debit of 10,000 "not found", last-5 and "UPI completed" answered from the wrong rows | **Test data artifact**: the live mock bank was seeded with dates up to 31 Dec 2026, but today is 6 Nov, so those rows are in the future and correctly excluded | Goes away after the clean slate re-seeds the bank relative to today |
| Highest senior FD rate answered "7.50%" (the right figure is 7.75%) | The LLM read the wrong row of the rate table; the figure check cannot catch a real figure in the wrong row | **Open**. Same weakness as the golden-set fact accuracy (78%). Needs a better table chunk or a prompt rule; measure on the golden set first |
| Zenith+ explanation over 90 words with no check-in | Prompt style rule not followed by the 120b model | Open, flagged as designed (`long_reply`), never cut |
| "I have an FD with AU Bank" asked to verify | The LLM called it personal | Accepted; the test now allows it |
| "Can I withdraw an FD before maturity?" refused | The KB had no answer that passed the checks | Open, to look at with the golden set |
| 4 items errored with "Generated JSON does not match the expected schema" | The provider rejected an out-of-list enum value | **Fixed**: one retry with a lenient schema |

**Not measured (say so rather than guess):** understanding latency with the v2 prompt, routing accuracy (the tracker's 84% was on the old prompt), the golden set, first-audio latency. They need Groq quota that is not available until tomorrow; the smoke set is small on purpose, run `python -m evals.smoke` (about 21 turns, replay-cached) after the quota resets, then `python -m evals.benchmark routing` once.

**Docs cleanup (6 Nov).** Deleted as stale (they are in git history): `mvp2-plan.md`, `phase-a-plan.md`, `platform-build-plan.md`, `gaps-and-next-steps.md`, `scenario-map.md`, `tech-architecture.md`, `PHASE-3-PLAN.md`. Their still-useful pointers are in the root `README.md` ("Pointers for the future"). Root README rewritten as a map. ADRs 0008, 0009, 0010, 0015, 0016, 0018, 0019, 0020 got "Implementation notes"; **ADR 0023** (no LLM phrasing for bank data) added. `CLAUDE.md`, `PROJECT-GUIDE.md` (§24), `brain/README.md`, `ui/README.md` and the five `context/` files are filled in and current.

### 6 Nov (afternoon): the dialogue layer (ADR 0024). Offline tests 247 -> 255, all pass.

Why: live calls sounded like a Q&A bot (brochure answers with markdown, menus of variant names, "I can't confirm the
rest… specialist?", the same "I'm sorry this happened" for every fraud turn, the same "One moment, please"). Retrieval was
fine. Researched production practice (OpenAI / ElevenLabs / PolyAI voice prompting, Rasa CALM, Pipecat memory) and built
the missing dialogue layer in code. No new dependency; no LangChain / LangGraph.

| Item | What was done | Where |
|---|---|---|
| Response strategy in code | direct / product_overview (40-65 words, then offer one part) / overview / full (explicit "everything") / guided / recommend. Recorded on each turn as `strategy`. Progressive disclosure, not a word cap: the target is in the prompt; going 25% over is flagged (`long_reply_target`); a safety limit (6 sentences / 110 words) stops only a runaway answer, at a sentence boundary, never for `full`. | `services/dialogue.py`, `conversation._answer` |
| Answer prompt rewritten | A customer-service persona: answer first, short spoken sentences, at most one question, no lists inside sentences, conditions stated honestly, uses the last exchange and the caller details. The answer step now gets the last exchange, the strategy line and only the facts relevant to the product. | `ANSWER_SYSTEM` |
| Guided flows | "I'm interested in credit cards", "which account suits me", "best FD for me": one question per turn that changes the answer (spend, travel / purpose, balance / tenure, senior), skipping known ones; then a KB recommendation of one or two with reasons, then a callback offer to apply. No verification. | `dialogue.FLOWS`, `conversation._guided/_flow/_recommend` |
| "Shall I check yours?" | After a general answer that could differ for the caller's own product (router flag `own_check`: FD early closure, renewal, foreclosure, minimum balance, fee waiver), the agent offers to check theirs. Only a yes starts verification. | `dialogue.own_offer`, `conversation.turn` |
| Variant question | A generic question that depends on the variant now asks "Do you already have one with us, or are you thinking of getting one?" (with two example names), not a menu of codes. | `variant_clarify` |
| Router v3 | New per-request fields `goal` (info / choose), `product`, `own_check`; `reply_to_pending` gains `answer`; facts = only what this turn says (code merges); duplicate requests merged ("human, human"). Prompt 9,233 -> 7,843 characters. | `understanding.py`, `domain.py` |
| Caller facts v2 | Call facts (senior citizen, occupation) last the call; task facts (tenure, amount, fd_type, purpose, balance preference, monthly spend, travel) end when the product family changes; only relevant facts go to the answer model. Turn number and confidence were not added (not needed; the model's confidence is not reliable). | `dialogue.merge_facts` |
| Fraud | "Connect me to your fraud team" = transfer, no OTP (`guard.asks_for_team`); OTP regex catches "OTP kisi ko bata diya" and Hindi; openings fit the report and are said once (not again after the code); before the code the caller hears "If you'd rather speak to our fraud team straight away, just say so"; OTP shared -> what went out in 2 days + fraud team offer; a vague "money was taken" -> the latest 3 debits and "is it one of these?" | `guard.py`, `journeys.fraud`, `workflows.recent_debits` |
| Honest bank answers | "Last 5" on an account with 1 transaction says "There's only one transaction on the account ending 4512" and that it is inactive; inactive accounts are marked in "which account?"; "the other one" resolves against the account in focus. | `personal._transactions`, `conversation._personal` |
| Voice-safe text | `clean_markup` removes bullets, headings, bold, code ticks and emojis before screen and speech; amounts, ranges, rates and "ending 4521" kept. Citation markers were already removed from speech. | `numbers.clean_markup` |
| Partial answers | A failed figure check after checked sentences keeps them and says "I'd rather not guess the rest… Is there something specific you'd like to know?" (outcome answered, reason `partial_…`), no transfer pushed. | `conversation._answer` |
| Waiting lines | Four per language, rotated, never the same twice in a row; still only when the answer is slower than 1.2 s. | `voice/phrases.py`, `session._filler_after` |
| Small fixes | Post-call judge model `llama-3.3-70b-versatile` no longer exists on Groq (404, no review ever ran) -> `openai/gpt-oss-20b` (different from the agent's 120b, own quota). `/pronunciation` page, API and `pronunciation.json` loading removed; spellings fixed in code. | `config.py`, `pronunciation.py` |

**Real-LLM check (free Groq, 120b, 10 calls, not replay-cached).** Router v3 on 6 key turns: **6 / 6 right** - fraud-team
request = human, no risk, department fraud; "OTP kisi ko bata diya" = otp_shared; "Can I withdraw an FD before maturity?" =
general, fd, own_check true; "I'm interested in credit cards" = choose, credit_card; "The first one" after "which account?"
= personal, transactions, count 5, Digital 4512; a flow answer = reply_to_pending "answer". Router latency 1.2-1.8 s,
prompt 3,017-3,060 tokens, output 269-449 tokens. **The output allowance must stay 800: 600 truncated the JSON (HTTP 400).**
Answer prompt: FD answer 54 words, 2 sentences, with the tax-saver exception; Zenith+ went from 100 words / 3 dense
sentences to 72 words / 4 short sentences ending "Which part would you like to hear more about?"; no markdown in either.
Not measured: routing accuracy on the full routing set, the golden set, end-to-end first audio (quota).

Tokens, honestly: the router prompt is about 10% smaller, not much less. Its ~3,000 prompt tokens are about 1,900 for the
instructions and about 1,000 for the JSON schema Groq adds; history is small. The answer call is about 750 tokens of
instructions plus the passages (Zenith+ 6,506 characters vs FD 5,231: the 516-token difference is passage content, which
is needed). The free tier's 8,000 tokens a minute still allows only about two turns a minute.

Deviations and skips: history stays at 6 turns (the 3-vs-6 comparison needs the paid tier; facts already carry what
must persist, so the change would only save tokens). No LLM-written summary. Overdraft-against-FD suggestion not added
(needs the bank's approval). Skipped as not needed: a separate "continuation / correction" router field (the
self-contained rewrite plus code account resolution already handle "the first one" and "I meant ten").

### 6 Nov (evening): passages, product identity, answer scope, comparisons. Offline tests 255 -> 261, all pass.

| Item | What was done | Where |
|---|---|---|
| Which passages are sent | Relevance: at least 20% of the best score and at least 0.05 (both). Identity: a passage whose heading is a different named product is dropped unless its text covers the named one. Near-copies dropped. The best passage always stays. The Zenith+ example's 5th passage (another card, score 0.038, "1.99% forex" vs Zenith+'s 0.99%) is now dropped. | `kbmeta.trim_context` |
| How it was tuned (no LLM calls) | `evals/context_replay.py` replays saved golden-run traces (reranked passages) through the rules and checks the golden `facts` / `sources` are still in what would be sent. 3 runs, 77-78 items each: **0 items lost evidence** at 0.20 + 0.05 with identity; passage text sent **-22% / -23% / -24%** (3,568 -> 2,792 average characters on the main run). A 0.30 share lost `en-fd-2y-regular`; a 0.10 floor lost `en-online-card`; the first identity rule (heading only) lost `en-digital-debit` (answered by the Kids account's comparison table) - fixed by also accepting a passage whose text names the product. | `evals/context_replay.py` |
| Answer scope from the router | New per-request enum `scope` (specific / overview / full / compare / choose) and `named` (product names said); `goal` is now derived from scope. Code accepts compare only with 2+ named products and full only with all / everything / poori / सभी words; otherwise its own patterns decide (kept as fallback). | `understanding.py`, `dialogue.strategy` |
| Comparison mode | "compare" strategy; every named product needs a supporting passage; a missing side is said by code ("I don't have the details for Altura to hand..."), and the model is told to leave it out. | `conversation._answer`, `kbmeta.missing_products` |
| Tests | Scope rules (router + sanity checks), patterns in English, Hindi and Hinglish ("ke baare mein", "poori jaankari", "सभी शुल्क", "all the charges", "and its forex?", "lounge benefits"), trimming (other product, weak, duplicate, Zenith vs Zenith+, recommend keeps all), comparison completeness. | `tests/test_dialogue.py` |

**Token split, real counts** (gpt-oss tokenizer `o200k_harmony`, run once with `uv run --with tiktoken`, not a project
dependency): router instructions 2,068; router schema as JSON 1,352 (Groq reports about 3,000-3,190 prompt tokens per
router call, so it sends the schema more compactly than raw JSON); router user message 10 (no history) to 140 (6 turns +
facts); answer instructions 715; answer passages per question **972 -> 767 (-21%)** after trimming. History is not the cost;
the router's fixed instructions and schema are.

**Real-LLM check (free Groq, 120b, 6 calls).** Scope: "Tell me about the Zenith+ credit card" = overview, named [Zenith+];
"Zenith+ card ki saari jaankari dijiye..." = full; "Zenith+ aur Altura mein kya fark hai?" = compare, [Zenith+, Altura].
**"What's the difference between Zenith+ and Altura cards?" breaks the strict schema every time: the model writes
`kind: "compare"`.** Adding "comparisons" to the general kind and putting `scope` before `kind` did not stop it. Fixed
without a second call: Groq returns the rejected generation; the adapter now keeps the full error body (it was cut to 500
characters), and when that generation is complete JSON, code repairs it (`understanding.repair`). Unknown kinds are mapped
by what the request says (personal fields -> personal, an action -> action, "agent" -> human, "complaint" -> issue,
"compare" -> general), never blindly to general. Real check: repaired in code, **1.9 s, no retry**, -> general / compare /
[Zenith+, Altura]. The lenient retry stays only for a cut-off generation. Both are counted in `/v1/stats`
(`router_repaired_in_code`, `router_retried`) and logged with the invalid field. (The 34 s seen earlier was the test
script's own 32 s pause for the free tier, not the retry.)

Passages that are about another product but mention the named one (the Kids comparison table for a Digital question)
are labelled for the answer model: "[about Kids; use only what it says about Digital]", so one product's benefits are
never attributed to another. Primary product = the chunk's heading; mentions = names in its text (derived, no new
metadata).

Not done (agreed to wait for the paid tier or a spare quota day): router schema trimming, a routing-set run, latency
p50 / p95 over repeated runs, the scenario suite on the real model.

### 6 Nov (late): cleanup and the judge. Offline tests 261, all pass.

- Removed leftovers: the unused `GOALS` constant, the dead Hindi "AU" lexicon rule, `data/pronunciation.json` (it held
  exactly the spellings now fixed in code). The smoke set is now 24 items: K1 expects the new "is it one of these?" wording,
  and 4 items cover ADR 0024 (FD rule then offer, card discovery, OTP shared in Hinglish, Zenith+ overview length).
- Bank APIs: no LLM tool calling, by design (ADR "LLM understands; code routes"). Code reads the caller's data over the
  mock bank's HTTP APIs and picks what to say. Every mock bank endpoint is used except `GET /payments/v1/txns/{utr}`
  (payment lookup by reference; account transactions already carry the status).
- Post-call judge: it had **never run** (the old model returned 404). Now on `openai/gpt-oss-20b`; the judge also gets the
  cited passages' text (it only saw their names, so it could not really check correctness), and the rubric names robotic
  replies (repeated apology or filler, lists read out, brochure answers). **Not yet run on a real call**: needs the brain
  restart, then `POST /v1/calls/{id}/review`. Calibration: 0 calls labelled by a person so far.

### 6 Nov (night): point 2 done - brain restarted on the new code, clean slate run

- Backup first (the clean slate cannot be undone): `data/pg` and `data/recordings` copied to the session scratchpad.
- Clean slate deleted 183 sessions, 597 turn records, 82 call events, 9 alerts, 119 auth events, 14 recordings, 2 handoffs,
  2 reviews; re-seeded the 14 mock bank tables (dates now relative to today). KB and eval run summaries kept.
  **Side effect:** the turn records behind the saved golden runs were deleted too, so `evals.context_replay` needs a fresh
  golden run before it can be used again.
- KB: CASA+TD KB is already on version 2 with the corrected rates (7.35% present, 7.40% gone; the one 7.90% left is the
  Green FD row, kept on purpose). No new upload was needed. The dummy workbook's only indexed chunk is the account-type ->
  debit-card mapping (no customer data).
- Brain started (launch config "brain"): 7 documents, 220 chunks. Live check: "Can I withdraw an FD before maturity?"
  -> answered with the tax-saver exception + "Would you like me to check this for your own FD?", no OTP; 3.6 s total
  (router 1.9 s, rerank 0.75 s, first token 0.58 s). **The post-call judge ran for the first time** (gpt-oss-20b): score 5.0,
  quotes verified. Note: it rated a 55-word one-sentence answer 5/5 for conversation - lenient; calibration will show.

### 6 Nov (night, 2): judge tightened, eval fixtures kept, bank-read design. Offline tests 262, all pass.

| Item | Decision | Where |
|---|---|---|
| Judge: spoken-response rules | Done. The conversation dimension now judges each reply as heard once: answer first, one idea per sentence (several facts packed into one long sentence is marked down even if correct), brief, at most one clear follow-up. | `review.RUBRIC` |
| Judge: critical overrides the score | Done. A failed hard code check (account data before verification, personal data left in text) makes the call critical even if the judge's average is 4.8. Tested. | `review.ReviewService.review` |
| Judge: calibration measures | Done. `/v1/reviews/calibration` now reports exact agreement, within one point, judge-minus-person per dimension (above 0 = too generous), critical recall and the missed critical calls. Target: about 50 labelled calls of every kind, re-checked after any prompt or model change. **0 labelled so far; treat the judge as uncalibrated until then.** | `review.calibration`, `ui/lib/api.ts` (type only; no new screen) |
| Eval fixtures outside the call database | Done. The clean slate now keeps turns that belong to no call (eval runs); the three golden runs' passages were recovered from the backup into `brain/evals/fixtures/context-*.json` (262 items, about 4 MB, KB text only) and `evals.context_replay` reads them with no brain and no LLM (same numbers as before: 0 lost, -22 to -24%). `--run <id> --save` snapshots a new run. | `maintenance.clean_slate`, `evals/context_replay.py` |
| Selective bank reads | **Designed now (ADR 0025), built with the real AU adapter.** Building it on the mock would cost effort before the real API catalogue is known and gives nothing on a local mock (110 ms). Today's safeguards stay: the model never picks an API or sees bank data. | `docs/adr/0025-selective-bank-reads.md` |
| Router token trim and the comparison wording | **Deferred.** Any router prompt or schema change needs a routing-set run on the real model to prove accuracy did not drop (the "nothing gets worse" rule), which takes most of a free-tier day. The in-code repair stays as a fallback and is counted in `/v1/stats`. Do it with the paid tier: baseline routing run first, then trim (duplicate detail list, facts only when stated), then the same run again. | - |

### 6 Nov (night, 3): evals folder cleanup

- Deleted: `evals/cache/` contents (52 replay files keyed on prompts that all changed today, so they could never be reused;
  the cache refills itself), `evals/reports/smoke-run.log` and the two partial smoke reports of 6 Nov morning (pre-change;
  their numbers are recorded above). Kept `reports/benchmark-20261105-123641.json` (the 84% routing baseline).
- Fixtures trimmed to the top 5 passages per question (the replay only uses 5): 4.1 MB -> 1.4 MB, same results (0 lost).
- Tests: none deleted. All 23 test files cover different behaviour; `test_live_llm.py` (2 tests) only runs with `-m live`.

### 6 Nov (night, 4): judge findings and quality across calls. Offline tests 264, all pass.

| Item | What was done | Where |
|---|---|---|
| Repeated evidence | The judge quoted the same whole reply for every dimension; code accepted each copy (all real). Now: rubric asks for a short (3-15 word), different phrase per dimension; code merges a repeated quote into one with all its dimensions; the UI shortens long quotes. Re-run on the FD call: 4 distinct quotes; conversation 5 -> 3 with "avoid packing multiple facts into a single long sentence". | `review.verify_evidence`, `review-panel.tsx` |
| Findings | Up to 5 per call, from the same single judge call: label (20 kinds: wrong fact, missing / unnecessary information, unresolved request, repeated question, lost context, wrong intent, missing / unnecessary verification, wrong refusal, missed / unnecessary / bad handoff, unsafe action, ignored risk, poor voice response, too long, wrong language, slow, other), source (agent / bank system / document gap / unclear caller / not enough evidence), what happened, quote (checked; an unverifiable quote is blanked, the finding kept and marked), why it matters, the fix, where to fix it. Only the agent's own mistakes lower its scores. The call's labels are derived in code from the findings. | `review.RUBRIC`, `JUDGE_SCHEMA`, `verify_findings`, `categories_of` |
| Quality across calls | `GET /v1/reviews/summary?days=30`: reviewed / judged / critical / needs-review counts, average per dimension, recurring problems biggest first (calls affected, how many the agent caused, an example with its fix and fix area), problems by source and agent problems by fix area. Shown on `/calls` when no call is open. | `review.summary`, `api/calls.py`, `quality-summary.tsx` |
| Not added (on purpose) | No separate voice-experience score (kept inside conversation), no second judge, no per-dimension calls, nothing changes the agent automatically. | - |
| Speech pauses | Spoken replies pause at a semicolon as at a full stop; the answer prompt now says one idea per sentence. | `numbers.speech_text`, `ANSWER_SYSTEM` |

### 7 Nov: live text test (10 calls, 7 customers + unknown callers) - bugs found and fixed. Offline tests 267, all pass.

First run (6-7 Nov, 34 turns): the computer slept overnight, so 2 turns failed on network ("getaddrinfo failed") and 4 on
Groq rate limits - not code bugs. Worked well: balances, last 5 transactions (3 + "want the other 2?"), EMI paid / bounced
with the reason, temporary card block with read-back and a reference, FD maturity, salary / UPI total / money from Rajesh,
Hindi gold-loan EMI, credit-card discovery questions, the fraud opening said once, the OTP-shared Hinglish opening.

| Bug | Cause | Fix |
|---|---|---|
| Senior "FD rate for 2 years" refused | Two NRI foreign-currency (FCNR) rate tables outranked the domestic table (0.059); the new 20% cutoff dropped the domestic table (a regression from passage trimming) | Rate questions: rate-card passages need only the 0.05 floor; FCNR / NRI passages dropped unless asked about. Replay still loses nothing. `kbmeta.trim_context` |
| "Connect me to my relationship manager" answered as the previous fee question | Router took it as a reply | Code: an explicit "connect / talk to + agent / manager / RM / team" is a transfer. `understanding.correct` |
| "Switch on international payments on my debit card" answered as a general question | Router called it general | Code: switch / turn on / enable + card + a payment type is a card control. `understanding.correct` |
| "Debit card not working at shops" treated as a complaint | Router called it an issue | Prompt: a card not working / declined is personal (debit_card, declines) |
| "Don't recognise 8499 on my credit card" listed recent debits | Router left out the amount | The amount is read from the caller's words (never a card ending). `understanding.amount_said` |
| "Why was the annual fee charged on my credit card?" -> "couldn't find one matching that" | Router passed "credit" as the card's name | Generic words are not a product name. `conversation._specific_item` |
| "धन्यवाद" repeated the EMI answer | Router rewrote "thanks" as the last question | Short thanks / bye answered in code, no LLM call. `smalltalk.is_courtesy` |
| FD maturity answer repeated the FD and date | Template | "You'll receive about ₹X then." |

UI: `/calls` hydration warning came from a browser extension adding an attribute to `<html>` (`suppressHydrationWarning` on that
tag only); "audit.summary is not a function" was a stale hot reload (gone after reload).

### 7 Nov (2): card troubleshooting, sources view, turn numbers, fix list. Offline tests 269, all pass.

| Item | What was done | Where |
|---|---|---|
| "My debit card is not working at shops" | Now a check of the caller's card in code (router safety net), not a dispute: says the cause (card temporarily blocked / shop payments off, with any declined payments) and offers the fix ("Would you like me to unblock your card ending 5521?"; shop payments next). A yes goes through the existing read-back + confirm path. Three request types stay separate: information (general) / own lookup (personal) / action - the "switch on international payments" safety net from 7 Nov (1) covers the action case. | `understanding.card_failing`, `personal._debit_card` |
| Balance | "There's nothing on hold" removed: a hold is mentioned only when there is one. | `personal._balance` |
| Sources in the call view | `[1]` markers are hidden in the conversation (kept in the stored record: the checks and the audit trail use them). Each turn has a collapsible "Where the answer came from": document, section, valid-from date, match score, the sentence it supported ("Used for"), the source text; bank answers show which bank records were read. | `ui/components/call-record.tsx` |
| Turn numbers | Every turn on `/calls` is labelled "Turn N" (the judge's numbering); a finding's turn is a link that jumps to it; `/calls?id=...#turn-N` opens at that turn. | `call-record.tsx`, `review-panel.tsx` |
| Fix list | Every finding becomes a to-do (migration 010 `fix_items`): `/fixes` page (To fix / Done / Won't fix / All, "only the agent's own mistakes", counts by where to fix, "Open call · turn N", Mark done / Won't fix / Reopen). A new review of a call replaces its open to-dos and keeps done ones; a to-do survives a clean slate (the call link is cleared). API: `GET /v1/fixes?status=`, `PATCH /v1/fixes/{id}`. | `review._sync_fixes`, `api/calls.py`, `ui/app/fixes` |
| Test calls | Run 2 (8 conversations, today's fixes except the card / balance ones) kept; the 8 older run-1 copies deleted (one copy per conversation); run 1's 2 unique calls kept. Run 2 shows the senior 2-year FD rate now answered (7.60%). | - |
| UI | `/calls` hydration warning from a browser extension silenced on `<html>` only. | `ui/app/layout.tsx` |

### 7 Nov (3): re-judged the kept calls, fixes from what the judge found. Offline tests 270, all pass.

- Kept 11 calls (run 2's 8, run 1's 2 unique, the FD test call); re-judged all with the findings judge: 6 open to-dos on
  `/fixes` (2 too long / packed sentences -> answer prompt, 2 unresolved -> retrieval (Zenith+ forex, BSBDA sweep), 1 poor
  voice response, 1 "annual fee not answered" which was really a Groq rate limit - the judge called it the agent's mistake).
- **False criticals fixed:** the personal-data check flagged the bank reference number (AU + 12 digits) as an account
  number; references are now left alone by redaction (account and card numbers still masked). The "caller repeating
  themselves" check counted the system's own re-ask after verification; that re-ask is now labelled
  (`after_verification_chat` for typed chat) and ignored. Re-review: both calls no longer critical.
- **Real gap the judge caught (Arjun, run 2):** "switch on international payments" on a temporarily blocked card asked to
  unblock first, unblocked, then forgot the request. Now after a successful unblock the agent asks straight away to make
  the change ("... Shall I switch on international use ...?"), one explicit yes each.
- If the router names a card-settings change without saying which setting, code fills it from the caller's words; if
  nothing can be understood the agent asks which setting (never an empty reply).
- UI: "Where the answer came from" is collapsible; the section is hidden when it only repeats the document name.
- Judge consistency: on re-review the Arjun call scored 5.0 with no findings although the gap was real - the judge is not
  calibrated yet (0 labelled calls); treat it as a pointer.

### 7 Nov (4): the re-ask after verification; system failures; long test calls. Offline tests 271, all pass.

- After the keypad code the agent picks the caller's question up again by itself. It was shown and judged as if the
  caller had said it twice ("caller repeating themselves"). Now: `/calls` shows it as a system line ("After verification,
  the agent picked up the caller's question again"); the code check ignores it; the judge sees it as SYSTEM, never a repeat.
  Older calls are recognised by the same question right after a "verify first" turn. `review.is_reask`, `call-record.tsx`
- New finding source `system_failure`: a turn that errored (AI provider, network, rate limit) is not the agent's mistake.
- Two long mixed-language test calls seeded (Ravi 17 turns, Imran 13 turns: English, Hinglish and Hindi in one call;
  accounts, transactions, payments, loans / EMIs, credit and debit cards, charges, card settings, fraud check, complaint,
  FD rule + "check yours?", guided card choice, comparison, transfer to a person). Results below when the run finishes.

### 7 Nov (5): bank calls as evidence, manual test scripts, a voice test call. Offline tests 272, all pass.

- **Bank systems called, per turn.** Every HTTP call the brain makes to a bank system is logged for its turn (system,
  method, path with ids masked to the last 4 digits, status, time, tries) and shown on `/calls` as a collapsible
  "Bank systems called (N calls to M systems)": Customer information, Core banking, Payments, Debit cards, Credit cards,
  Loans, Deposits, CRM. A turn that used data read earlier in the call says so. `adapters/http_bank.log_call`
  (a per-turn context list; the parallel reads run on worker threads with the caller's context).
- **Manual test scripts**: `docs/manual-test-scripts.md` - five long calls (Ravi, Meera, Arjun, Imran, a new customer)
  in English, Hindi and Hinglish, with what to expect and which bank systems each step should touch.
- **Voice test call**: an Indian caller voice (Edge en-IN-PrabhatNeural) speaks a Hinglish script into the live voice
  socket in real time; the keypad code goes as a keypad event. Results below.

### 7 Nov (6): long-call bugs fixed; voice test blocked by the free tier. Offline tests 275, all pass.

Long mixed-language calls (Ravi 17 turns, Imran 13): bank reads, card block, dispute with a reference, credit limit in
Hindi, the guided card choice and the transfer worked. Bugs I found reading the turns (not from the judge) and fixed:

| Bug | Fix |
|---|---|
| "Was my UPI payment to Suresh Patel completed?" answered a credit-card payment | `payment_status` keeps the transactions topic when the router said transactions (`_TOPIC_KEEPS`) |
| Credit-card dues in Hindi / "minimum due" -> "no matching transaction" | A stray keyword no longer turns a card question with a detail into a transaction search |
| "personal loan ki EMI kyun bounce hui?" -> "how much and when?"; "charge for that bounce?" -> "which account?" | Code: an EMI / loan bounce question is the loan's EMI status or bounce charges (not for "how do I..." questions). `understanding.own_data` |
| "mujhe apne account ke charges..." -> a general Royale description | "apna / apne / apni" added to the router's own-data words; code net for "my / apne account + charges / balance" |
| "Which debit card?" answered "yes" -> took the first card | A bare yes / no asks again |
| 1-year regular FD rate -> "up to 7.35%" | Rate-card passages are never cut by trimming for rate questions (the table row scored 0.049) - a second regression from trimming; replay still 0 lost |
| Judge "critical": complaint reference AU-DSP-... seen as personal data | That reference format is left alone by redaction |

Voice test call (Pooja, Hinglish, Indian male caller voice en-IN-PrabhatNeural, live socket, keypad code): **blocked by
Groq limits.** The 120b model's daily 200,000 tokens were used up by the day's long tests; the fallback (20b, now set in
`.claude/launch.json` for the brain only - not in `.env`, not smoke-tested) hit its 8,000 tokens-a-minute cap, which a
call at speaking pace (about 2 turns a minute) exceeds. What the attempts did show: the salary turn worked even though
speech recognition wrote the Hinglish in **Urdu script**; Hindi lines came back in Devanagari; the first line ("Hello,
mujhe ... hai. Mera balance kitna hai?") was **split into two turns** at the pause between sentences, so the agent sent the
code twice (turn-taking: the end-of-turn wait is too short for a two-sentence opening). Not fixed (voice tuning; to discuss).
Next: rerun the voice call when quota is fresh (or on the paid tier).

### 9 Nov: delete a call or a recording; test scripts. (Not tested, not restarted: the owner asked for no tests.)

- `DELETE /v1/calls/{id}` removes a call for good: turns, timeline, verification events, alerts, review, **fix-list
  items**, handoffs and their messages, recordings (rows and encrypted files), the call. Tickets in the mock bank's CRM
  stay (bank records; the link is cleared). `DELETE /v1/calls/{id}/recordings/{rid}` removes one recording (row + file).
  A live call cannot be deleted (409). UI: "Delete call" in the call header and "Delete recording" / voice-note delete,
  each behind a confirm dialog. `maintenance.delete_call`, `RecordingStore.delete`, `api/calls.py`, `call-record.tsx`.
  Needs a brain start to go live; only compiled (Python) and type-checked (UI).
- `docs/test-call-scripts.md`: a script for every test call recorded so far (14) and six new ones (priority customer,
  Hindi gold loan, refund, corrections, safety / edge cases, lost phone and card).

### 9–11 Nov: briefing, outgoing-speech filter, latency metric, two ADRs

**Briefing.** `docs/briefing-security-state-scale.md` covers:
- row-level security, authentication and tokens, hard-coded rules, guardrails, evaluations;
- conversation history and context, Bedrock stateless vs stateful APIs and cost;
- scale from 100 to 1,000 calls at once, deployment, and regulation (DPDP, RBI FREE-AI).

Each point says why, with file and line references and labels: now / before go-live / at scale / not needed.

**Built:**
- **Outgoing-speech filter** (`guard.unsafe_output`, called in `_check_sentence` and on the clarify question).
  - It blocks an answer sentence that asks the caller to tell or share an OTP, PIN, CVV, password or card number, and a web address not in the cited sources.
  - Warnings and statements pass ("never share your OTP", "we'll send the OTP to your mobile").
  - The refusal reason is `unsafe_output`.
- **`/v1/stats` now also reports `voice_to_voice_ms`**, the first audio of the real answer. `first_audio_ms` already existed but counts the "one moment" filler. My briefing first said it was missing; corrected.
- **The India-only Bedrock hint** replaces `apac.` in `bedrock.py`, `config.py` and `.env.example`.

**ADRs:**
- new [0026](../docs/adr/0026-stateless-llm-calls-india-only.md): stateless LLM calls, India-only inference;
- new [0027](../docs/adr/0027-access-control-token-at-bank-rls-with-sso.md): customer data guarded at the bank's APIs; RLS with SSO;
- notes added to 0006 (the filter), 0007 (both steps on 120b in `.env`) and 0021 (per-process secrets and the rate limiter);
- none removed: each records a real decision.

**Numbers:** offline tests **275 → 297 pass** (22 new: speech filter). No Groq calls. Smoke set not run (quota).

**Live:** needs a brain restart.

### 11 Nov: charges question, card call, natural sentences, everyday Hindi

**Charges call** ("मुझे अपने account के charges के बारे में जानना है" answered with Royale figures).

Causes:
- the own-account safety net missed mixed script ("अपने **account**");
- the "which product?" gate fired only for "savings account" / "credit card" wording.

Fixes:
- **Clarify first.** A charges question that names no charge type, product or reason is asked back once, in code (`understanding.broad_charges`): "taken from your account, or our general charges like minimum balance, debit card or ATM?". The pending kind is `charges_which`.
  - "Taken from my account" goes to the caller's own charges, with verification (`wants_own_charges`).
  - Anything else goes to the router with the question as context.
- **Mixed-script own-account regex** fixed.
- **The "which account?" gate** (`kbmeta._GENERIC`) also fires for plain "account" / "minimum balance", so no product is guessed.

**Card call cfdda8da.** Most of it was already fixed earlier: the card check instead of "how much was it?", "switch on international" as an action, the unblock-then-setting order.

The remaining gap is fixed: after a debit-card change succeeds, if the call had a "card not working" check and shop payments are still off, the agent says so and offers to switch them on (`journeys._do_action`, `auth.was_read`). There's a replay test of the whole call.

**Natural sentences in bank-data replies** (`personal.py`), all three languages:
- choosers say "One is your Digital account ending 4512, which is inactive, and the other is your Value account ending 4521. Which one would you like me to check?";
- a transaction is "On Friday, 6 November 2026, at 9:15, there was a UPI transfer of ₹10,000 to Suresh Patel from your account ending 4521, and it's still processing.";
- intros are sentences, not "label:".

**Everyday Hindi:**
- 111 Hindi template lines use common words (अकाउंट, पेमेंट, ट्रांज़ैक्शन, डिक्लाइन, मिनिमम बैलेंस…) instead of bookish ones (खाता, भुगतान, लेनदेन, अस्वीकार, न्यूनतम…);
- the answer prompt's Hindi rule says the same (`PROMPT_VERSION 2026-11-11.1`).

**Seen in call e0f24b03, not fixed:** turn 6 (English) was answered in Hinglish, carried over from turn 5's Hindi.

**Numbers:** offline tests **297 → 316 pass** (19 new: charges clarify, reply reading, mixed script, card call replay). No Groq calls. Smoke set not run (quota): the router and answer behaviour on the real LLM is not measured.

**Live:** needs a brain restart.

### 11 Nov: system deep dive and go-live plan

`docs/system-deep-dive-and-go-live.md`, written for AU's chosen stack (Cisco telephony, AWS with Bedrock, Sarvam speech).

- **Part A, how every part works today:** components, the database (pgserver is a real Postgres process with data on disk, not in-memory), the voice call and brain turn step by step, RAG, auth, the bank adapter, memory, audit, what's dev-only.
- **Part B, the plan:**
  - Cisco integration: SIP from CUBE to a media gateway, DTMF for the OTP, transfer with context as call variables;
  - Sarvam streaming; AWS services and why;
  - what's containerised and how each part scales;
  - capacity from 100 to 10,000 calls at once;
  - 20 failure scenarios;
  - the security plan, rollout phases and a readiness checklist.

Docs only, no code change.

**Added the same day: §B11, every Indian language.**
- Sarvam STT understands all 22 scheduled languages; Sarvam TTS speaks 10 plus English.
- Tiers:
  - Tier 1 (en/hi/Hinglish) is built;
  - Tier 2 is the 9 other Sarvam voice languages, covering about 90% of people together with Tier 1;
  - Tier 3 is understood only: the closest language or a human.
- The code is tied to 3 languages in templates (about 270), safety word lists, digits and dates. The plan is a one-time refactor (about 2 weeks), then about 1–1.5 weeks per language with native-speaker review and an eval gate.
- **No translation step:** the router already writes the English search query in the same call, and the answer LLM writes directly in the caller's language, so there's no added latency.
- **Search:** the embedder and reranker are multilingual and see English. Only Postgres keyword search is English-only, which matters only if AU adds regional-language documents (§B11.5).

**Open questions for AU** (see the doc §B10): peak calls at once, which Cisco product, API gateway catalogue, SMS gateway, retention periods, AWS account rules.

### 12 Nov: deployment design for AWS (documents only, no code change)

- `docs/deployment-options-and-costs.md`: how to read the cost workbook; decisions kept out of it; Cisco notes (internal);
  monolith readiness (§5); load-test plan (§6).
- `docs/architecture-diagrams.md` §3–4 and `docs/diagrams/`: the deployed design; how one app copy serves many calls
  (`call-waiting-and-sizing.png`, Mermaid version alongside).
- **Proposed (not yet ADRs, owner and AU to confirm):**
  - one VPC in Mumbai with ALB + WAF;
  - ECS;
  - a modular monolith (app + GPU search server + staff UI);
  - Aurora (RDS for a pilot);
  - no Redis, SQS or RDS Proxy at launch;
  - Converse API, not Responses (Responses re-bills the stored chat every turn: ~1.7x the LLM cost);
  - Sarvam for speech, gpt-oss-120b on Bedrock Mumbai;
  - own GPUs for search (Bedrock rerank isn't offered in Mumbai and costs more).
- **Measured on the laptop:**
  - Silero VAD 0.92 ms per 32 ms of audio (~2.9% of a core per call);
  - reranking on CPU 20–37 s (so a GPU is needed);
  - first audio of the real answer **3.6 s typical, 5.8 s slow** (22 recorded turns; "one moment" plays at 2.3 s);
  - signing: HMAC 6 µs, ES256 56/132 µs, RSA sign 1.1 ms (keep HMAC for the OTP).

### 12 Nov: echo fix in the browser console (UI only; ADR 0028 supersedes 0013)

- Chrome's echo canceller only subtracts audio from a WebRTC connection; our agent audio played through Web Audio.
  `ui/lib/echo-safe-output.ts` plays it through a loopback of two peer connections in the page and an `<audio>` element
  (console and desk call). It falls back to direct playback with a warning if the loopback can't connect.
- **Verified:** `bun run build` passes; in Chromium the loopback connects (~35 ms) and adds ~46 ms of buffering.
- **Not verified:** real speakers + mic. Owner to run `docs/echo-self-test.md` (pass ≥ 15 dB removed).

### 13 Nov: cost estimate, call volume, rate limits, tunnels, context clean-up

- **Cost workbook** `docs/deployment-costs-and-options.xlsx`: two plain sheets.
  - **Estimate:** calls, usage per call, what runs where, servers (monolith vs microservices), monthly cost, choices within AWS, what to confirm.
  - **Prices:** every price, its value in ₹ and its source, at ₹94 = $1.
  - Covers **100 calls at once only**: monolith ₹29.9 lakh a month, ₹7.97 a call, 75% to Sarvam; microservices +₹24,497 (one more app server).
  - The other `.xlsx` files in `docs/` are older versions.
- **Call volume is a placeholder.** 12,500 calls a day came from treating 100 slots as fully busy, which is wrong.
  - What 100 slots can carry: Erlang B at 1% blocking gives ~84 calls in progress, ~1,260 in the busiest hour, **~10,500 a day**.
  - What AU probably receives: no public figure exists. SBI FY16 (4 lakh calls a day, 30 crore customers) gives 0.48 calls per customer a year; for AU's 1.2 crore customers that's **~16,400 a day**, needing ~150 slots.
  - AU's FY26 BRSR reports 58,652 customer complaints in the year.
  - **Owner to choose** which volume the sheet uses; AU's real data replaces both.
- **Prices checked 13 Nov (AWS price list, Mumbai):**
  - Transcribe streaming $0.01/min (about twice Sarvam);
  - Polly Neural $16 per 1M characters (half Sarvam v3; Generative voices not offered in Mumbai);
  - Sarvam's own models can be self-hosted in our AWS account (Saaras v3: $4/h software fee).
- **Groq 429s on 13 Nov explained** (no code change caused them).
  - The free tier allows 8,000 tokens a minute, about one full turn.
  - A product question makes 2 LLM calls, and a mid-sentence pause re-ran the same question 2 s later.
  - The understanding prompt is the same size as on 11 Nov (3,221 vs 3,222 tokens).
- **Sharing the demo:** UI on ngrok, brain on a Cloudflare quick tunnel, plus `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS`
  (`ui/README.md`). `allowedDevOrigins` added to `ui/next.config.ts`; the UI dev server needs a restart to pick it up.
- **Docker images for AWS ECS (written, not built or run yet):** `brain/Dockerfile` (the monolith: text, voice, audit,
  desk, mock bank, search models baked in; non-root; needs a GPU), `ui/Dockerfile` (static pages on nginx, port 8080),
  root `.dockerignore`, `deploy/ecs/*-task-definition.json`, `deploy/README.md`. No Compose (ECS doesn't use it).
  UI change: `NEXT_PUBLIC_API_URL=/` means "API on the same address" (the ALB routes `/v1/*` to the brain); `tsc` passes.
  Limit: run exactly one brain task until pending-work B2.1-B2.3 are done.
- **Context clean-up so a new session works:** `context/pending-work.md` (new); ADR 0028; CLAUDE.md, this file,
  `architecture.md`, `project-overview.md`, PROJECT-GUIDE §21 and the go-live checklist corrected where they had gone stale.

### 14 Nov: one project guide (documents only, no code change)

- `PHASE-4-PLAN.md`, `ROADMAP.md`, `reference/voice-agent-playbook.md` and `reference/system-deep-dive-and-go-live.md`
  merged into the guide, then deleted. The deep dive had never been committed, so it is not in git history; its content
  is guide §26–27. The guide now lives at `reference/PROJECT-GUIDE.md` (moved by the owner).
- Guide: text and tables only (diagrams stay in `docs/diagrams/diagrams.md`); §1–24 keep their old numbers so ADR
  references still work; new §25 history, §26 go-live, §27 languages, §28 other voice agents, §29 research to act on,
  §30 open questions, §31 where each old section went. Stale facts corrected (316 tests, card actions built, file list).
- README, CLAUDE.md, `context/` files and `brain/README.md` point to the guide. ADRs not edited (accepted ADRs are never
  rewritten); their `PROJECT-GUIDE.md` §n citations still match the section numbers.
- Found: `docs/echo-self-test.md` (pending-work A4) no longer exists.
- No ADR: a document reorganisation is not an architecture decision. Nothing committed.
- **ADRs merged (owner's request: no unnecessary ADRs), 28 → 24 files:** 0013 (echo not fixed, superseded) and 0028
  (echo loopback) folded into **0012** (browser test audio: WebSocket transport + loopback playback); 0023 (bank-data
  templates) folded into **0024** (how the agent replies); 0001 (record ADRs) folded into `adr/README.md`, which now also
  says "keep the set small; removed numbers are never reused". 0001, 0013 and 0023 are in git history (under the old
  `docs/adr/` path); 0028 was never committed, so its full text lives on only in 0012. References in the guide, READMEs, context files, the
  briefing and ADR 0025 updated; older log entries above keep the old numbers (see the ADR README for the mapping).
  Reviewed the other 24 and kept them: each records a separate decision (e.g. 0021 Redis/Kafka vs 0026 stateless LLM;
  0009 ports vs 0025 selective reads, a different status: design only).

### 15 Nov: cost workbook v2, second pass, now a Google Sheet (documents only, no code change)

- The workbook now lives in Google Sheets: "AU voice agent - AWS and Sarvam cost estimate (v2)"
  (https://docs.google.com/spreadsheets/d/1bl04HOma47wsYDxGEdWE0NoDE1GZVsdAPkay-FVYXuU). The owner's original sheet is
  untouched. The local `docs/deployment-costs-and-options-v2.xlsx` is now out of date.
- **Simplified:** the Servers sheet keeps only the processor working (9.5 → plan with 9 calls per container). The
  LLM-connection and helper-thread limits (about 20 calls) are a note, because they don't change the answer. The
  microservices sizing moved to one typed estimate on Choices. "Copy" is now called "container". Cost lost the
  microservices summary rows and gained a "What exactly we use" column. The Estimate check now reads busiest-hour
  calls → call minutes ÷ 60 → calls at the same time on average.
- **Found in the code:** `app/voice/session.py` already sends only the caller's speech to speech-to-text: the VAD cuts
  utterances, and Sarvam REST `saaras:v3` gets one request per utterance. TTS is one request per sentence. The estimate
  still counts the whole call (an upper limit); the 40% case is on Choices (saves ₹3.69 lakh a month).
- **Sarvam plan is now Growth, not Pro.** Pro allows 100 STT and 200 TTS requests a minute; at 100 calls at the same
  time we need about 300 and 600. The Growth fee of ₹49,999 a month comes back in full as credits, so it adds ₹0. Sources:
  sarvam.ai/api-pricing and the Sarvam docs pricing page. The docs show Bulbul v4 Flash at half the pricing-page rate;
  still to confirm.
- **Result:** ₹28,59,913 a month, ₹7.63 a call, ₹1.91 a minute; Sarvam 69.3%, Bedrock 15.3%, other AWS 15.4%.
  4 app servers, 16 containers (6.25 calls each at the busiest moment, at most 9). All formulas checked with a script
  (0 errors), and the values were read back from Google Sheets.

### 15 Nov: cost workbook v2 (documents only, no code change)

- New `docs/deployment-costs-and-options-v2.xlsx`, built from the owner's Google Sheet (that sheet and the old local
  file are untouched). Sheets: Estimate, Servers, Cost, Choices, Negotiation, Storage, Prices, Answers, DB_Tables.
  Every formula is one step (×, ÷, +, −, SUM); no ROUND, lookups or IFs. Yellow cells are typed inputs.
- **Servers now worked out, worst case:** three limits per copy (processor 9.5 calls, LLM connections 20, helper
  threads 21.6), plan with 9 calls per copy → 11 copies → 4 c7i.2xlarge app servers (3 needed + 1 spare), 36 calls per
  server. The old "2 servers, well over 100 calls each" assumed the voice detector could use every core; it can't
  (one main loop per process). The VAD already runs on every frame, so barge-in is in the 0.92 ms figure.
- **Fixed in the numbers:** recordings were priced at Standard-IA ($0.0138) under the name S3; now S3 Standard 1 month
  then Glacier Instant Retrieval. Added data leaving AWS (audio to Sarvam and to AU's Cisco), disks, monitoring, ECR and
  a VPN to Cisco (to confirm). Staff screens moved to Fargate (as in `deploy/README.md`). Sarvam rates moved to the Pro
  plan (pay as you go allows only 20 calls at once).
- **Result at 100 at once / 3,75,000 calls a month:** ₹29,95,851 a month, ₹7.99 a call, ₹2.00 a minute; Sarvam 70.7%,
  Bedrock LLM 14.6%, other AWS 14.7%. Storage and traffic ₹0.29 a call (was ₹0.06). Microservices: 7 app servers,
  +₹74,264 a month.
- Not verified: no Excel recalculation available; all formulas were evaluated by a script (0 errors) and totals
  cross-checked. Bedrock gpt-oss-120b Mumbai price, internet egress, Glacier read price and Cisco audio format are
  still "to confirm".
- Found and left for the owner: the old Answers sheet said Responses costs ~2.3x the LLM bill; this tracker and the
  costs doc say ~1.7x. The new Answers sheet gives no multiple.

### 16 Nov: GPU search server (pending-work B2.1)

The embed and rerank models can now run in a separate process, so brain copies on AWS don't each need a GPU.
- `SEARCH_PROVIDER=local` (default, the laptop demo): unchanged. The brain loads both models itself; the code only moved
  into `container.build_search()`.
- `SEARCH_PROVIDER=http`: the brain calls `python -m app search` (`app/search_server.py`, port 8200) through
  `adapters/search_http.py`. Same model classes, so vectors and scores are identical (checked bit for bit).
- Safety: bearer token (`SEARCH_TOKEN`), and the server refuses to start on 0.0.0.0 without one; it never logs texts. The
  brain refuses to start if the server runs different models (name or size). If the server is down, the brain still
  starts; product questions get the "system problem" reply with a hand-over, everything else works. `/health/ready`
  shows `search` but doesn't fail on it.
- Docker: one Dockerfile. `au-search` = the default build with the `search` command; the brain can be built without
  models (`--build-arg SEARCH_MODELS=0`). `deploy/ecs/search-task-definition.json` and the steps in `deploy/README.md`.
- No new libraries (FastAPI, uvicorn, httpx were already used).

**Numbers:**
- Offline tests **316 → 323 pass** (7 new, `tests/test_search_server.py`).
- Latency on the RTX 3050, same model objects, 60 interleaved runs: embed one question p50 47.1 → 54.0 ms
  (p95 70 → 76); rerank 16 passages p50 230.6 → 236.0 ms (p95 236 → 241). **About +12 ms per question in http mode**,
  most of it local HTTP and JSON on Windows. Local mode: no change. Not measured on AWS yet.
- Smoke set (local mode, the default): **19 / 24 passed (79.2%)**, `evals/reports/smoke-20261116-1111*.md`. About 40 real
  Groq requests in total (the cache was stale from the 11 Nov prompt changes); a repeat run made 1. The 5 misses are
  understanding and dialogue choices (A1, A3 multi-request kinds; G1 variant clarify; R5 "how do I block" treated as an
  action; D1), not search. There is no earlier smoke run on the 11 Nov code to compare with, so this is the new baseline.
- **Found, not fixed:** `python -m evals.smoke` crashes on Windows while closing the Groq client at the very end
  ("Event loop is closed"), so it never writes its report. The results were saved with a throwaway wrapper.

**Live:** nothing changes for the running demo (local is the default). A brain restart picks up the new code.

### 16 Nov (2): capacity, barge-in and the job queue written up (documents only, no code change)

The manager's point: Timelines A and B ignored barge-in. Checked against the code and the cost sheet, then written into
the guide and diagrams.
- **Barge-in is already in the CPU numbers.** `voice/session.py` runs the voice detector on every 32 ms frame for the whole
  call, also while the agent speaks. "Always listening" is about 3% of a core, not 100%. Barge-in adds one STT request
  and a decision per possible interruption.
- **Sizing now follows the cost sheet** (v2, Servers sheet): 6.3% of a core per call on AWS (worst case), 60% of a core
  at most, **9 calls per container** (one process, 2 vCPUs), 4 servers for 100 calls (3 + 1 spare), 13 for 400. The old
  diagrams text (25 per vCPU, 36 per container, 3 servers for 400) is replaced.
- **No Redis/SQS "watchman"**: a queue can't recognise speech, and 12,500 audio chunks a second (400 calls) through a
  queue costs more than the detector itself.
- **Correction:** the Postgres `jobs` table handles only document ingestion. The post-call review is an in-process task
  (lost if the container stops), and the worker's start-up reset would requeue another copy's running job. Both added
  as pending-work B2.13.
- **Files:** `reference/PROJECT-GUIDE.md` §14 (review wording), §26.2 (more pre-pilot changes; Redis conflict), §26.4
  (Sarvam rate limits; why streaming waits and when), §26.6 (CPU per call, what the old sizing got wrong, the watchman,
  the job queue, code needed before the pilot), §26.7 (two failure rows), §26.10, §30, §32;
  `docs/diagrams/diagrams.md` §7–8 (both timelines redrawn: listening all call, REST STT after a pause, an interruption,
  a burst collision; new sizing); `reference/call-waiting-and-sizing.md` (same; `call-waiting-and-sizing.png` is now out
  of date); `context/pending-work.md` (A12 laptop load-test harness; B1.7; B2.4; B2.6; B2.11–B2.14; B3; B4 Load).
- **Found:** `docs/deployment-options-and-costs.md` and the local cost workbooks are no longer in the folder (never
  committed); CLAUDE.md, README, pending-work and the guide still link to them. The Google Sheet is the current workbook.
- **Not verified:** only the 0.92 ms detector figure is measured. The 1.5× AWS slow-down and the 2% "everything else"
  are the sheet's assumptions until a load test. No tests run (documents only).
- **Later the same day:** the guide §26.6 gained the sheet's GPU, database and staff-screen sizing (2 GPU servers at 100
  calls, 3 at 400; 2 databases; 2 staff containers), which was the last thing only `reference/call-waiting-and-sizing.md`
  held (with an older GPU path: 20 s turns, 0.13 s a search). That file was then **deleted** at the owner's request (it
  was never committed); its timelines live in `docs/diagrams/diagrams.md` §7–8. Its README row removed. The PNG it
  mentions was not in the folder.

### 16 Nov (3): concurrency explained, measured; live-call limit built

- **New guide §26.11** "Concurrency, processes, threads and memory": server → physical core → vCPU → container →
  process → thread → event loop → task → call; the GIL; what runs on the main thread and in each helper pool (with file
  references); CPU and memory per call and per process; how the numbers stack up on a server; the call limit; quick answers.
- **Measured on the laptop** (throwaway brain on port 8011, fresh data folder, judge off, calls streaming noise so no
  STT or LLM was called): idle 5% of a core, 2.6–3.0 GB (local search models); 10 calls 40% (~3.5% each), 20 calls 89%
  (~4.2% each), **40 calls 99%: one core full**, although the laptop has 16. Silero: 0.88 ms CPU per frame with 1 torch
  thread, 1.8 ms with 2, 3.7 ms with 4 (same wall time); `silero_vad` sets torch to 1 thread on import. Torch import
  ~450 MB; each call's Silero copy 8.7 MB; the recorder 3.8 MB per call-minute.
- **Built: `MAX_LIVE_CALLS`** (default 9; `app/config.py`, `app/api/voice.py`, `app/metrics.py`, `app/api/ops.py`,
  `ui/app/page.tsx`, `.env.example`, `brain/README.md`). Over the limit: `busy` event + close 1013. `/health/ready`
  shows `live_calls` and stays ready. Metrics `brain_live_calls`, `brain_calls_refused_total`. The counter is freed in
  an outer `finally`: the first version leaked a line when the task was cancelled during clean-up (found by the new test).
- **Numbers:** offline tests **323 → 324 pass** (1 new: `test_live_calls_over_the_limit_are_refused`). UI build passes.
  No Groq calls; smoke set not run (no change to answers or routing). The demo handles 1–2 calls, so the limit of 9
  changes nothing in normal use.
- **Live:** the UI note is live (hot reload). **The brain needs a restart** to pick up the limit.
- **Found:** silent calls alone measure ~1–1.3% beyond the detector on the laptop. With turn bursts, real calls may
  need ~6.5–7.5% of an AWS vCPU, a little above the sheet's 6.3% (8–9 calls per container). Kept 9; the AWS load test
  decides (open question).

### 16 Nov (4): GIL, threads, processes and vCPUs clarified (documents only, no code change)

- Guide §26.11: the GIL subsection rewritten (the old wording "one process uses one core" and "each process has its own
  core" was imprecise). Added "Common confusions, cleared up" (GIL ≠ one thread; a process isn't one vCPU or pinned;
  a call isn't a thread; STT/LLM/TTS run on Sarvam's and AWS's machines, not our CPU; the main thread is the event
  loop in our app; torch releasing the GIL doesn't help when called on the loop) and "One turn of one call: who does
  what, and where" (each pipeline step: machine, thread, CPU). Evidence used: the same process at 99% of one core with
  40 calls and 156% right after while recordings were encoded (16 Nov measurement). Wording also fixed in §26.6, the
  quick answers and `docs/diagrams/diagrams.md` §8.

### 16 Nov (5): recordings on S3 (B2.2) and the startup lock

- **Recordings** now go through the file store (`services/recordings.py`, `container.build_recording_files`):
  `data/recordings` locally (same folder as before, so old recordings still play) or `<S3_PREFIX>recordings/` with
  `FILE_STORE=s3`. `FILE_STORE=s3` + recording on + no `RECORDING_KEY` → the brain refuses to start (a key file on the
  container's disk would be lost). `maintenance.clean_slate` / `delete_call` take the recording file store (the CLI
  builds it). No S3 lifecycle rule: retention stays in the app, so rows and files agree. No MinIO in the setup: Docker
  on the laptop keeps the `au-brain-data` volume.
- **Startup lock** (`db.py` `STARTUP_LOCK`, a Postgres advisory lock): migrations, the vector index and mock-bank
  seeding run one copy at a time.
- **Decided not to change:** the desk "live" flag and `LIVE_SESSIONS` stay per copy. The UI uses the flag to decide
  whether it can join the call's audio, which only works on the copy holding the call; a database flag would also show
  "live" for calls cut by a brain restart. The live-call gauge (`brain_live_calls`) was already built (16 Nov (3)).
- Guide: new §26.12 (running more than one copy: what is per copy, what is built, order of work); §14, §21, §26.1,
  §26.5, §26.6 table, §30 (Object Lock vs delete-a-call) updated. `deploy/README.md`, `pending-work.md` B2.2–B2.4,
  `brain/README.md`, `.env.example` updated.
- **Numbers:** offline tests **324 → 328 pass** (4 new, `tests/test_recordings.py`: S3 save / play / tamper / delete,
  delete-call and clean-slate on S3 leave other prefixes alone, the key guard, the lock blocks a second copy). No change
  on the call path, so voice latency is unchanged by design; not re-measured. Not verified: a real S3 bucket or MinIO
  (tested with the stub S3 client the document store tests use). Smoke set not re-run (no LLM-facing change).
- **Needs a brain restart** to take effect locally (behaviour on the laptop is the same either way).

### 16 Nov (6): how calls stay apart, and why no queue for calls (documents only, no code change)

- Guide §26.11: new part "How many calls in one process stay apart". It covers one WebSocket → one `stream()` task →
  one `VoiceSession` (own detector, buffer, player); the OS routes bytes by socket; replies are awaited by the calling
  task; `session_id` on every row; the send lock; keep-alive; the `finally` clean-up. It also explains why the detector
  takes turns (9 × 0.92 ms ≈ 8.3 ms per 32 ms window, ≈ 12.4 ms on AWS) and costs the same whether callers talk or not,
  why live calls don't go through SQS or another queue, and that a Cisco media gateway may turn SIP/RTP into one
  WebSocket per call (to confirm).
- Guide §17: new row "Amazon SQS for calls". §22 glossary: "Message queue (SQS)" and "asyncio task".
- Not verified: the ALB idle timeout and uvicorn ping interval are their defaults (60 s, 20 s), not checked on AWS.

### 16 Nov (7): Sarvam speech made ready for the trial credits (code; needs the key and a brain restart)

The owner has temporary Sarvam access (₹100 of credits). Plan: Sarvam STT + TTS while credits last, then back to
Whisper + Edge. The LLM stays on Groq. The `SarvamSTT` / `SarvamTTS` adapters already existed but had never run; they
were checked against docs.sarvam.ai and fixed.

| Change | Why |
|---|---|
| `enable_preprocessing` sent only for `bulbul:v2` | The docs mark it "not supported" on bulbul:v3 (could reject every sentence) |
| TTS stream reply read as raw PCM, WAV (streaming header, size 0) or MP3 | The docs don't say which `/text-to-speech/stream` returns; the old WAV stripper returned nothing for a size-0 header |
| `SARVAM_STT_MODEL` (saaras:v3) and `SARVAM_STT_MODE` (codemix) are separate settings | `STT_MODEL` is Whisper's; forgetting to change it sent a Whisper name to Sarvam |
| Typed errors (`SpeechError.out_of_credit` / `.retryable`); 429/5xx retried, 402/403 never | Sarvam: 402 = no credits, 403 = bad key, 429 can also mean "budget exhausted" |
| STT audio cut at 29.5 s | The REST endpoint takes 30 s at most; `MAX_UTTERANCE_S` is 30 |
| `SPEECH_FALLBACK=true`: a failed Sarvam request uses Whisper / Edge; out of credits or bad key → backup until restart (`voice/fallback.py`) | When credits run out mid-call, the call carries on instead of failing. Half-spoken sentences are not repeated in the other voice |
| Fixed phrases of paid voices kept on disk (`data/tts-cache`, keyed by voice, settings and exact text) | The warm-up synthesises ~1,900 characters: about ₹5.7 per brain start with Bulbul v3 |
| `brain sarvam-check` | One TTS + STT round trip straight to Sarvam (under ₹1) to check the key; prints no key |

- `brain/.env` got a commented Sarvam block (key empty, providers commented out), so the running demo is unchanged.
- The `sarvamai` SDK was not added: httpx already does the two REST calls; one fewer dependency for the security review.
  The chat-completion test script from Sarvam's onboarding prompt was skipped: the LLM stays on Groq.
- Tests: offline **343 pass** (328 before; +15 in `tests/test_sarvam.py`, Sarvam faked with `httpx.MockTransport`).
  No Groq or Sarvam requests were made. Smoke set not run (no change to text answers).
- **Not verified (no key yet):** real Sarvam replies, the actual stream format, latency, Hinglish quality of `codemix`
  and `priya`'s voice. Run `sarvam-check` first.
- Credit budget (Nov 2026 prices): STT ₹0.49 a minute; Bulbul v3 ₹3 per 1,000 characters. ₹100 ≈ 33,000 spoken
  characters, i.e. roughly 30–60 short test calls. `bulbul:v4-flash` is ₹1 per 1,000 but uses its own speaker IDs (not wired).
- Live: no. The brain needs a restart after the key is in and `STT_PROVIDER` / `TTS_PROVIDER` are uncommented.

### 16 Nov (8): newer Sarvam models, warm connections (code; needs a brain restart)

Checked against Sarvam's model and pricing pages and a third-party benchmark of Bulbul v4-flash vs v3 (A. Mathew,
13 Oct 2026: first audio only ~20 ms faster on v4-flash; connection reuse worth 100-250 ms; v3 speaker names give
HTTP 400 on v4-flash; v4-flash speaks ~6-7% faster at the same pace; basic plan caps v3 at 30 requests a minute,
v4-flash at 60; price ₹2 per 1,000 characters after the launch offer ended 12 Nov).

| Change | Why |
|---|---|
| **Speech connections kept 300 s** (httpx default was 5 s) for Groq Whisper and Sarvam | Most turns of a call paid a fresh TLS handshake (100-250 ms). Affects today's Groq STT too. Same as the LLM client |
| Connections opened when a call starts (`VoiceRuntime.warm`, a tiny free GET) | The first turn doesn't pay the handshake either |
| STT default `saaras:v4` (was v3) + key terms from `STT_VOCABULARY` (39 product and banking words) | Sarvam's current default, same price, codemix supported; key terms do what the Whisper word list does |
| TTS default `bulbul:v4-flash` (was v3), voices per language: Ishita (`ishita_en_customer`, `ishita_hi_banking`, `ishita_enhi_customer`) | ₹2 vs ₹3 per 1,000 characters, twice the rate limit; customer/banking personas; the same voice in all three languages |
| Speaker names checked against the model at startup | A v3 name on v4-flash (or the reverse) would fail every sentence with 400 |
| Own `SARVAM_PACE` (1.0), no longer Edge's +10% | v4-flash is already faster at 1.0; tune by ear |
| `sarvam-check` compares v4-flash and v3 (3 languages, 2 takes, warm connections; WAVs with `--out-dir`) | The benchmark's method on our own sentences; about ₹2-3 |

- Kept: HTTP streaming (the benchmark found it faster than one WebSocket per request).
- Not done: Malayalam and 8 kHz phone audio (later, with Cisco); a second voice provider for comparison.
- Tests: offline **347 pass** (343 before; +4). No Groq or Sarvam requests.
- **Not verified:** the Ishita persona IDs come from Sarvam's voices page, not a live call (a wrong ID shows as a 400
  in `sarvam-check`); v4-flash voice quality against v3 (no published study); the latency gain from keep-alive was not
  measured here (it needs live calls).
- Live: no. The keep-alive change also reaches the current Groq Whisper path after a brain restart.
- Later the same day: `sarvam-check` sentences now carry the words most often said wrong (AU, Zenith+, Laksya, BSBDA,
  0101, KYC, OTP, UPI, NEFT, EMI, an amount, a rate) and go through the live call's pronunciation rewrite
  (`speech_text`), so the WAVs are what a caller hears. One take by default; about 350 characters per model, about ₹2
  for both models. The pronunciation rules were tuned on Edge voices (6 Nov) and are untested on Sarvam.
- **First real `sarvam-check` (owner, 16 Nov 15:20):** the key works; all six takes came back (both models, all three
  Ishita voice IDs accepted); the WAVs are clean speech (stream decoding works). Billed ₹2.25 of ₹100 (₹97.74 left):
  bulbul:v3 349 chars ₹1.05 (₹3 / 1,000), **bulbul:v4-flash 349 chars ₹0.70 (₹2 / 1,000: the ₹1 on the docs pricing
  page is wrong)**, saaras:v4 ~1 min ₹0.51. Speaking speed at pace 1.0 (chars/s, v3 → v4-flash): English 11.6 → 14.8,
  Hinglish 9.6 → 11.8, Hindi 13.0 → 10.8 (v4-flash slower in Hindi, as the benchmark article also found). Timings and
  the heard-back transcripts were in the owner's terminal, not recorded here; the listening verdict is pending.
- **Fix: silence while a keypad code is awaited.** Before, the normal 8 s timer ran: the caller still opening the
  SMS heard the general "are you still there? You can ask about accounts..." and the call **ended at 16 s**,
  mid-verification. Now, while the code is awaited (verification status `otp_sent` or `invalid`), the wait is
  `CODE_WAIT_S` (30 s), the reminder is a new phrase `code_reminder` ("Please type the six-digit code sent to your
  mobile on the keypad. Take your time.", en / hi / hinglish), and the call ends only after a second 30 s. Any new
  turn or an accepted code returns to the normal timer. `voice/session.py`, `voice/phrases.py`, `config.py`; the UI
  lists the phrase as a canned line (`ui/app/page.tsx`, live now through hot reload). Offline tests **349 pass** (+2).
  Needs a brain restart; with Sarvam the new phrase costs about ₹0.5 to record once. Open question for the owner / PM:
  is ending a normal call after two silences (16 s in all) too quick for elderly callers?
- Sarvam test scripts: `docs/test-call-scripts.md` Part C (three calls, about 17 turns; corrected so that no silent wait
  runs into the no-input hang-up).
- Noted, not changed: with Sarvam, the "unclear speech" filter never fires and noise is caught only by empty text or
  known Whisper phrases, because Sarvam returns no no-speech or confidence score (Whisper does). Watch in test calls.

### 17 Nov: review of the four Sarvam test calls; spoken-form fixes (code; needs a brain restart)

Reviewed the owner's four live calls of 16 Nov, 23:39-23:58 IST (Ravi `54aa55be`, Pooja `d48a5afe`, Sunita
`f4d36d3a`, Imran `210b57c4`): transcripts, traces, logs, and the recordings themselves. The recordings were decoded
and measured (caller and agent channels), and each agent reply was transcribed back with Groq Whisper as a
pronunciation check (no listening by Claude; tone and warmth need the owner's ear).

What the calls showed (issue → fixed or not):

| # | Issue | Status |
|---|---|---|
| 1 | All 4 failed turns were Groq **429 tokens-per-minute** (8k TPM; the understanding call alone is ~3.8k tokens) | Owner: ignore for now. Proposed, not built: error reply in the call's language (it fell back to English for Hindi callers), and the error's "I can connect you to our team" made a real offer so "yes" transfers without the LLM |
| 2 | Brackets in templates: "मोबाइल (आख़िरी अंक 0107)", "(ending 0105)", nested "on hold (card authorisation pending (fuel station))"; heard as one run-on phrase | **Fixed** in the text sent to TTS: an aside becomes commas (`spoken_punctuation` in `numbers.py`). Screen text unchanged |
| 3 | "6-digit" in the code prompt, heard "six dash digit" | **Fixed**: a hyphen after a digit becomes a space (a range like 5-10 keeps it); "&" said "and" / "और" |
| 4 | Dates read like a statement: "On Thursday, 5 November 2026, at 21:40"; "10 Dec 2026"; Hindi "1 नवंबर 2026, रविवार को, 9:30 बजे" | **Fixed** (`personal.day`, `_when`): full month, no year when within ~6 months, 12-hour time with part of day ("9:40 at night", "शाम 4:30 बजे", "subah 9:05 baje"). Changes the screen text too |
| 5 | "POS purchase" (statement jargon) | **Fixed**: "card purchase" |
| 6 | Two loans: "Your next EMI of ₹25,140... Your next EMI of ₹7,850..." | **Fixed**: "Your home loan EMI of ₹25,140 is due on 10 December." (new `emi_next_of`) |
| 7 | A Hindi caller's "Yes." switched the reply to Hinglish | **Fixed**: a reply of 1-3 neutral words (yes, ok, haan, ji...) keeps the call's language |
| 8 | After the code: "Thank you, you are verified." + 1.2 s gap + filler "Okay, one second." + answer | **Fixed**: the waiting question is prepared while "verified" plays and queued right behind it; no filler on that turn (voice-to-voice metrics are not recorded for it) |
| 9 | After verification (call 4) the agent asked "is today's call about your open dispute?" though the caller had said "my card was stolen", and went on without waiting | **Not changed**: asked "ask, never assume" is tested behaviour. Question for the owner below |
| 10 | "five transactions in the salary account" understood as "What is the balance...": balance re-read before the transactions (13 s), caller said "stop stop" | Not changed: an understanding (LLM) error; needs a routing-set check when quota allows |
| 11 | "last EMI I paid" answered with the next EMI due (judge flagged it too) | Not changed: understanding picked `next_emi`; the `emi_paid` template exists |
| 12 | Filler before almost every reply (understanding ~1.5 s > `FILLER_AFTER_MS` 1.2 s) | Not changed: a timing trade-off; options listed for the owner |
| 13 | Hindi caller cut off after "...पूछनी थी कि" (1.47 s pause) | Working as designed ("कि" already waits up to `MAX_END_SILENCE_MS` 1.5 s); the pause was longer. Option: 2000 ms |
| 14 | STT: "किश्त" heard as "किश्ती" | Not changed; understanding coped |

- Measured from the recordings: caller stops → first agent sound **2.0-2.8 s** (one 3.5 s), mostly the
  understanding call; gaps between sentences 0.2-0.5 s, except 1.2-1.3 s after "you are verified" (fixed by 8).
- Research (Sarvam's v4-flash best-practice guide; voice-agent prompt guides from LiveKit, Vapi, Parloa and others):
  short sentences, punctuation for pauses, send whole sentences (we already do), let the TTS read normal numbers, no
  SSML on v4-flash, pace 1.1-1.2 for "professional IVR". Not adopted: rewriting the LLM prompt (the deterministic
  text layer covers templates and LLM text alike, and a prompt change would need a full re-measure on Groq quota); a
  separate "speech response" LLM pass (adds latency); Sarvam's `dict_id` (the guide's own parameter table doesn't
  list it for v4-flash). Noted for later: Sarvam says romanised Hindi ("Aapka order...") sounds worse than Devanagari
  with English words in Latin ("आपका order..."); our Hinglish templates are romanised.
- /calls "every call is Nov 16": not a database error. Times are stored in UTC and correct; the first page held the
  owner's 4 calls (23:39-23:58 IST) and ~72 smoke-test text sessions from 10:51-11:11 IST the same day, and older
  rows showed only the date. The list now shows date and time ("Nov 16, 11:57 PM"); live via hot reload. Also noted:
  `DEMO_TODAY` is fixed when the brain starts, so a brain left running past midnight still treats yesterday as today.
- Tests: offline **363 pass** (349 before; +14). UI `tsc` passes (`bun run build` not run: the dev server is live).
- Live: UI yes; brain changes need a restart. Changed phrases will be re-recorded once by Sarvam (a few paise).
- Smoke set after the change: 18/24 (75.0%), 0 Groq requests; the two new misses were the owner's real block of
  Ravi's card 6712 in call 1 ("already blocked"). The smoke run puts the demo cards back afterwards; the next run was
  **19/24 (79.2%), same as 16 Nov**.
- **Eval runs no longer fill /calls.** `evals.smoke` and `evals.benchmark` ran against the real database, so each run
  added 24-40 text sessions to /calls. They now delete their own sessions at the end (`DELETE /v1/calls/{id}`: turns,
  handoffs, alerts); the results stay in `evals/reports/`. Checked: a smoke run left the call count unchanged (119).
  Offline pytest already used a throwaway database. The 99 older eval sessions (72 from 16 Nov, 24 from 17 Nov, 3 from
  6-7 Nov; found by "not live, no recording, first question is a scripted eval turn") are still there, pending the
  owner's OK to delete them; the owner's 11 own text chats and the 9 live calls are not in that set.

### 17 Nov (2): fixes from the call review, approved by the owner (code; brain needs a restart)

| Fix | How | Where |
|---|---|---|
| "Yes" after a system error transfers without the LLM | A failed turn's "I can connect you to our team" is now a real offer (pending `handoff`, department general), so "हाँ, team se connect karo" is read in code. Before, the yes went to the LLM and failed the same way (call f4d36d3a) | `conversation.turn` |
| A balance nobody asked for is not re-read | When a balance request comes with another request and the caller's words have no balance word (balance, kitne paise, left, bacha, बैलेंस...), it is dropped (call 54aa55be: "five transactions" re-read the balance first) | `understanding.asked_only` |
| "Last EMI I paid" answers the past payment | "last / previous / pichhli / पिछली ... EMI / kist" (and "EMI ... I paid / kati thi") without "next / agli / kab hai" turns `next_emi` into `emi_status` | `understanding.asked_only` |
| Open request asked only when the caller gave no reason | After verification, "is today's call about your open dispute?" is skipped when a question was waiting (the caller already said why they called). Verification always starts from a specific request today, so in practice it is now rarely asked | `conversation.verify`; test reworded |
| Longer wait when the words sound unfinished | New `INCOMPLETE_WAIT_MS` = 2500 (was 1500, shared with the adaptive wait). Industry: LiveKit's turn detector waits up to 2.5 s when it thinks the turn is unfinished (0.3 s when finished), Pipecat Smart Turn 3 s, OpenAI's server VAD 0.5 s for any pause. Complete sentences still end at 700 ms; `MAX_END_SILENCE_MS` (1500) now only caps the adaptive wait, so normal turns are not slower | `config.py`, `voice/session.py`, `.env.example` |
| Bulk delete on /calls | "Select" button: rows get tick boxes, "Select all shown", "Delete" with the same "Delete for good" confirmation; deletes one by one through `DELETE /v1/calls/{id}` (a live call is refused and left). Live now (hot reload); checked in the browser without deleting | `ui/app/calls/page.tsx`, `ConfirmDelete` exported |

- Model choice asked by the owner (both steps on 120b today): see the reply of 17 Nov; not changed.
- Tests: offline **370 pass** (363 before; +7). Smoke pinned to 120b (the cached baseline): **19/24 (79.2%), the same
  5 misses as 16 Nov, 0 Groq requests**. UI `tsc` passes.
- Quota note: `brain/.env` was edited at 10:52 and now has `ANSWER_MODEL` / `UNDERSTAND_MODEL` commented out, so the
  code defaults apply (understanding **20b**, answer 120b). A smoke run started after that missed the cache (the model
  is part of the key) and made 19 real 20b requests (~65k tokens) before it was stopped; Groq then reported the 20b
  **daily token limit (200k) used up** (the post-call judge also runs on 20b). The running brain still has the models it
  loaded at start; a restart with the lines commented would put understanding on 20b.

### 17 Nov (3): PM review of call 54aa55be (code; brain needs a restart; tests not run yet, by the owner's choice)

The PM reviewed the 16 Nov call before today's changes. Already fixed earlier today: brackets and "6-digit" in speech,
dates, the unasked balance re-read (`asked_only`), "last EMI paid" → `emi_status`, the stacked filler after verification.

| PM point | Change |
|---|---|
| Code request is abrupt | "Sure, I can help with that." / "जी, ज़रूर।" / "Ji, zaroor." before "For your security..." (only when a question starts verification; the fraud opening keeps its own lead-in) |
| Hold reason written for a screen: "(card authorisation pending (fuel station))" | Mock bank reasons are now spoken phrases ("a pending card payment at a fuel station", "a cheque deposit that is still clearing"); English: "Of that, ₹2,500 is on hold for a pending card payment at a fuel station, so you can use ₹79,850 right now." A real bank's hold codes would need the same mapping in the adapter |
| "Five transactions" read three, then "Want the other 2?" | A number the caller asked for is read in full (up to five at once); with no number, still three then the offer |
| "Salary account" assumed to be 4521 | Not an assumption: it uses the bank's salary flag, and asks "which account?" if there is none. The intro now says it back: "...on your salary account ending 4521" |
| Two loans: "Your EMI of ₹X... was paid" twice | "Your home loan EMI of ₹25,140, due on 10 November, was paid successfully." (`emi_paid_of`) |
| Never substitute data | "Last EMI paid" with no payment on record now says "I can't see any EMI paid yet on your home loan" (it used to fall through to the outstanding amount) |
| Temporary block confirmation offers a permanent block | When the caller asked for a temporary block: "Just to confirm, I'll temporarily block your Zenith+ credit card ending 6712. You can unblock it any time. Shall I go ahead?" The permanent option stays when the caller didn't say which |
| Audit said "Understood as: balance" while transactions were answered | The audit records every request the turn was split into (after code corrections), with topic and detail; "Why" lists them all and shows the detail (e.g. `next_emi` vs `emi_status`) |
| 34 bank calls for one card block | Explained, not changed: every verified turn reads the whole customer profile (16 parallel reads over 6 systems, during understanding, so hidden from the caller); the confirm turn reads it twice (plan, then read-back to prove the card changed). Reading only what a turn needs is the fix if the real bank's APIs are slow or metered |

Not done: intent taxonomy rewrite and a separate relevance checker (the topic/detail schema plus `asked_only` cover the
failures seen); "would you like to know why it's on hold?" offers (adds a turn).
Tests: test files updated (five-means-five, three-when-unasked); **not run** (the owner asked to batch testing).

### 17 Nov (4): PM notes 2 and 3 (calls d48a5afe and f4d36d3a) (code; brain needs a restart; tests written, not run)

**Root cause of "no such transaction" (Pooja, call d48a5afe): stale mock data, not the filters.** The understanding
was right (UPI + debit + total + last 7 days; Rajesh + credit + today), and the same code on fresh data answers
"4 payments, ₹3,940" and "₹5,000 from Rajesh Sharma". But the live mock bank seeds only once, and it had been seeded
on **6 Nov**: Rajesh's "today" payment was dated 6 Nov and the "this week" UPI payments 3-6 Nov. On 16 Nov both
searches truly found nothing. Fix: `corebank.seed_info` records the day the data was made for; at startup a mock bank
seeded for another day is emptied and re-seeded for today (test-call changes such as blocked cards and mock CRM
tickets go with it). The first restart re-seeds once (no date recorded yet).

| PM point | Change |
|---|---|
| Yes/no questions ("did my salary come?", "did Rajesh pay me today?") | Found: the answer starts "Yes." / "जी हाँ।" / "Ji haan." Only for money-in checks and salary |
| "No such transaction" for every miss | Says what was searched: "No, I don't see any money from Rajesh today." / "नहीं, आज Rajesh से आया कोई पैसा नहीं दिख रहा।"; "...on any of your accounts" when all were searched. A bank system that can't be reached is still a separate reply (BankError: "can't fetch right now"), never "none found" |
| UPI spend total | "पिछले 7 दिनों में आपने UPI से कुल ₹3,940 खर्च किए, 4 पेमेंट्स में।" / "You spent ₹3,940 through UPI in the last 7 days, across 4 payments." (account named only when there are several). The sum is computed in code, never by the LLM |
| Account digits on every turn ("अकाउंट (आख़िरी अंक 9001)") | Not said in the new total / not-found replies when the caller has one account |
| "मेरे कितने loan बाकी हैं?" answered with the gold loan's outstanding + the next EMI again | Code rule (`asked_only`): "how many / kitne / कितने ... loan" without an EMI word is a count of all loans: "आपके नाम पर अभी एक लोन चल रहा है, आपका गोल्ड लोन।" With "baaki / left" and one loan, the EMIs left follow ("आपने 4 EMI भर दी हैं और 8 बाकी हैं।"). Code-only details `loan_count` / `loan_count_left` (kept out of the LLM schema so cached evals stay valid) |
| Next EMI repeated after "outstanding" | "Outstanding" is answered alone; the next EMI is added only to a plain loan overview |
| Name the loan in the next EMI | Always: "गोल्ड लोन की अगली EMI ₹5,400 की है, 15 दिसंबर को।" |

Not changed: a clarifying question for "kitne loan baaki" (answering both readings costs no extra turn); the
understanding step merging the previous question into its rewrite ("...अगली किश्ती कब है और मेरे कितने loan बाकी हैं?"):
a prompt change, left for a routing-set run; the 6-system profile read per turn (see part 3). Answer wording changed, so
the next smoke run will miss the cache on multi-turn items (their history text changed) and spend some Groq requests.
Tests added: Pooja's total / Rajesh / salary yes, Sunita's loan count, stale re-seed. Not run yet.

### 17 Nov (5): PM review across all recordings (code; brain needs a restart; tests written, not run)

Several reviewed lines came from the 16 Nov smoke-test sessions and predate today's fixes (balance hold wording, five
transactions read as three, account digits). Triage of the rest:

| PM point | Verdict | Change |
|---|---|---|
| Stolen card already blocked: "kuch change karne ki zaroorat nahi" sounds dismissive | Valid | New `already_blocked`: "Your Ananta credit card ending 4410 is already permanently blocked, so it can't be used for any payment." (the fraud-team offer follows as before) |
| Unauthorised UPI debit still processing: "may still be reversed by itself" | Valid (a guess that invites waiting) | "It's still showing as processing. Since you don't recognise it, it's best to report it now." then the complaint + transfer offer |
| Shared OTP: "Good news: no payments in the last two days" | Valid (a shared code is used for logins and new payees too) | "I don't see any payments ... but a shared code can also be used to log in or add a payee, so your account needs to be secured now." then the transfer offer |
| Unrecognised payment, none found: "I don't see any debits in the last week" | Valid | Adds "If you feel your account isn't safe, I can connect you to our fraud team right away." |
| Reference numbers read as one long number | Valid | Speech only: "AU261116001210" → "A.U., 2 6 1 1, 1 6 0 0, 1 2 1 0"; ticket ids likewise ("A.U. C M P, ..."). Endings and amounts untouched |
| Handoff "context package" | Already built | The desk note carries reason, department, verified + time, customer / segment / RM, last questions, actions done with references, alerts, call facts, language |
| Per-turn language detection | Already built | Understanding returns the language every turn; one-word replies keep the call's language (17 Nov) |
| Global sort for "last 5 across accounts" | Already built | Merged and sorted by date and time before the count is applied |
| Verify state after an action; code decides success | Already built | Card actions read the bank state back (ADR 0008) |
| Response relevance validator, response modes, interruption types | Covered | Topic/detail templates, `asked_only` code rules, "never substitute" EMI rule, `read_reply`, account choice, stop words. A separate validator layer would duplicate these |
| Fraud taxonomy as separate workflows | Mostly built | lost card, unrecognised debit, OTP shared, other fraud are separate `risk` flows in code with their own steps. Phishing/scam and account-compromise flows are not built |
| Shared OTP: transfer without asking; don't send another OTP; check logins, devices, payees | Not built: bank policy | Needs AU's fraud procedure (already an open question). The mock bank has no login/device/payee events |
| Hinglish in Devanagari + English (Sarvam's advice), Hindi style guide | Not built | Large rewrite of every Hinglish template and the answer prompt; to be tested by ear first |
| "I'm interested in credit cards": ask what kind of help first | Not changed | Adds a turn before the recommendation; product decision (question below) |
| "5 in each account" | Not built | "both accounts" keeps the documented default (5 newest across both); per-account counts not asked for yet |
| Metrics per language; multilingual regression set | Partly there | Smoke / routing / golden sets cover en, hi, hinglish; per-language reporting is a follow-up |

### 17 Nov (6): cost workbook v2 made fully connected (Google Sheet only, no code change)

Management changed the downloaded xlsx: 2-minute calls raised the cost per minute, and 200 calls at the same time
didn't change the bill. Cause: per-call usage was typed for a 4-minute call, calls at the same time and calls a day
were two separate inputs, and "servers we run" was typed. Fixed in the Google Sheet:

- **Estimate:** calls at the same time is now worked out from calls a day × busiest-hour share × call length (the
  old "spare calls" row is gone). Usage is per turn: turns a minute (3) × call length gives turns; characters (112.5),
  tokens read (4,250), tokens written (383.3) and searches (0.4) are per turn. Same measured numbers as before.
- **Servers:** app and GPU servers we run = servers needed with the spare, rounded up to whole servers.
- **Prices:** a Yes/No check on Sarvam Growth's request limits (fails above about 165 calls at the same time).
- **Choices 5:** microservices = monolith + 3. **Answers:** updated, two new questions on cost per minute.

| Test | Bill a month | Per call | Per minute |
|---|---|---|---|
| Before and after, 12,500 calls a day, 4 min (100 at once) | ₹28,53,051 | ₹7.61 | ₹1.90 (unchanged) |
| 2-minute calls | ₹15,66,121 | ₹4.18 | ₹2.09 (fixed GPU, database, network over fewer minutes) |
| 25,000 calls a day (200 at once) | ₹54,58,185 | ₹7.28 | ₹1.82 (7 app servers; Sarvam check says No) |

Skipped as not needed now: call-type mix, a separate scenarios or validation sheet, a peak-bunching factor (the
spare server covers it), database and staff-screen sizing (fixed at this size), a fixed-plus-per-turn split (no
measurement of the fixed part yet). Management needs a fresh xlsx download; the old local
`docs/deployment-costs-and-options-v2.xlsx` is out of date.

**Later the same day, the link was turned round (owner's call):** calls at the same time (Estimate B7) is typed again,
and calls a day is worked out from it: B9 = B7 × 60 ÷ call length ÷ busiest-hour share (12%, now row 9, so calls a day is row 10). The three
in-between rows (calls in the busiest hour, minutes in it, average at once) were removed. Doubling B7 still doubles
the bill (₹54.58 lakh, 7 servers, Sarvam check No). Shorter calls now mean more callers on the same lines: 2-minute
calls give 25,000 calls a day, bill ₹28.60 lakh, ₹1.91 a minute, ₹3.81 a call. The baseline is unchanged
(₹28,53,051). Answers explain bunching: random calls averaging 100 reach about 130 for short moments; the 4 servers
carry 144 (108 with one down). Not built: a second "type calls a day" mode (no forecast from AU yet) and a separate
bunching input; a cost-per-minute split by supplier already exists on the Cost sheet.

### 17 Nov (6): regression pack run, data audit, fixes (code; brain needs a restart)

**Pack:** `evals/regression.yaml` (22 conversations from the PM's test plan, typed, real Groq 120b, replay-cached), run by
`python -m evals.smoke --pack regression --keep` (`--keep` leaves the calls on /calls; the report has full transcripts;
an item can run with the LLM down; the run stops at Groq's daily limit). First run 12:21-12:55: 61/69 turns passed the
checks, and reading the transcripts found 9 groups of problems (report `evals/reports/regression-20261117-125511.md`).

**Data audit** (live mock bank): one real gap - Sunita (107) had **no transactions** at all (4 added to the seed). The
rest was right: Ravi's loans and EMI history, his 4512 (inactive Digital, one cash deposit; salary is on 4521), Imran's
debit card 6634 is his own; the "already blocked" Ananta of 16 Nov was state left by earlier test calls.

**State:** no new state manager. The call already keeps a structured state (`CallState`: product/account in focus,
waiting requests, declined offers) and a typed pending question (kind: action / fraud_followup / fraud_pick / handoff /
more ...). The bugs were places that threw that state away too early; fixed there.

| Problem | Fix (code only, no LLM call) | Where |
|---|---|---|
| Loan questions labelled with a card detail went to credit cards ("I don't see a credit card", "which card?", card payments read out) | On a loan, `amount_due`→`outstanding`, `payment_status` / `statement`→`emi_status` | `conversation.personal_query` (`_LOAN_SAME`) |
| "Last instalment deducted", "pichhli EMI kab kati" filed under transactions | Last-EMI words move it to loan / `emi_status`; "when is the next one due?" no longer repeats the past one | `understanding.asked_only` |
| "How much have I paid in total (loan)?" | EMIs paid and left for that loan (`emis_count`), never a summed amount we don't hold | same |
| "Has that 2,500 been deducted?" → "no payment to Deducted" | Amount + "deducted / cut / kata" → `hold_check`: "No. The ₹2,500 is on hold for ...; it hasn't been taken ... Until it clears, you can use ₹79,850." Status words are never names | `asked_only`, `personal` |
| "How much can I use?" re-read the balance | `available_only`: "You can use ₹79,850 from your Value account ending 4521 right now." | same |
| "Does that include money people sent me?" repeated the total | `total_scope`: "No. That total is only money you paid out; money people sent you isn't included." | same |
| "Last three UPI payments" included money received | "payments / paid / spent" + UPI → money out | `asked_only` |
| "Five in each account" gave five in all | `account = each`: per account, newest first; "only N" when fewer | `asked_only`, `personal._each_account` |
| Bare "my balance" asked "which one?" | Every account (PM expectation) | `personal._balance` |
| A side question dropped a waiting confirmation (stolen card transferred while still active; ₹10,000 complaint never raised) | Fraud offer: the question gets "I can't confirm that from here. The fraud team can check it with you." and the offer again (pending kept); a detail gets "I'll pass that on" once; an unrecognised-payment pick with no answer gets one more useful question (amount / UPI, card or ATM); "so my account is safe?" gets no promise plus the fraud team. A card-action confirmation is asked again after the answer | `journeys.resolve`, `conversation.turn` |
| "Stolen, but don't block it" offered a permanent block of a debit card | `no_block`: "All right, I won't block it. ... our fraud team should look at other ways to protect you. Shall I connect you?" | `asked_only`, `journeys.fraud` |
| "Can I unblock it tomorrow?" offered to unblock now | `ask_only`: "Yes. It stays blocked until you ask us to unblock it; just call us when you're ready ..." | `asked_only`, `journeys.action` |
| Bare "Yes." after "which one?" became a card-statement answer | Asked again, never a new request | `conversation._understood` |
| Groq error message in English to a Hindi caller | The call's language, or Hindi for Devanagari input | `conversation.turn` |
| Wording | Every EMI line names the loan; Hinglish / Hindi EMI lines start "Aapke / आपके ..."; "eMI" fixed; no transactions → "I don't see any transactions" (not "matching"); a named account is said ("...on your account ending 4512") | `personal` |

Re-run on Groq (13:05-13:26, kept on /calls): fixed in the real run - balance / available / hold, home-loan follow-ups,
"how much do I still owe?", EMI paraphrases (en / Hinglish / Hindi), the ₹10,000 complaint raised with a reference
after the side question, OTP follow-ups, "so my account is safe?". Still wrong in that run, fixed afterwards in code:
`no_block` / `ask_only` were set on the understanding but read from the request (now on both); "not sure" reads as a "no"
(the extra fraud question now accepts it); "does that include ...?" is the scope reply when "include" is said; "do I
need to explain again?" → "No, I'll pass on everything we've discussed"; "is the complaint registered?" → the reference
from this call's record. The run stopped at **Groq's daily token limit** at R11 (the runner now writes its report even
then). Not yet re-run on Groq: R11, R13, R16, R14-hi and the items changed since.
**EMI references (owner):** an EMI has no number of its own in the bank's records; the loan has one (LHO900101). With
more than one loan every EMI line, bounced EMIs included, says the loan's last four digits ("The EMI on your home loan
ending 0101 is ₹25,140, due on 10 December." / "आपके 0101 वाले होम लोन की अगली EMI ..."); the loan count lists them too.
Offline tests **389 pass**.
**Final runs (new Groq key, 14:00):** full pack **22/22**, 16 real requests (the rest cached); reading the transcripts
found five more, fixed and re-run (R06, R11, R13, R26 all right): the corrections now run after the risk screen (it had
rebuilt the fraud request and dropped `no_block`); "do I need to explain again?" while a transfer waits → "No, I've
passed on everything ..." (no second transfer); "what is this payment for?" about the call's one hold → the hold
explained; a bare "Yes." with nothing waiting → "anything else?" (no re-greeting); the mock bank re-seeded once so
Sunita's new transactions exist. **Every EMI line now says the loan's last four digits, also with one loan** ("The EMI
on your gold loan ending 0107 is ₹5,400 ..."), owner's request. Offline tests **389 pass**.
Not changed: the eval's 25 s turn was Groq rate-limit waiting inside the eval runner, not the agent; "Grounding: high"
means the facts came from bank records, not that the answer fits the question (that is what this pack and the call
review check); security events (logins, devices, payees) after a shared OTP are not in the mock bank; whether to
transfer without asking / skip the new OTP is AU policy (open question). Offline tests **389 pass**.

### 17 Nov (7): spoken form everywhere, card consent, loan scope, transaction follow-ups, EMI debit dates (code; brain needs a restart)

**Spoken form (permanent):** every fixed reply is written the way it is said: no brackets, semicolons, "&", "6-digit",
digit hyphens. 118 templates rewritten (Hindi "{l4} वाले अकाउंट", hold reasons said in Hindi/Hinglish, the open-request
topic, the card ending). Kept that way by three layers: a test that scans every template (`tests/test_spoken_form.py`,
fails on any new written-only mark), a free review check on every call (`replies_in_spoken_form`, `numbers.screen_marks`),
and the LLM judge rubric (written-only text = `poor_voice_response`). The answer prompt says the same for KB answers.
**OTP prompt:** "Sure. Before I share your transactions, I need to verify it's you. I've sent an OTP to your registered
mobile number ending 0101. Please enter it on your keypad." (topic-aware; "OTP" instead of "6-digit code"). The audio
already said "6 digit" since 16 Nov (speech layer); now the written text does too.

**Cards:** "I want to block my card" asks "temporarily ... or permanently, with a replacement card?" (a plain "block"
that reports nothing lost is no longer taken as a lost card). "Make it permanent" replaces the waiting confirmation (a
side question re-asked the old temporary one); "Will I get a replacement?" is answered, then the same confirmation; "Yes,
block it and connect me" blocks, then transfers; "Unblock it tomorrow at 10 AM" says it can't be scheduled and offers now;
"Lost my card, don't block it yet, connect me to fraud" → no block, fraud team offered. After verification the caller's
own words are re-asked when the whole turn is still to do (a rewritten question had lost "don't block it yet").

**Loans:** the loan in focus is kept ("how much have I paid so far?" after the home loan → home loan only); "across all
my loans" → all, with a total first; "how many EMIs have I paid?" with two loans and no context → "which one?". "How much
have I paid / kitna repay kiya" is an amount (`repaid_amount`: sanctioned − outstanding, with EMIs paid); "how many EMIs"
stays a count. "Last EMI kab kati / when was it deducted / when did money last leave my account for my EMI" → the actual
debit (`emi_last_paid`); a returned EMI is said as not deducted. "My EMI bounced, has it been paid now?" → "No, ... hasn't
been paid yet. It was returned because ..." (`emi_recovery`). **EMI debit date:** the mock bank's EMI record now has
`paid_on` (migration `011_emi_paid_on.sql`, seed, mock API, HTTP adapter, `Emi.paid_on`) - a due date is never presented
as a debit date. Mock bank re-seeded once for it.

**Transactions:** "five in each account" starts with an overview, then each account with "First, ... Finally, ...";
"today / yesterday" for recent days; failed: "It was unsuccessful, so no money was deducted"; returned EMI: "It was
returned, so the payment wasn't completed". Follow-ups: "only the failed ones" (`failed_only`, a status, never a
name), "only the debits" (a direction), "why is ₹2,500 blocked?" (the hold on its account, never "which account?" or a
complaint), "available excluding the hold" (balance, hold and usable amount said apart; an inactive account is said
as inactive), "did the pending payment get reversed?" (`pending_check`: after a hold → that hold, "I don't see a
reversal recorded yet"; never guessed). The call state remembers what the call was just about (`focus.about`,
`focus.loan`) - no new state manager.

**Tests:** offline **394 pass**. New targeted pack `evals/checks.yaml` (11 conversations, PM test list A/C/D/E merged),
run on Groq 120b and kept on /calls; after the fixes every turn is right (reports `evals/reports/checks-20261117-*.md`).
The slow turns in those reports (18-28 s) are the runner waiting for Groq's free-tier rate limit (understanding time ≈
total time); the agent's own work was 0.1-0.2 s. Regression R03/R22 expectations updated for the new behaviour (not re-run).

**Browser audio (muffled agent, own voice from the left speaker):** the app never routes the microphone to the
speakers (code read, and the playback path probed in the browser: mono, both channels). The symptom matches the laptop's
Realtek microphone-array playthrough / Windows communications mode while Chrome holds the mic. Owner to check: Sound
settings → Recording → Microphone Array → Listen (off), Playback → Speakers → Levels (mute any "Microphone" slider),
Communications → "Do nothing". No app change made.

**Docs (later the same day):** PROJECT-GUIDE §25.6 (14–17 Nov history), §33 (every code file: why, what, who uses it) and §34 (how a reply is made; the LLM-writes-a-slot-template idea compared with fixed templates and rephrasing; proposals not built).
**Cleanup (later the same day, owner-approved list):** removed `evals/benchmark.py`, `routing.yaml`, `decisions.yaml`, `context_replay.py` + `evals/fixtures/`, `stt_ab.py` (one-off or superseded tools; the rate-limit helpers moved into `evals/smoke.py`); `evals/checks.yaml` merged into `regression.yaml` (33 conversations); `docs/manual-test-scripts.md` merged into `docs/test-call-scripts.md` (Part D); unused `audio.ms_to_bytes`, `audio.strip_wav_header`, `Player.has_pending`, `ui/components/ui/separator.tsx` removed; the four template helpers now share `personal.fill()`; `_ms` defined once. Not merged (they differ): the two `_norm` (review vs evals), the two `words` (one keeps apostrophes); `_f` kept in both adapters (merging would tie the mock bank to the HTTP adapter). Offline **394 pass**; UI build passes. Docs and comments updated.
**Reply language (late 17 Nov, live call 7fa9b902):** an English "hey" was transcribed as "हो"; any Devanagari letter (and the LLM's label) made the reply Hindi for the whole English question. Now code decides per turn from the caller's words (`conversation.reply_language`): an explicit request first; fillers and greetings ignored (a filler-only turn keeps the call's language, on the first turn the language picked at the start); mostly Devanagari → Hindi; mostly Latin → English or Hinglish, never Hindi. Guide §27.3. Offline **395 pass**; R09, R11, R03b, R14-hi, C3 rerun from the cache, all right (no Groq requests).
**Call 264e51d7 (late 17 Nov, Edge TTS; code changed, no tests run by the owner's choice):** the judge flagged turns 3 and 4. "How much have I **paid** across all my loans?" got the outstanding balances (the LLM said `amount_due`, which the repaid rule skipped); "how much is left and how many instalments are left?" lost the count; "what about the instalments?" got only the next EMI. Now `understanding.loan_facets()` reads which loan figures the caller asked for (amount repaid, outstanding, EMIs paid/left) from their words and each one gets its own request (the LLM's own reading, e.g. next EMI, is kept first); the last EMI is said once when the debit day is the due day. "Mobile ending 0101" was right (Ravi's mobile 9000000101; his loan also ends 0101). **The 15 s gap after verification** was Edge TTS: the live call synthesises "Thank you, you're verified" before it starts the waiting answer, and Edge took about 13 s (no error logged). Left as is by the owner (Sarvam takes 1-2 s); a fix would be to play that line from the phrase cache.
**Judge rubric (late 17 Nov):** `review.RUBRIC` now lists the failure patterns found on 16-17 Nov (wrong figure for what was asked: paid vs outstanding, last vs next EMI, debit vs due date, hold vs deducted, pending vs reversed; a part of a question unanswered; lost thread on follow-ups; card consent and scheduled requests; invented promises and duplicate transfers; reply language; unnatural phone speech; an inactive balance shown as usable), each mapped to a finding category, and says bank-data answers are templates (fix_area router or bank_templates, not answer_prompt). critical_failure also covers an action different from what was asked. Judge runs after the call: no effect on call latency; about 450 more prompt tokens per judged call. **Real bank APIs:** guide §29.5 now lists the fields the answers rely on and what happens without each.
**18 Nov (code, no tests run by the owner's choice):** (1) two requests in one sentence: `understanding._add_missing` adds a plainly said second request the router left out ("my balance", "money got deducted" as a report, not a question); (2) "How do I block my card if I lose it?": `guard.asks_how` makes a how-to about a card action a product question (no verification, nothing done); (3) the variant question now says how many there are and names four ("We have 13 savings accounts, like …"), the matched ones first, then the knowledge base's catalogue (`retrieval.family_variants`); (4) pending-work A7, a pause splitting one question: an opener with no question ("I have a question.", "mujhe kuch poochna hai") counts as unfinished (`turntaking._OPENER`), caller speech within 1.5 s of the reply's first audio is merged with the last question (`session.CONTINUE_MERGE_S`), and a new personal question within 60 s of a valid code keeps that code (`auth.REUSE_CODE_S`, event `otp_reused`). Brain restart needed. **Evaluation report:** `docs/evaluation-report.md` (all saved numbers, per language from the 3 Nov golden run, judge averages over 23 calls; how to refresh). The older smoke and benchmark report files are no longer in `evals/reports/` (the 84% routing figure is from this tracker).
**SageMaker search (18 Nov, owner's plan):** `SEARCH_PROVIDER=sagemaker` + `SAGEMAKER_ENDPOINT`. The search server image runs as a SageMaker real-time endpoint: `serve` in the image runs `python -m app search --sagemaker` (port 8080, `/ping`, `/invocations` with `op` info / embed / rerank; only in that mode); the brain's `SageMakerSearchClient` (`adapters/search_http.py`) calls InvokeEndpoint with the AWS credential chain, shares the model check with the HTTP client and maps every AWS error to the usual search error. Same model code in local / http / sagemaker: no re-indexing when switching. Steps in `deploy/README.md` (ECR, model, endpoint config with ml.g4dn.xlarge and the al2023 gpu-4-1 image for the CUDA 12.8 build, check, `.env`). 3 new offline tests (fake SageMaker runtime), search server tests 10 pass. Not run on real AWS yet. TTS_SPEED_PCT comment corrected (Edge only).
**AWS pilot pack (18 Nov, docs + DDL, no code change):** `deploy/database/01_bootstrap.sql` (login `au_voice_app`, database `au_voice`, pgvector; run by the RDS master user) and `02_schema.sql` (all 33 tables of migrations 001-011 plus `corebank.seed_info` and the bge-m3 HNSW index, created as `au_voice_app`, migrations recorded as applied), for the manager to run. One database, two schemas (`public` app data, `corebank` mock bank). Tested on a throwaway Postgres 16: both files run, the app as `au_voice_app` skips all migrations, seeds and re-seeds (TRUNCATE), owns every table. `deploy/aws-pilot-guide.md`: RDS/S3 settings, why the app login must own its tables, later migrations (app applies, or DBA first), what is deployed (2 Dockerfiles, 3 images: au-brain without models + au-ui on one EC2 with Docker Compose behind an ALB, au-search on SageMaker), who talks to whom, security groups, IAM, what each kind of change rebuilds and restarts, rollback, and a CI/CD design (PR tests, build to ECR on main, deploy over SSM, approval for production, manual search pipeline). `deploy.sh`, the compose file and the workflow are sketched in the guide, not added. Diagrams (owner: Mermaid in md only, no HTML; the HTML page and its artifact were deleted): `docs/diagrams/diagrams.md` §11 solution architecture (layers like a vendor slide), §12 AWS pilot deployment, §13 DFD level 0, §14 DFD level 1 (processes, data stores, trust boundaries), §15 deploy and CI/CD. All 5 render (Mermaid 11.4.1).
**draw.io diagrams (18 Nov, owner asked, presentation style like a vendor slide):** `docs/diagrams/drawio/` 01 solution architecture, 02 AWS deployment (official AWS icons, resolved styles so the desktop app shows them), 03 DFD level 0, 04 DFD level 1, 05 CI/CD, 06 logging, monitoring and audit (where each log is stored), 07 database layout; plus §16 card-block sequence (Mermaid). Listed in `diagrams.md` (draw.io versions).
**AWS layout decided (19 Nov, owner): brain + UI on ECS Fargate, search on SageMaker** (dedicated GPU = the alternative, `SEARCH_PROVIDER=http`). The brain never loads the search models on AWS (`local` is laptop-only). `deploy/ecs/brain-task-definition.json`: Fargate, 2 vCPU / 8 GB, 40 GB task storage, no GPU / `DEVICE`, `SEARCH_PROVIDER=sagemaker`, `SAGEMAKER_ENDPOINT=au-search`, `SPEECH_FALLBACK=false`, `S3_PREFIX`, health grace 120 s. `search-task-definition.json` now uses the `au-search` image (kept for the GPU option). `deploy/aws-pilot-guide.md` §3–5 rewritten for ECS (task definitions instead of compose, execution/task roles, services table with min 0 % / max 100 % and circuit-breaker rollback for the brain, ECS deploy in CI/CD). `deploy/README.md` and the Dockerfile header comments updated. draw.io 01 (label), 02 (ECS cluster box, Docker volume removed, ECS icon instead of SSM), 05 (register revision → update-service → circuit breaker). Mermaid diagrams not changed (owner's choice). No app code changed; nothing run on AWS.
**Revised the same day (ADR 0028): brain on ECS with EC2 capacity (Managed Instances, `c7i`), UI on Fargate, search on SageMaker.** Fargate dropped for the brain: it pulls the several-GB image at every start (deploys and replacements take minutes), no CPU choice, about ₹15–35k/month more; EC2 caches the image and matches the cost sheet. Brain task definition: `requiresCompatibilities` EC2 + MANAGED_INSTANCES, 2 vCPU / 3.5 GB (4 per c7i.2xlarge), no extra task storage. Pilot guide §3–5, deploy README, Dockerfile header and draw.io 01/02/05 updated. Open: the Managed Instances fee for Mumbai, whether AU security requires host agents (then our own EC2 group), whether `ulimits`/`initProcessEnabled` are accepted on Managed Instances. Cost sheet: the GPU search lines are still EC2 g6.xlarge × 2 (stale for SageMaker); see the open questions.
**Open:** "And when is the next one?" after a balance answer is read as the next EMI (the only "next" in the call); the
PM asks for a clarify only when genuinely ambiguous - left as is. Customers with zero transactions, only incoming UPI,
only failed transactions or several holds don't exist in the mock data (not added).

### 16 Nov (5): timeline corrections (documents only)

- `docs/diagrams/diagrams.md` §7: the barge-in was drawn too early. The code ducks the agent after 0.35 s of caller
  speech and decides only after the caller pauses 0.3 s (or after 1.5 s of speech): now duck 7.35 s, stop ~8.0 s,
  next turn ~9.6 s, new reply ~11.3 s. §8: the always-on band was the detector alone (39%); with audio handling it is
  ~50% for 9 calls (sheet: ~57% with turn bursts). Collisions cost milliseconds, not 0.1 s (bars drawn wider).
  Excalidraw versions (sequence diagrams) given in chat; Excalidraw doesn't import Gantt.

### 17 Nov (8): diagrams redrawn and two added (documents only, no code change)

`docs/diagrams/diagrams.md` now has 10 diagrams + an appendix. All 12 Mermaid blocks render (Mermaid 11.4.1).
- **§7 and §8 are now the Excalidraw board's charts A and B** (owner's choice, from the pasted board JSON; same
  bars, labels and times). Three corrections to match the code, listed under each chart: A's speech-to-text is one
  upload after the caller pauses (not "live"); A's listening runs the whole call (it stopped at 3.5 s); B's
  "Container CPU" gains the always-on listening bar. A first redraw earlier the same day (numbered steps) was
  dropped. The 16 Nov versions (barge-in, bursts colliding) are in the appendix (A1, A2).
- **New §9 sizing chain** (Servers sheet: 0.92 ms → 6.3% a call → 9 a container → 36 a server → 4 servers for 100) and
  **new §10 monthly cost** pie (Cost sheet read 17 Nov: ₹28,53,051, ₹7.61 a call; Sarvam 69.5%, Bedrock 15.4%,
  AWS servers and services 15.2%).
- Small fixes in place: §2 verification is the keypad code (was "KYC: DOB + address"); §3 grouped into 5 stages
  with colours and plainer labels; §4 328 tests (was 316) and colours; §5 server counts from the sheet.
- The new Excalidraw link the owner shared (17 Nov) is identical to the 14 Nov board. Its sizing note (~25 calls per
  vCPU, 3 servers for 400 calls) is not copied: §9 has the current sizing (9 per container, 13 servers for 400).
- Found, not changed: PROJECT-GUIDE §26.6 still says ₹28,59,913 and ₹7.63 a call; the sheet now says ₹28,53,051
  and ₹7.61.

### 17 Nov (9): echo on laptop speakers, investigated; microphone settings changed (UI only)

**What the owner heard.** Agent voice leaking into the mic; the agent "not played fully"; caller on the left speaker,
agent on the right.
- **Left/right is the call recording, by design** (`recordings.py`: caller left, agent right). Not a live-call bug.
- **Measured in the 12 latest recordings** (decrypted locally, numbers only). The browser's echo canceller works:
  no linear echo (correlation ≈ 0), and the mic is silent most of the time the agent talks. What is left: about 10–24
  short fragments per call (100–350 ms, about −30 dB, ~8 dB under the caller's voice). That is laptop-speaker
  distortion. Only 8 fragments in 12 calls were long enough (≥ 350 ms) to start a barge-in check. Those lower
  the agent's volume to 30% for a moment, which is the likely "not played fully".
- **A dedicated WebRTC server would not change this.** Echo is removed on the device with the speaker and mic. The
  page already plays the agent through a WebRTC loopback, so the browser already treats it as a remote voice (ADR 0012).
- **Tried and dropped: an echo gate in the capture worklet.** It was simulated on the 12 recordings. It removed
  50–60% of echo fragments but also 40–55% of real caller interruptions, so barge-in would get worse.
- **Windows:** one mic (Realtek "Microphone Array"), no Stereo Mix or loopback device. Windows audio enhancements are
  on (the default). Not changed: that is a system setting for the owner to try.

**Changed (`ui/lib/mic.ts`, used by the live call and the agent desk):** the mic settings are the same as before (echo
cancellation, noise suppression and auto gain on, mono). New: the console shows what the browser really applied
(device, echo-cancellation mode and the values offered, auto volume, noise suppression). The full settings and
capabilities go to the browser console.

**Tried the same day and reverted after a web check** (sources: Chromium blink-dev "Intent to Ship:
echoCancellationMode", Chrome blog "More native echo cancellation", Zoom developer forum on Chrome-wide echo
cancellation):
- **Echo cancellation `"all"`** (Chrome 141+) also removes audio from other apps. It adds nothing here: the agent's
  voice is the only thing we play, and it is already covered. Which canceller Chrome uses for it on Windows is not
  documented, and Chrome's own early tests of the Windows system canceller were weak.
- **Auto gain off.** No source shows it reduces echo, and it risks a too-quiet caller.

How web meeting apps handle this: the same way we do. Remote voices arrive over WebRTC, Chrome's echo canceller uses
them as its reference and removes them from the mic. Small leftovers remain on laptop speakers there too. In a meeting
a person ignores them; our speech detector hears them, which is what the 350 ms minimum and the echo-text match on
the server are for.

TypeScript and `bun run build` pass. **Live:** the UI picks it up on reload, with no brain restart. The UI dev server on
:3000 was found stopped and was not restarted.

**Review of mic and playback (same day).**
- One mic stream per call (`openMic()`), one playback path, and the mic stays open while the agent talks.
- Fixed three places where the mic could stay on:
  - call setup failing after the mic opened (`start()`/`join()` now release);
  - leaving the console page during a live call (unmount stops it);
  - leaving during a voice note.
- There are no reconnects: a closed WebSocket ends the call.
- The console now logs the requested constraints, the mic's capabilities and the applied settings.
- Loopback checked in Chrome 156 (built-in browser, test tone, no mic): connected in 2.0 s, audio received, nothing
  concealed, about 48 ms jitter buffer, `<audio>` playing.
- Not verifiable without a mic: that Chrome's echo canceller uses this audio as its reference.
- Risk noted, not changed: connecting took 2.0 s against a 3 s timeout. A slower start falls back to direct playback,
  and the console then shows the "echo-safe playback unavailable" note.

### 18 Nov (1): tests and evals trimmed (code; brain needs a restart)

- **Removed the eval API and `brain eval`** (`app/api/evals.py`, the CLI command, `EvalRunner.runs()`, the `requests.http`
  section). They had no pacing or replay cache, so one run could spend the day's Groq requests, and a deployed endpoint
  should not spend LLM quota. The golden set runs only with `python -m evals.golden_run` (paced, cached).
  The `EvalRunner` service stays; its test now calls it directly (`test_golden_runner_scores_and_writes_a_report`).
- **Removed `tests/test_live_llm.py`** (2 real-Groq tests, always skipped) and the `live` marker. The smoke set covers
  the real LLM better. The test command is now plain `uv run pytest`; `-m "not live"` still works.
- **Old local reports deleted** in `evals/reports/` (19 files, gitignored). Kept the latest of each kind:
  `smoke-20261117-114140`, `regression-20261117-230251`, `checks-20261117-161741`. Older report names quoted above
  no longer exist on disk.
- **Kept, checked:** `app/cli.py` is needed: `python app.py`, `python -m app` and the Dockerfile start the server through
  it, and `search`, `clean-slate`, `upload`, `sarvam-check` are in use. `app.py` (3-line launcher) and `app/main.py`
  (the FastAPI app that tests, evals, uvicorn and Docker import) stay separate.
- Docs: smoke set size corrected to 24 (was "40"); stale routing-set and deleted-script references fixed in
  `pending-work.md`, the briefing and PROJECT-GUIDE.
- **Numbers:** offline **395 pass** (395 + 2 skipped live before; the eval test was converted, not lost). No Groq calls.
  **Live:** the brain still serves `/v1/evals` until it is restarted.

### 19 Nov (1): several questions in one sentence: the call agenda (code; brain needs a restart; tests written, NOT run)

**Why:** the 17 Nov smoke run failed both multi-request items (0/2). Cause, from the cached LLM replies:
- **A3** ("block card 6712 … and then my balance in both accounts"): the first reading found both requests. At the
  keypad code the code kept only a text string; after the code the router read it again, next to a history holding the
  same sentence, and returned only the block. The balance was lost.
- **A1** ("money got deducted, check my balance"): the router made one balance request (arguably right).
- Also: requests were capped at 3 by the LLM's output format (a 4th and 5th vanished); leftovers ran by themselves at the
  start of the next turn; "no, that's all" did not clear them.

**What was built** (guide §24.4; owner-approved plan of 19 Nov):

| Part | What | Where |
|---|---|---|
| Call agenda | Every separate thing asked, with a status (pending / auth / waiting / done / declined / skipped / dropped / handed over / failed). Rules in code: 2 short reads together; 3+ → "You've asked about three things…", one at a time, "Shall I go on to X?"; fraud first; yes / no / skip / choose / a new question then "Shall I go back to X?"; offered twice → let go; one goodbye reminder; hand-over → the desk brief lists what is left | `services/agenda.py` (new, pure) |
| Finding the questions | Router `asks`: every question (up to 8) in the caller's own words + a short spoken label; first 3 detailed as before, the rest understood when their turn comes. `agenda_reply` / `agenda_pick` read "skip that one" / "the second one" | `services/understanding.py` (schema, prompt, `parse`, `read_asks`) |
| Check in code, any language | A quote must be the caller's words (≥ 60% of its words in what they said) or it is dropped; a label may not carry digits the caller didn't say; if ≥ 6 words and ≥ 35% of the turn are in no quote, one more router call ("Already found: …") adds what was missed (`coverage_retry` in the trace) | `understanding.coverage_gap`, `Conversation._second_look` |
| Verification in the middle | Items waiting for the code resume from the first reading (`agenda.resume_after_verification`); the text is not read again | `conversation.turn` |
| Owner decision A1 | "Money got deducted" + a balance → the balance, then "Is there a payment you don't recognise that I should look into?" (yes → the unrecognised-debit flow). Without a balance: as before | `understanding._add_missing`, agenda follow-up item |
| Versioned state save | `CallState.v` inside the existing `sessions.state` jsonb: a save succeeds only if nobody saved in between, else read, merge (a settled item is never reopened), retry. **No migration, no new column.** The agenda is also saved right after it is built, so a barge-in can't lose it | `services/state.py` |
| Record and review | Each turn's trace has `data.agenda`; the call record has `agenda` (labels + statuses); new review code check `every_request_answered` (warn) | `audit.call`, `review.code_checks` |

- **Memory:** the agenda lives in Postgres, ≤ 20 items per call (≈ 0.5–1 KB each); nothing is kept in the process between turns.
- **Latency:** no extra LLM call in the normal case. "Yes, go on" to an item already understood makes **no** LLM call
  (read in code). Extra calls only: the 4th+ question when its turn comes (one router call, like any turn), and the
  second look when the coverage check fires (logged). The router writes slightly more (asks + labels): **not measured yet**.
- **Tests:** new `tests/test_agenda.py` (17 tests: rules, quote and coverage checks incl. Hindi, versioned save and merge,
  review check, and whole conversations: verification in the middle, five questions, skip / choose / let go, coverage
  second look, goodbye reminder). Updated `test_understanding.py` (2 tests now expect "Shall I go on to your balance?"
  instead of the balance running by itself) and the fake LLM (`asks`, `agenda_reply`, "Already found"). **Not run**, by
  the owner's choice: run `uv run pytest`.
- **Evals:** smoke A1 now expects `kinds: [personal]` + a second turn that must say "recognise"; regression pack section
  F (5 conversations: five questions, verification in the middle, Hinglish skip / choose, a question in the middle,
  Hindi goodbye reminder). **Not run.** The prompt changed, so the replay cache misses: the next smoke run makes real
  Groq requests (about 24–50).
- **Behaviour change to know:** 3+ questions are no longer all answered in one long reply; leftovers are offered, not
  run automatically.
- **Live:** nothing until the brain is restarted.

### 19 Nov (2): solution diagram redrawn: orchestrator decides, speech layers sense and produce (documents only)

- `docs/diagrams/drawio/01-solution-architecture.drawio` and Mermaid §11 in `docs/diagrams/diagrams.md`:
  - **End-of-turn and barge-in decisions and playback** moved from the speech layers into the **call orchestrator**. This matches the code (`voice/session.py`, `voice/turntaking.py`, `voice/player.py`).
  - Speech-in now holds only speech activity detection (Silero, local) and the Sarvam speech-to-text client. Speech-out holds spoken form, pronunciation, the phrase cache and the Sarvam text-to-speech client.
  - **Network boundaries shown:** VPC (private subnets: app, staff UI, database); AWS services outside the VPC reached privately; **Sarvam outside AWS, through the NAT gateway** (the only call that leaves AWS), matching `deploy/aws-pilot-guide.md`.
- Checked: XML valid, every edge resolves, no overlapping boxes (script check plus a rough rendered preview). Not opened in draw.io itself, and the Mermaid was not rendered.
- **Open question (owner not yet convinced):** Postgres vs S3 for the call record. The discussion on 19 Nov found that Postgres today also holds the in-call state, read and written every turn (`services/state.py`, `store.py`). Not decided; nothing built.

### 21 Nov (1): storage decision note for the manager (analysis only, no code change)

- Published "Do We Need Postgres?": https://claude.ai/artifact/59VHugfYaUNQkTBDRcS4ic (private until shared).
- **Proposal (option C):**
  - in-call state in container memory (about 80 database statements per call today);
  - the call record to S3 at hang-up (write-once), with verification, card-action and hand-over events written at once;
  - analytics on Parquet + Athena;
  - a small Postgres only for the KB, a 90-day call index and the review/fix workflow.
  - No Redis, no MongoDB.
- **Rough monthly cost at 300k calls a day:**

  | Design | ≈ $/month |
  |---|---|
  | Today | 1,650, growing |
  | C (proposed) | 350 |
  | DynamoDB | 165 |
  | S3 only | 220 |
  | MongoDB Atlas | 550–800 |

  Recordings are excluded (the same in every option). Several prices are US list prices, not Mumbai.
- **Needs before building:** the owner's and manager's decision; a new ADR (touches ADRs 0003, 0014, 0021, 0026); a latency measurement before and after (estimated 10–30 ms saved per turn, not measured); the retention period from AU compliance; the real call volume (docs still say a 12,500/day placeholder).

### 21 Nov (2): storage without Postgres, re-assessed (analysis in chat, no code change)

- **New planning figures from the owner:** 4 lakh calls a month, 200 calls at the same time, 4-minute calls. The cost workbook (17 Nov) still types 100 at the same time. 200 at the same time with 4 lakh a month means 22.5% of a day's calls in the busiest hour (the workbook assumes 12%).
- **Owner's frame:**
  - QA is never immediate;
  - supervisors use CloudWatch;
  - the human agent reads the call from S3 by call ID;
  - customer data comes from AU;
  - the desk is Cisco.
- **Measured on the local database (read-only):**

  | What | Measured |
  |---|---|
  | One turn row | ≈ 3.3 KB |
  | Call state + facts | ≈ 0.2–1.4 KB per call |
  | Knowledge base | 220 active chunks, 139 KB of text, about 1 MB with vectors |

  Keyword search uses `to_tsvector('english')` with `ts_rank_cd`, which is not BM25.
- **Conclusion given (not decided):** under that frame Postgres is not required.
  - Call state stays in memory (no Redis).
  - Records and the key events go to S3; the key events are written at once.
  - KB search runs in memory in the search server (vectors + BM25), with its files in S3.
  - Analytics via Firehose → Parquet → Athena.
  - Saves about ₹45k a month (≈ 1.5% of the total) and some latency per turn (not measured).
  - The cost is rework: storage layer, `/calls`, `/kb`, the offline tests, and re-running the retrieval evals.

### 20 Nov: mock bank switch, Bedrock API key from `.env`, CPU timing (code; brain needs a restart)

- **`MOCK_BANK`** (new, default `false`; owner's decision): `true` starts the embedded mock bank and seeds the demo
  customers. `false` with `BANK_BASE_URL` set calls that gateway; `false` with no URL means no bank (personal questions go
  to a person, a warning in the log). `.env.example`, this laptop's `.env` and `deploy/ecs/brain-task-definition.json`
  set `MOCK_BANK=true`, so the local demo and the AWS pilot behave as before. `tests/conftest.py` sets it too.
- **`AWS_BEARER_TOKEN_BEDROCK`** (new, `SecretStr`, never printed): a Bedrock API key can go in `brain/.env`. The brain
  hands it to boto3, which reads it only from the process environment. Empty on AWS (IAM role).
- **CPU timing** (Ryzen 7 5800H, `DEVICE=cpu`, measured): reranking 16 passages 12.8-14.2 s (8 passages 6.9-8.1 s);
  one query embedding ~0.2 s; the per-turn warm-up ~0.4 s (hidden behind the understanding call). Only KB turns rerank:
  bank-data, action, small-talk and hand-over turns do not.
- **Test call:** `docs/test-call-scripts.md` Part E: Ravi, 7 bank-only turns, 2 KB turns, ending with a person. Not run yet.
- **Offline tests: 413 pass, 3 fail** after this change; all 3 fixed in the entry below.

### 20 Nov (2): the three failing tests fixed (code; brain needs a restart)

- `test_agenda.py` five questions: the **test** was wrong. The transactions answer ends in its own question ("Want the
  other 2?"), and by design the next agenda item is never offered on top of another question. The test now says yes to
  the other 2 first, then expects "Shall I go on to your loan?".
- `test_agenda.py` skip / choose: a **real gap**. A single-transaction answer did not say which account, even for a caller
  with two. Now it does: "... a cash deposit of ₹1,500 into your account ending 4512." (`services/personal.py`).
- `test_voice_session.py` barge-in: a **real bug from the A7 fix (18 Nov)**, also failing on the last commit. Speech within
  1.5 s of the reply's first audio was always joined to the last question, so "ruko, what about senior citizens?" became
  "What is the minimum balance ...? ruko, what about ...". Now, once the agent is speaking, a stop word first (stop, wait,
  ruko, bas, sorry, no, nahi) makes it a new turn. Before the agent speaks it is still a correction and is joined (its
  test still passes). `voice/session.py`. Needs a live voice check.
- **Offline tests: 416 pass, 0 fail.** Smoke set not run.

### 20 Nov (3): models without `.bin` (office laptop blocks `.bin` downloads)

- `scripts/download_models.py` downloads `.safetensors` only. bge-m3 comes from Hugging Face's conversion bot
  (SFconvertbot, `refs/pr/130` on BAAI/bge-m3), pinned to commit `9a0624b8`; SHA-256 `993b2248...`.
  **Checked:** all 391 tensors are exactly equal to BAAI's `pytorch_model.bin`, so embeddings are identical and the KB
  needs no re-indexing. No app code change (the loader already prefers `model.safetensors`). The Docker build uses the same script.
- GGUF and ONNX looked at; not built. ONNX (BAAI's own `onnx/` for bge-m3, onnx-community for the reranker, plus
  `onnxruntime`) would be ~100-120 lines in 6 files behind a `SEARCH_RUNTIME` setting; speed on CPU not measured.
  Waiting for the owner's decision.

### 20 Nov (4): ONNX Runtime option built and measured (code; brain needs a restart only to use it)

- `SEARCH_RUNTIME=torch|onnx` (default torch); `OnnxEmbedder` / `OnnxReranker`; `download_models.py --onnx` (BAAI's
  `onnx/` for bge-m3, onnx-community fp32 reranker, pinned + SHA-256); new dependency `onnxruntime` 1.32. ADR 0029 (Proposed).
- **Measured on CPU, 12 KB questions, real search:** ONNX fp32 gives identical results (12/12 same top-5 order, scores
  equal to 5 decimals, embedding cosine 1.000000). Query embedding 232 -> 80 ms. Reranking 16 passages: no gain
  (~17 s vs ~17 s). int8 reranker (downloaded for the test only, not wired in): 1.8x faster (~11 s), top-1 12/12, top-5 4.92/5,
  scores differ up to 0.16. 8 candidates instead of 16: 2.1x faster, top-5 only 3.75/5. CPU timings varied 13-20 s between runs.
- **Conclusion:** no CPU option makes a KB answer voice-fast; a GPU is still needed for that. Bank-data turns are fast either way.
- Offline tests 418 pass. Smoke set not run (search results unchanged).
- The local Postgres reported crash recovery when the test opened it (an earlier unclean shutdown); it recovered on its own.

### 20 Nov (4): Chroma as a vector store for a Postgres without pgvector (code only; **not run or tested**, owner's call)

- Why: the office laptop's Windows Application Control blocks pgvector's `vector.dll` (pgserver's copy and any
  self-built one), so the owner's own Postgres 16 (pgAdmin) can't have pgvector. Chroma's binaries do run there
  (owner checked: create, add and query worked).
- `VECTOR_STORE=pgvector` (default, unchanged; AWS) | `chroma`. In chroma mode Postgres still holds everything,
  including every vector (`chunk_embeddings.embedding` as `real[]`). Chroma (`data/chroma`, in-process, telemetry off)
  is only the search index. Startup `sync` repairs Chroma from Postgres (no re-embedding).
- Files: `config.py` (setting), new `services/vectors.py`, `db.py` (extension only in pgvector mode; column type
  converted either way), `migrations/001_init.sql` (no `CREATE EXTENSION`; column created as `real[]`, which
  pgvector mode turns into `vector` at startup), `documents.py` (Chroma writes inside the Postgres transaction;
  purge deletes the document's vectors), `retrieval.py` (`_dense_chroma`, filtered to active versions),
  `container.py`, `cli.py`, `pyproject.toml` (optional extra `chroma`; `uv.lock` not updated yet).
- Back to pgvector later: set `VECTOR_STORE=pgvector` on a Postgres with the extension; startup converts the column.
- **Skipped:** tests for chroma mode, `pytest`, the smoke set, latency numbers, an ADR (cheap to reverse; ADR 0003
  stands for AWS). Owner asked for no runs. To check: `pytest` (pgvector path), then a chroma-mode start on the laptop.

### 20 Nov (5): `VECTOR_STORE=numpy` (code only; **not run or tested**)

- Chroma ran once on the office laptop (15:39), then its Rust DLL (`chromadb_rust_bindings`) was blocked by Application
  Control at 15:55. Cause not known (file rewritten by a reinstall, or a policy/reputation change); Event Viewer →
  CodeIntegrity → Operational shows it. Extra compiled libraries are unreliable on that laptop.
- New `NumpyVectors` in `services/vectors.py`: every vector in memory, exact cosine search (one matrix product),
  same methods as `ChromaVectors`; `sync` loads the vectors from Postgres at startup (binary transfer). No new library.
  `db.py` and `container.py` accept `numpy`. Same database as chroma mode: no migration, no re-upload.
- Expected: ~10 ms per search at ~50,000 chunks; exact search, so at least as accurate as HNSW. **Not measured.**
- **Skipped:** tests, `pytest`, smoke set. Also open: the phrase cache can store fallback (Edge) audio under the Sarvam
  tag after a one-off Sarvam failure (`voice/phrases.py:86`); owner told to clear `data/tts-cache`; code fix offered.

### 20 Nov (6): `USE_SYSTEM_CERTS` (code only; **not run or tested**)

- Office laptop: Sarvam calls failed with `CERTIFICATE_VERIFY_FAILED` (the office proxy re-signs HTTPS with the
  company root certificate; Python's certifi list doesn't have it), so every request fell back to edge-tts.
- `USE_SYSTEM_CERTS=true` calls `truststore.inject_into_ssl()` at the start of `container.build`, so all HTTPS
  (httpx, boto3, aiohttp) trusts the Windows certificate store. New dependency `truststore` (pure Python, no DLL;
  pip vendors it). Off by default; AWS unchanged.

### 20 Nov (7): LLM output-token caps removed (code; **live only after a brain restart**)

- Live call fbe5b9aa: "my last 5 transactions" failed with `json_validate_failed`. The understanding call had
  `max_tokens=800`; gpt-oss spent it all reasoning and returned nothing. 4 of 555 recorded understanding calls hit
  800 (median 430, p90 543, p99 744). The cap was ours, passed to every provider (Groq and Bedrock).
- Removed every output cap: understanding (800, and the lenient retry), spoken answer (700, which could cut a reply
  mid-sentence), handover summary (350), judge (1,600). `max_tokens` is now optional in the `LLM` port; when it's
  absent, neither adapter sends a cap.
- Checked on Groq: a request with no cap is accepted and doesn't reserve a large token budget (2 test requests).
- Kept on purpose: timeouts (LLM 20 s, retry wait 4 s, bank 3 s, search 3 s: a caller is on the line) and the
  input trims on the summary (8,000 chars) and judge (9,000 chars) prompts (8k tokens/min on the free tier).
- Offline tests: 418 pass. Smoke: **not run** (stopped at the owner's request). The replay cache key included the
  cap, so the next smoke run starts with a cold cache.
- Not changed, owner to decide: transactions are already read in parts (3, then "want the other N?"), but a caller
  who asks for a number hears up to 5 at once (PM review, 17 Nov). The owner suggested 2–3 then asking.

### 20 Nov (8): mute button in the live call (UI only; live now, the UI hot-reloads)

- The console (`/`) shows **Mute / Unmute** next to "End call" while a live call is on. Mute disables the mic track,
  so the browser sends pure silence: frames keep flowing (the server's turn-taking is unchanged), but no room sound
  reaches the brain. Every new call starts unmuted. Files: `ui/lib/live-call.ts` (`setMuted`), `ui/app/page.tsx`.
- Checked: type-check passes, page loads with no console errors. **Not checked:** a real muted call (the browser
  pane blocks the microphone). No brain change, no restart needed. The agent desk (`/desk`) has no mute; not asked.

## Completed (before Phase 4)

| Phase | What | Where it's described |
|---|---|---|
| 1 (MVP 1) | General questions by text and voice: ingestion, hybrid retrieval + rerank, grounded answers with figure check, golden-set evals | `PROJECT-GUIDE.md` §5–8, §14 |
| 2 (MVP 2) | Caller verification (registered mobile + keypad code), personal data answers, call memory | `PROJECT-GUIDE.md` §9–10 |
| Voice | Silero VAD, Whisper STT, Edge TTS (Neerja / Swara), streaming replies, barge-in A1–A4, ducking, phrase cache | `PROJECT-GUIDE.md` §11 |
| Audit | Hash-chained turns, grounding levels, encrypted call recordings, call events, alerts, `/calls` page | `PROJECT-GUIDE.md` §13–14 |
| UI | shadcn across `/`, `/kb`, `/calls`, `/desk` | `ui/README.md` |
| 3 | Answer decisions (answer / clarify / conflict / no answer); mock core bank with 7 scenario customers; card actions with confirmation and read-back; complaints and disputes; fraud journey; 10 departments; handoff with consent, hold music, agent desk, live audio bridge, callbacks; pronunciation list; policy pack; benchmark tooling | `PHASE-3-PLAN.md`, `PROJECT-GUIDE.md` §23 |

### Fixes made after the 5 Nov live test call (in code; live only after a brain restart)

- `KeyError: 'section'` on bank-data answers in voice.
- "No, don't do that. Show me…" now answers the new request instead of only cancelling.
- Fraud words ("don't recognise", "stolen", "shared my OTP", Hindi and Hinglish) send the turn to the fraud journey.
- Transaction wording: no "last 1", no "paid (failed)", "at" for shops and ATMs.
- Prompt rule: a general product question answered from one variant must clarify.
- Desk: no empty "Completed by …: ." line.
- Understanding: `personal` and `action` are nullable (lower latency); "this account / FD" counts as personal; the detail decides the topic.

## Live state (check before testing)

- Brain code: 16 Nov 2026 (search server B2.1, live-call limit, recordings via the file store B2.2, startup lock; local behaviour unchanged). If the running brain started before that, restart it (`python app.py` in `brain/`).
- **328 offline tests** pass (16 Nov): `uv run pytest -m "not live"`. UI production build passes (12 Nov).
- `.env` runs both LLM steps on `openai/gpt-oss-120b` (code default for understanding is 20b; ADR 0007 notes). The judge is 20b.
- Groq free tier: 8,000 tokens a minute and a daily token cap per model. Expect 429s on fast product questions.

## Latest numbers

| What | Result | Date and notes |
|---|---|---|
| Routing on the real LLM (partial run) | **32 / 38 right (84%)** | 5 Nov. The run stopped at the Groq daily limit; 16 items not run. Misses: "account maintained?", Hindi charge question, "will my FD renew", "was my card payment received", "annual fee on my card", "upcoming debits". |
| Golden set, best clean run | pass 72.4%, hit@3 91.1%, hit@5 93.7%, MRR 0.81, fact accuracy 78.1% | 3 Nov, older prompt. Later runs that day were distorted by rate limits and a GPU slowdown (since fixed). |
| Understanding latency | ≈ 0.9 s p50 (old schema); the new schema measured +190 ms before it was made nullable, not re-measured since | 5 Nov |
| Retrieval | ~85 ms warm | after the GPU warm-up fix |

| First audio of the real answer (voice) | **3.6 s typical, 5.8 s slow**; "one moment" at 2.3 s | 12 Nov, 22 recorded turns, laptop + Groq + Edge TTS. Biggest parts: end-of-speech wait 1.1 s, understanding 1.35 s, search 0.8 s |

Re-measure after any voice or prompt change (pending-work A3).

## Issue tracker

An issue counts as done only when its **acceptance test passes**, not when code changes. Plan sections are in PHASE-4-PLAN.

**Statuses below date from 6 Nov.** Phase 4 fixed most of these in code (see the log); live acceptance tests were not all re-run (pending-work A5).

| # | Issue (from the call review, §1) | Root cause | Decision | Status | Acceptance test |
|---|---|---|---|---|---|
| A | Balance request answered with a guessed pending UPI and a complaint offer | One intent per turn; the issue journey guessed the transaction | requests list (§3.1); never guess the transaction (§3.4); 19 Nov: balance first, then "a payment you don't recognise?" (call agenda) | fixed in code 19 Nov, tests not run | "Money got deducted, check my balance" → balance first, then asks which debit; no complaint offered |
| B | "Last 5" gave 1 + 5 from two accounts | Limit applied per account | Merge, sort and cut to N, or filter if an account is named (§4.2) | open | Exactly 5, newest first, each with its account ending |
| C | "₹10,000 paid (failed)" | Template wording | One status table (§4.2) | fixed in code, not live | "paid" never next to failed or pending |
| D | `KeyError: 'section'` | Bank citations have no section | `.get` fallback | fixed in code, not live | Voice bank answer with no error event |
| E | "No… show me transactions" only cancelled | The yes/no reader ignored the rest | Long "no" goes to understanding; `reply_to_pending` (§3.1) | partly fixed, not live | Declines the pending item **and** answers the request |
| F | General FD question answered "no FD in your name" | Scope taken from the whole call | Per-request scope with references (§3.1) | open | "Can I withdraw an FD before maturity?" after "I have an FD" → general answer |
| G | Variant mixing (Swadesh, "no minimum balance 6.75%") | Passages from several variants | Doc tags + variant check in code (§5.1, §5.2) | open | Generic savings minimum balance → clarify, naming the variants |
| G2 | 7.75% vs 7.90% | Stale "up to" figures in the docx vs the 23 Apr 2026 rate card | Keep 7.75% (§5.3) | open (step 1) | "Highest senior FD rate?" → 7.75%, 30–36 months |
| H | Lounge "no extra discount" | Answered a nearby question; unsupported negative | Style rule 7 (§4.1), restate the ask (§5.4) | open | No negative claim without a source saying so |
| I | "So, you know" / "Oh dear" → full greeting | Fixed greeting template; fragments treated as turns | §4.4, §6.2 | open | Fragments get a short reply or none; greeting only once per call |
| J | Explicit fraud-desk request asked "Is that okay?" | Every transfer asks consent | §6.3 | open | Explicit request transfers directly |
| K | Unrecognised ₹2,000 debit → plain complaint | No fraud workflow states | Fraud trigger (fixed, not live) + full workflow (§3.4) | partly fixed | Card block offered first; "no matching debit" handled honestly |
| L | Hears itself on speakers | Chrome doesn't echo-cancel Web Audio | WebRTC loopback playback (ADR 0012, formerly 0028, 12 Nov) | fixed in UI, not measured on hardware | `docs/echo-self-test.md` ≥ 15 dB |
| M | OTP stored as a turn | Typed code took the normal path | Verification-state routing (§9.2) | open, **fix first** | A typed code never shows up in turns, logs or LLM input |
| N | 429 → "I'm having trouble" for many turns | No fallback | Bounded retry + tested fallback + breaker (§7.3) | open | Forced 429 → answer from the fallback, or an honest callback offer |
| O | Answers too long | No length guidance | Chunked explanations, top-3 lists (§4.1) | open | No reply over ~80 words without a check-in |
| P | ASR "चाटना", garbage turns | No vocabulary hint; no unclear-speech rule | §6.4 (A/B tested) | open | Fewer errors on saved recordings, no new substitutions |
| UI | `/calls` 1,557 px wide on a phone; `/kb` table overflows | Flex rows don't wrap | §15 | open | No horizontal scroll at 390 / 768 / 1024 px |

### 20 Nov: word-count and character-gap limits removed (call a6545287)

Call a6545287 (Ravi): "मुझे मेरे last 5 transactions देखने हैं" → "which account?" → "मुझे दूसरे account के बारे में बताइए"
(7 words) missed the 6-word limit for "this answers which account?", and code also required the LLM's topic to match the
waiting question (the LLM said "account status"). The transactions were never given.

| What | Before | Now |
|---|---|---|
| Is a reply the answer to our question? (16 places: `conversation`, `confirm`, `journeys`, `understanding`, `dialogue`, `smalltalk`, `turntaking`) | Word count (≤ 2 … ≤ 25) | `services/says.py` `new_request()`: the answer unless it also names another data topic, a problem, fraud, a person or a product question. Any length, any language. |
| Account pick resumes the waiting question | ≤ 6 words and LLM topic = waiting topic | Names an account and no other topic (the LLM's topic is ignored) |
| Thanks / bye / greeting without the LLM | ≤ 5 / ≤ 8 / ≤ 4 words | Only polite words besides the thanks / bye / greeting |
| Two words "near" each other in a regex (53 gaps such as `card.{0,40}declined`, `(\w+ ){0,3}`) | Within N characters | Anywhere in the same sentence (`says.SAME_SENTENCE`) |

**Not tested yet** (owner: no tests this time). Not run: offline tests, smoke set. Some offline tests may still expect
the old behaviour (e.g. a long "yes ..." was "unclear"; now it is "yes" unless it asks something else). Trade-off: a
same-sentence match can catch two words that a 40-character gap kept apart (a false fraud or card-failing reading in a
long run-on sentence). **Needs a brain restart.** Still open from this call: the ATM complaint read as a balance question,
English answered in Hinglish, the "waiting" agenda item marked done; see the next-step notes.

### 22 Nov: Edge-era speech code removed, ONNX models deleted, Bedrock and opener research

| What | Done |
|---|---|
| English number-to-words (`numbers.speech_text`: "₹10,000" → "ten thousand rupees", "%" → "percent"; `indian_words`) | Removed. Amounts stay as digits for Sarvam (bulbul v3+ reads numbers itself). Kept: marker/markup stripping, reference numbers digit by digit, `Rs`/`INR` → ₹, `p.a.` → "per annum", `say()`. Edge (dev/fallback) now also gets digits. |
| `PhraseCache.clear()` | Removed (unused since the `/pronunciation` page went). |
| ONNX model copies `brain/models/bge-m3-onnx`, `bge-reranker-v2-m3-onnx` (≈4.9 GB) | Deleted on this laptop. `SEARCH_RUNTIME=onnx` code kept; re-download with `scripts/download_models.py` if needed. |
| Tests | `test_numbers.py`, `test_api_flow.py` updated to expect digits. **Not run** (owner: no tests on this laptop); syntax check only. |

Research (no code change): Bedrock docs say gpt-oss reasoning is "on by default (effort configurable: low, medium, high)" but don't name the
field; Converse takes extra model fields in `additionalModelRequestFields`. Our Bedrock adapter sends none (Groq sends
`reasoning_effort: low`). Also: the gpt-oss-120b model card lists **no geo (`in.`) inference profile**, only in-region
ap-south-1, while `adapters/bedrock.py` and ADR 0026 say to use an India geo profile. To check.
Contextual openers (options A/B/C): none shortens the time to the real answer; decision pending with the owner.

**Needs a brain restart** (speech text change).

## Open questions (for the owner or the PM)

- **Shared OTP (PM review, 17 Nov):** should the agent transfer to the fraud team straight away (no "Shall I?"), and skip
  sending a new code to the same mobile? Today it verifies, checks two days of payments, warns, then offers a transfer.
  Needs AU's approved fraud procedure.
- **"I'm interested in credit cards":** ask first whether they want help choosing, benefits or eligibility (one more
  turn), or keep going straight to "how much do you spend a month, and on what?"

- **Filler on almost every turn:** understanding takes ~1.5 s and the filler starts at 1.2 s. Raise `FILLER_AFTER_MS`
  to ~1600 (fewer fillers, a slightly longer silence on slow turns) or keep it?

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

## Session notes

- 5 Nov: Phase 4 plan written after reviewing all calls. The owner wants to run Phase 4 in a fresh chat; this file and `CLAUDE.md` exist so that works.
- Benchmark tooling: `brain/evals/benchmark.py` has a Groq 429 retry wrapper (`Patient`) and a token pacer. It still can't get past the daily request limit, which is why PHASE-4-PLAN §10 redesigns testing.


---

> **Archived on 25 Nov 2026:** the tracker as it was before the EKS / handoff rewrite (its 22–23 Nov entries and
> open questions), word for word.

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
