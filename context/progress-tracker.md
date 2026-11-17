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

### 16 Nov (5): timeline corrections (documents only)

- `docs/diagrams/diagrams.md` §7: the barge-in was drawn too early. The code ducks the agent after 0.35 s of caller
  speech and decides only after the caller pauses 0.3 s (or after 1.5 s of speech): now duck 7.35 s, stop ~8.0 s,
  next turn ~9.6 s, new reply ~11.3 s. §8: the always-on band was the detector alone (39%); with audio handling it is
  ~50% for 9 calls (sheet: ~57% with turn bursts). Collisions cost milliseconds, not 0.1 s (bars drawn wider).
  Excalidraw versions (sequence diagrams) given in chat; Excalidraw doesn't import Gantt.

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
| A | Balance request answered with a guessed pending UPI and a complaint offer | One intent per turn; the issue journey guessed the transaction | requests list (§3.1); never guess the transaction (§3.4) | open | "Money got deducted, check my balance" → balance first, then asks which debit; no complaint offered |
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

## Open questions (for the owner or the PM)

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
