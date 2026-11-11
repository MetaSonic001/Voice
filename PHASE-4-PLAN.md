# Phase 4 plan: a voice agent that behaves like a good first-line bank representative

**Status (6 Nov 2026): built, except §13 (deployment, reference only), §14 (vendor comparison, reference) and the items listed under "Deviations" in `context/progress-tracker.md`.** Section 0 refers to the removed Phase 3 plan; its journeys are all built.

Inputs: all 44 recorded calls (the review is in §1), your notes on call `408e24bf`, the architecture slide from the production vendor (Gnani-style stack), the articles you linked, and the two "queue / design" reviews you pasted.

---

## 0. Was Phase 3 finished?

**Yes. Every item in PHASE-3-PLAN §9 was built.** One thing is not finished:

| Phase 3 item | State |
|---|---|
| Answer decisions, "my minimum balance" from own account | ✅ built |
| 10 departments, routing in code, transfers with consent, hold, agent desk, live bridge, callbacks | ✅ built |
| Mock core bank (7 scenario customers), policy pack, card actions with read-back, complaints and disputes, fraud journey, leads | ✅ built |
| Pronunciation list | ✅ built (but you still don't like how "AU" sounds; see §8) |
| Tests | ✅ 100 offline tests pass |
| **Benchmark numbers on the real LLM** | ⚠️ **not finished.** Run 3 stalled on Groq's daily request limit (1,000 requests per day per model). I stopped it after 38 of 54 routing items: **32 right (84%)**. The 6 misses were "this account maintained?", a Hindi charge question, "will my FD renew", "was my card payment received", "annual fee on my card" and "upcoming debits". This is the strongest argument for the new eval design in §10. |

Your live call `408e24bf` ran on the brain you started at 14:41, **before** the fixes from that call were loaded. Three things are already fixed in code but not yet live: "No… show me five transactions", the `KeyError: 'section'`, and "paid (failed)". The others are not fixed and are covered below.

---

## 1. What the 44 calls actually show

I read every call. Grouped by root cause, not by symptom:

| # | What happened (call) | Root cause | Where fixed |
|---|---|---|---|
| A | "Money got deducted… check my account balance" → read out a ₹10,000 pending UPI and offered a complaint (`408e24bf`) | The understanding returns **one** intent per turn. It picked "issue", and the issue journey then **guessed** the transaction (the latest debit) instead of asking. | §3.1 requests list; §3.4 "never guess the transaction" |
| B | "Last five transactions" → 1 + 5 from two accounts (`408e24bf`) | `personal.answer` loops over accounts and applies the limit **per account**. | §4.2 merge, sort, cut to N |
| C | "₹10,000 paid (failed)" | The template put the direction word before the status. | Fixed in code; §4.2 adds one status table |
| D | `KeyError: 'section'` | Bank answers have no `section` in their citations. | Fixed in code |
| E | "No, I don't want that. Show me…" → only cancelled | The yes/no reader checked the reply only. | Fixed in code (a long "no" now goes through normal understanding); §3.1 makes it a proper "decline + new request" |
| F | "Which FD rate is correct, 7.75 or 7.90?" → "No FD in your name" (`408e24bf`); "Can I withdraw my FD before maturity?" → "No FD in your name" (`0425bde8`) | "I also have an FD" earlier in the call pulled the turn to **personal**. Nothing says "a general question about a product I hold is still general". | §3.1 `scope` field + rule; §5.3 conflicts |
| G | Generic savings minimum balance answered with Swadesh's figure; a generic savings question answered "no minimum balance, 6.75%" with **5 citations** (`dd99b638`); "Aarambh" account named (`0425bde8`) | The answer step sees passages from **different variants** and treats them as one product. Prompt rules alone don't hold. | §5.1 product/variant tags on documents; §5.2 variant check in code |
| H | "Do you offer discounts on lounge access?" → "16 visits, no extra discount" | It answered a nearby question and then asserted a negative the sources don't state. | §5.4 "answer the question asked; never assert an absence" |
| I | "So, you know," / "Oh dear." / "Hello, I am waiting." → full greeting again (3 calls) | `smalltalk` has one fixed template, and it's a greeting. Fragments are also treated as complete turns. | §4.4 acknowledgement replies; §6.2 fragment hold |
| J | "Please connect me to your fraud team" → "Is that okay?" | Every transfer asks for consent, even an explicit one. | §6.3 explicit request = no second question |
| K | "Debit of ₹2,000 I don't recognise" → "raise a complaint?" | Fixed in code: the fraud trigger now sends it to the fraud journey. Still missing: when no ₹2,000 debit exists, the agent must say so and ask (when, which account or card, the merchant). | §3.4 |
| L | **The agent hears itself on speakers** (`7f289533`, `dd99b638`, `7bd64f2e`): its greeting and "One moment, please" came back as the caller's words | The browser does not echo-cancel our playback. | Not fixing: browser-only; use earphones (§2) |
| M | **An OTP was stored as the caller's text** (`64e9d431`: the 6 digits were typed into the chat box) | Digits sent while a code is pending go through the normal path and are saved. | §9 must-fix |
| N | Groq rate limit (429) → "Sorry, I'm having trouble" on 5–8 turns in a row (`2b89a538`, `0425bde8`) | No fallback model, and the error looks like the agent being broken. | §7.3 fallback + honest wording |
| O | Answers too long: Zenith+ (≈90 words, 3 citations), Laksya (6 sentences), "what savings accounts do you offer" (16 names) | No spoken length budget, and no "give the top of it, offer more". | §4.1 conversation style |
| P | Wrong-reply noise: ASR "चाटना" for "जानना", garbage transcripts in `e9de0371` | STT has no banking vocabulary and garbage isn't rejected. | §6.4 |
| Q | Old calls (3–4 Nov) show 8–13 s turns and 60 s errors | The GPU idle slowdown and an LLM outage, both fixed in Phase 3. | n/a |

**The honest summary:** retrieval is not the main problem. Hit@5 on the golden set is 94%, and the right passage is usually found. That only shows a relevant passage was *found*. It doesn't show the right variant ranked first, that the passage was current, or that the answer was correct, which is why §10 adds variant accuracy and answer correctness. The failures are in three layers around it:

1. **Understanding:** one intent per turn; general vs personal decided by the whole call instead of the question.
2. **Data handling:** per-account limits, guessing which transaction the caller means.
3. **Answer scope and style:** variants mixed, too long, templated greetings.

That matches your reviewer's line: *"the biggest problem is orchestration, not just the LLM."*

---

## 2. The agent hearing itself on speakers: not fixing

**Decision (5 Nov):** dropped. It only happens in the browser demo on laptop speakers: Chrome doesn't echo-cancel Web Audio playback. Real phone lines cancel echo in the handset and network. For demos, use earphones.

---

## 3. Conversation brain: understanding, state, tools

### 3.1 Understanding v2 (one LLM call, as today, with a richer output)

Keep one fast understanding call (gpt-oss-20b, strict JSON). The reviewer's points I agree with:

```
{
  "language": "hi",
  "requests": [                                  // 1..3, in the caller's order
    {"type": "enquiry", "domain": "accounts", "task": "balance",      "scope": "personal", "entities": {...}},
    {"type": "enquiry", "domain": "accounts", "task": "transactions", "scope": "personal", "entities": {"count": 5, "direction": "debit"}}
  ],
  "risk": ["unrecognised_debit"],                // separate from the request type
  "reply_to_pending": "decline",                 // accept / decline / question / none
  "needs_clarification": null,                   // or {"slot": "account", "why": "..."}
  "search": "..."                                // rewritten query for RAG (only if some request needs the KB)
}
```

**What changes and why:**

| Change | Fixes |
|---|---|
| `requests[]` instead of one `intent` | A, E ("decline + new request"), "balance and last 5" in one breath |
| `scope: general / personal` **per request**, decided from the current sentence **plus references to earlier turns**. "What's **its** minimum balance?" after "I have a savings account" is personal. "What are the premature withdrawal rules for FDs?" after "I have an FD" is general. A product the caller merely owns never makes later questions personal by itself. | F |
| `risk` flags separate from request type ("I lost my card, block it" = action + `lost_card`) | the reviewer's point that fraud and action aren't exclusive |
| `manipulation` leaves the intent list; prompt-injection detection becomes a guard (§9) | cleaner taxonomy |
| `task` from a **registry in code** (domain → tasks → handler → tool calls). Each task also declares its **access level**: open (product info) / verified (balances, transactions) / verified + confirm (card actions) / people only (address or mobile change, refunds, limits). Code enforces it; being verified doesn't unlock everything. The LLM only picks names; adding a task is a registry entry plus a prompt line, not a classifier rewrite. | missing tasks (complaint status, replacement status, etc.) can be added one by one |

**Latency guard:**
- the schema stays nullable, so short turns emit `null`;
- one understanding call returns at most 3 requests. **No request is ever dropped:**
  - anything beyond 3 is kept in `open_requests` and handled next;
  - each request is tracked on its own (done, declined, pending, failed), so cancelling one never cancels another;
  - after an interruption or a failure, the remaining requests carry on;
- target: understanding p50 ≤ today's (≈900 ms) on the routing set. If the richer schema costs more than +100 ms at p50, cut fields back.

**Safety guard (deterministic, before and after the LLM):**
- The fraud regex stays, but as a **risk** detector, not an intent override. Risk = regex OR the LLM's `risk` flags (either is enough), so phrasings the regex misses still count: "someone took money from my account", "I never made that payment", "my card is missing". Each of these becomes a test.
- "How do I block a card if I lose it?" is a general enquiry. "I lost my card" is an action with the `lost_card` flag. The LLM tells these apart. The code makes sure a credible `lost_card` / `unrecognised_debit` / `otp_shared` is **never** answered as a plain enquiry.

### 3.2 Conversation state (one structured object per call, not just chat history)

There is partly a state already (`sessions.pending`, facts, verified). Make it one explicit record per call:

```
state = {
  verified: true, customer: CIF…, language: "hi",
  focus: {account: "4521", card: null, fd: null, txn: "UPI…7781"},   // "this account", "that transaction", "the other one"
  open_requests: [...],          // asked but not yet answered (e.g. 2nd request while the 1st needed a clarification)
  pending: {...},                // the yes/no question we asked (exists today)
  done: ["balance"], declined: ["complaint:upi"],
  auth: {method: "otp_keypad", verified_at, expires_at},            // what each task needs comes from the registry
  actions: [{op: block_card, card: 6712, status: awaiting_confirmation | submitted | succeeded | failed | unknown, ref}],
  last_list: [txn ids we read out]                               // "the second one", "the ₹1,980 one"
}
```

- **Short-term memory:** `focus` and `last_list` resolve references such as "this account", "the second one", "the other card".
- **Unfinished work survives interruptions:** if the caller barges in during a 5-item list, `last_list` keeps the items not yet read. "Go on" continues; a new question doesn't lose them.
- **Long-term memory:** at verification, load the customer's **open tickets and handoffs from the last 30 days**. The 30 days is a design choice to confirm with AU.
  - The agent **asks**; it never assumes: "I can see an open request about a UPI payment. Is today's call about that?"
  - It only names the category, never other details, until the caller says yes.
  - One query; no new store.
- **`unknown` is a real action status.** A timeout doesn't mean a write failed. The agent re-checks the status before saying anything, and never says "failed" when the outcome is unknown.
- **The caller hangs up mid-operation:**
  - reads are cancelled;
  - a write already sent is allowed to finish and its outcome is recorded;
  - if the outcome is unknown, a follow-up ticket is created;
  - late responses are logged against the call.
- **No session restore after a dropped call.** The next call verifies again.

Stored in the `sessions` row (jsonb) as today. Redis only at scale (§12).

### 3.3 Tools: realistic, separate mock bank APIs

**Yes. In reality these are different systems with different APIs, and the mock should behave like that.** Today `MockCoreBank` is one Python class over one `corebank` schema, called in-process. That hides exactly the things that break in production: timeouts, partial failures, different IDs, different status vocabularies, write idempotency.

**Proposal: one `mockbank` service (separate process, own port 8100), with one router and one Postgres schema per bank system.** Realistic, but not 8 microservices to run on a laptop.

| System (real-world equivalent) | Mock API (examples) | Schema |
|---|---|---|
| **Customer / CIF** | `GET /cif/v1/customers?mobile=`, `GET /cif/v1/customers/{cif}`, `GET …/relationships` (which accounts, cards, loans) | `cif` |
| **Core banking (CBS)** | `GET /cbs/v1/accounts/{id}` (balance, available, holds, AMB), `GET /cbs/v1/accounts/{id}/transactions?from&to&limit`, `GET …/charges` | `cbs` |
| **Payments switch (UPI / IMPS / NEFT)** | `GET /payments/v1/txns/{utr}`, `GET …?account&status=pending` | `payments` |
| **Debit card management** | `GET /cards/v1/debit/{id}`, `POST …/block`, `POST …/controls` (with `Idempotency-Key`) | `cards` |
| **Credit card platform** | `GET /cc/v1/cards/{id}` (dues, limit, statement), `GET …/transactions`, `POST …/block`, `POST …/international` | `cc` |
| **Loans (LMS)** | `GET /lms/v1/loans/{id}`, `GET …/emis`, `GET …/foreclosure-quote` | `lms` |
| **Deposits** | `GET /deposits/v1/fds/{id}`, `GET …/premature-quote` | `deposits` |
| **CRM / complaints** | `POST /crm/v1/complaints`, `POST …/disputes` (separate lifecycle), `GET …?cif=&open=true` | `crm` |
| **Contact centre** | handoff queue: the existing `/v1/desk/*`, kept where it is | (brain) |

**What makes it realistic:**
- each system has **its own IDs and status words**, so the brain's adapters must map them to one internal set (the canonical status table in §4.2 lives here);
- **auth between brain and mock bank:** a service token, plus a **customer-scoped token** issued only after OTP.
  - The token is short-lived (call length), signed (stdlib `hmac`), lists the CIF and the allowed operations, and names which service it is for.
  - Each mock API checks it independently and rejects any other CIF.
  - The customer identity comes from the verified session, **never** from the LLM. The token is never shown to the LLM, the transcript or the desk.
- **Separate schemas are for realism, not security.** They share one Postgres. Real isolation is AU's systems' job.
- **Writes need an `Idempotency-Key`:**
  - a retry with the same key and the same request returns the **original** result (even if the first response was lost);
  - the same key with a different request is rejected (409);
  - keys expire after 24 h.
- **Fault injection** per system, from a header or env var:
  - added latency, timeouts and 5xx errors;
  - malformed JSON and unexpected status values;
  - "succeeded but the response was lost";
  - partial failures across systems: the card block succeeds but complaint creation fails; one account answers and another times out.

  The agent must say exactly what succeeded and what couldn't be confirmed, and never claim success or failure it didn't verify.
- **OpenAPI** for each router, generated by FastAPI. These are **our proposed contracts**, useful to start a conversation with AU's integration team. They are not AU's specs. The real adapters are written against AU's documentation when we get it;
- **contract tests (T0):** request, response and error schemas, auth, idempotency and timeout behaviour of each mock API, and the registry and workflows run against them;
- **every bank call is an audit event:** call ID, operation, the authorisation decision, the result status and timing. No OTP, CVV or full numbers;
- the brain keeps its **ports** (`Bank`, `ServiceDesk`). The adapter changes from in-process `MockCoreBank` to an `HttpBank` that calls the mock APIs **in parallel** (one balance + transactions turn = 2 concurrent calls, ~20 ms locally). Swapping to AU's real APIs means writing a new adapter, not touching the conversation code.

**Seed data:** the same 7 scenario customers plus the workbook customers, split across the schemas. The workbook stays untouched. Transaction dates are made **relative to today**. "31 Dec 2026" showing up on 5 Nov was your item P0-8: today minus N days, so the demo never shows the future.

**What "tools" means here (and what it doesn't).** We do **not** switch to LLM function-calling, where the model decides which API to call in a loop. Each tool round-trip costs another LLM call (+0.6–1 s per hop), and the model can pick the wrong tool or argument. Instead:

> **understanding (LLM) → task registry (code) → tool calls (code, in parallel) → validation (code) → response (LLM phrasing or template)**

This is the "intent router → API gateway" box in the production stack and in your notes. The LLM recommends; code decides and executes.

### 3.4 Workflows (multi-step), in the same registry

The registry has **handler kinds**: `kb`, `bank_read`, `action`, `workflow`, `ticket`, `handoff`, `lead`, `verify`. That's the reviewer's list; it adds `workflow` and `verify`. A workflow is a short list of steps with conditions, each step being a tool call or a question. Built-ins:

- **Unrecognised debit.** Protection comes first; it isn't held back until the transaction is found.
  1. If it's a card debit, **offer the block straight away**.
  2. Find candidate transactions (amount ± date ± account, card or UPI). The result is one of:

     | Found | What the agent does |
     |---|---|
     | Nothing matches | Says so; asks for when, which account and the merchant |
     | More than one | Lists at most 3 |
     | Still pending | Explains the status |
     | Successful | Reads it back: "₹2,000 at X on 3 Nov, is that the one?" |
     | Already reversed | Says the money is back |
     | Bank system down | Says it can't check right now; still offers the block and the fraud desk |

  3. Once the caller confirms the transaction:
     - card → a dispute and the fraud desk;
     - UPI → a fraud complaint, 1930 and the fraud desk.

     Card and UPI steps follow the policy pack. They differ, so they aren't one generic flow.

  **It never assumes the transaction** (fixes A and K).
- **Lost or stolen card:**
  - An explicit "I lost my card, block it" **is** the request. If only one card matches, it is blocked straight away and read back ("Done, your debit card ending 5521 is permanently blocked"). If it's ambiguous, ask which card.
  - Card digits heard by speech recognition are read back first, because a misheard digit could block the wrong card.
  - "Lost" or "stolen" means a permanent block. "Misplaced" means offer a temporary block.
  - Then: roughly when it was lost → card transactions since then → dispute any the caller doesn't recognise → offer a replacement (a service request).
- **Existing Phase 3 journeys move into the registry unchanged:** ATM cash not dispensed, UPI pending or debited-but-not-credited, EMI bounce, card payment pending, FD premature quote, foreclosure quote.
- **New registry tasks:** complaint status (tickets already exist) and credit card lost or stolen.
- **Block + "has anyone used it today"**, run together (reviewer's example).

### 3.5 Going live on AU's real APIs: what changes

The router has three layers. Only the last one gets replaced:

1. **Understanding (LLM):** what the customer wants. Unchanged; it only changes when a new task is added.
2. **Task registry (code):** the rules for each task, which operations to call, or which human queue it goes to. Small edits only, if AU's APIs are shaped differently (one card API instead of two, a reason code needed for blocks).
3. **Adapters (code):** written new, one per AU system, turned on in config. They map AU's IDs, fields, status codes and errors to our internal format.

The design keeps changes to the conversation layer **as small as possible**, but it can't promise zero. Real APIs may differ in:
- auth requirements and status meanings;
- mandatory fields and limits;
- asynchronous processing;
- complaint and dispute lifecycles;
- failure modes and extra regulatory checks.

So workflows, status mappings, state and tests must be **re-validated** against AU's real APIs and procedures before go-live.

The human queue is chosen separately, by a rule in code (task + risk + segment → queue). Update that table when AU confirms its real teams and hours.

**To keep the switch cheap:** if AU shares its API specs (PM question 2 in §19) before step 4, build the mock APIs to match them.

---

## 4. Talking like a person (the "human feel")

### 4.1 Conversation style rules (one place, used by every reply)

Built from your notes, the production guides (Bearworks: *"one or two sentences per turn… one question at a time"*) and our calls:

1. **Answer first, then at most one question. Length fits the question, not a fixed cap.**
   - A fact ("what's the fee?") gets one or two sentences.
   - An explanation (how premature FD withdrawal works, what happens after a block, a dispute process) may take longer. It is given in **chunks of about 2–3 sentences**, each ending with a natural check-in ("Shall I go on?" / "Does that help?"). The caller can also just interrupt.
   - **Code never cuts an answer.** Cutting could drop a condition like "plus GST" or "only for deposits under ₹3 crore", which would make the answer wrong.
   - The code check is only a safety net. If a reply runs past about 80 words with no check-in, it is logged and flagged for review, and the prompt is tuned. It is not truncated.
2. **Give the top of it and offer the rest:**
   - "We have 16 savings accounts. The popular ones are Value, Digital and Royale. Is there something specific you're looking for, like zero balance or higher interest?"
   - lists of more than 3 items are never read out in full.
3. **Acknowledge the concern before acting, once, and specifically:** "I understand, a debit you don't recognise is worrying. Let me check." Not "I sincerely apologise" on every turn.
4. **Ask instead of dumping.** When a question depends on a variant, tenure or category, ask the one missing thing ("Which account do you have, or would you like the zero-balance options?") rather than reading the range table. The range is said only if the caller asks "roughly?".
5. **No markdown, no citations [1], no "as per the document"** in speech.
6. **Same language as the caller's last turn**; Hinglish stays Hinglish (§6.5).
7. **Never make an unsupported negative claim.** If a current, authoritative source clearly says a benefit isn't available, the agent can say so. Otherwise: "I don't see a separate discount in what I have; the card includes 16 free international visits." Free visits and discounts are different benefits; answer the one asked about.
8. **Accessibility basics:**
   - "repeat that" / "phir se bolo" repeats the last reply;
   - "slower please" lowers the speaking rate for the rest of the call;
   - long silences get one gentle prompt before anything else.

### 4.2 Bank-data answers: verified facts, then natural phrasing

Today personal answers are **templates**. They are accurate but robotic, and they read every field. Plan:

- **Tools return facts; a central validation step checks them before any reply is phrased.** It checks:
  - the data belongs to the verified customer, and to the account or card asked about;
  - amounts and dates are valid;
  - statuses are mapped;
  - any "done" claim matches a `succeeded` action status.

  If validation fails, the reply is a safe fallback ("I couldn't confirm that"), not a guess.
  - **Account scope:**
    - if the caller names an account ("my salary account"), filter to it;
    - if they don't, merge **across accounts**, sort newest first, cut to **exactly N**, and say so: "your latest five across both accounts". Every item carries its account ending.
  - Map statuses with the **one status table**:

    | Status | Spoken |
    |---|---|
    | success | completed |
    | pending | still processing |
    | failed | failed, no money moved |
    | reversed | reversed, money returned |
    | returned | returned |
    | unknown | "I can't confirm its status" |

  - "Paid" is only ever said for success.
- **Phrasing, by complexity:**
  - **Simple single facts** (balance, due date, EMI date): improved templates, with variety and fewer fields. **No extra LLM call; latency unchanged.**
  - **Lists and explanations** (last 5 transactions, why a charge, EMI bounce): a short LLM phrasing call (20b, streamed). It gets **only the validated facts JSON** plus the style rules. A **figure check in code** (as we already do for KB answers) rejects any number, date or name not in the facts and falls back to the template. Cost: about +300–500 ms to first audio on these turns only. Worth it, because these are exactly the turns that sounded robotic.
- **Progressive disclosure for lists:** "Your last five: ₹1,250 at Barbeque Nation yesterday, ₹1,980 at Reliance Smart yesterday, and ₹10,000 by UPI to Suresh Patel this morning, which is still processing. Want the other two?" The `last_list` state lets "yes" continue.

### 4.3 Proactive, but only when it helps

The agent offers the **one** next step a good representative would, not a menu:

- low balance close to a due EMI → mention it once;
- pending UPI older than T+1 → offer the complaint;
- FD maturing within 30 days → mention renewal;
- an open ticket from a previous call → ask if this is about that (§3.2).

These are rules in code reading the same facts; no extra LLM call.

### 4.4 Small talk and fragments (bug I)

- Replace the single greeting template with context-aware short replies:
  - fragment or filler ("so, you know", "hmm") → **say nothing and keep listening** (§6.2);
  - "Oh dear" / "ok" → "Is there anything else I can check for you?";
  - "Hello? I'm waiting" → "Yes, I'm here. Go ahead.";
  - thanks → "You're welcome!".
- Greeting is said **once per call**, never again.

---

## 5. RAG accuracy: right product, right variant, right date

Retrieval finds the passage (hit@5 94%). The answers go wrong when passages from **several variants** or **two rate cards** are mixed. Plan, lightweight version of what your research suggests:

### 5.1 Document-level metadata (not per-figure records)

At ingest, tag each **document** (not each chunk) with:

```
{ category, product, variant, doc_type: rate_card | schedule_of_charges | terms | faq | brochure | policy,
  effective_from, effective_to, superseded_by, authority: 1..5 }
```

- Extracted once per document by a small LLM call plus title rules. Shown on the KB page for a human to check or correct (a document is ~1 minute to review).
- Chunks inherit the tags.
- Costs nothing per question.
- This is the piece I skipped in Phase 3 as over-engineering. The call evidence (G: 3 separate failures) now justifies the **document-level** version. Per-figure policy records are still not needed.

### 5.2 Use it at answer time (code, not prompt)

- **Variant check:** if the question names no variant but the top passages come from more than one variant of that product, or from exactly one variant while the question was generic, it is **clarify**, decided in code before the answer LLM runs. This makes the Phase 3 prompt rule deterministic.
- **Authority and date:** rate card > schedule of charges > terms > FAQ > brochure. Expired or superseded documents are dropped unless the caller asks about the past.
- **Product filter:** when the understanding names a product or variant, retrieval is restricted or boosted to it, with fewer distractors.

### 5.3 Conflicts (the 7.75% vs 7.90% case)

**Checked (5 Nov).** It is a **real conflict**, not a tenure difference:

| Source | Says |
|---|---|
| Rate card PDF "rates-at-a-glance", **effective 23 Apr 2026** | Senior citizens: highest rate **7.75%** (30 months 1 day to 36 months, under ₹3 crore, callable). General: highest **7.35%**. |
| `CASA+TD KB.docx` (undated product overview) | "up to **7.90%**" for senior citizens; "up to **7.40%**" for general customers. The 7.90% appears in 3 places. |

**Decision (for now): keep 7.75% only.** In step 1, the docx's "up to" figures are corrected to match the dated rate card (7.75% senior, 7.35% general) and uploaded as a **new KB version**. The KB keeps versions, so the original stays on record. The bank's source file in `Sample Questions/` is not edited.

No conflict-resolution engine is built around this example. What remains is light and runs in code:
1. Before calling anything a conflict, compare what each figure applies to: product, variant, tenure, amount band, customer category, callable or not. 7.25% (regular) vs 7.75% (senior) is not a conflict.
2. Drop expired documents.
3. Prefer the more authoritative or newer document **for that topic**.
4. **Code** picks the source and passes it, with where it came from, to the answer step. The LLM never chooses between conflicting figures.
5. If it's still unresolved, CONFLICT: say so and offer the specialist (as today).

### 5.4 Answer the question that was asked

The answer prompt gets an explicit first step: restate the exact ask in 5 words ("discount on lounge access"). If the sources don't answer **that**, it's partial or no-answer, not a nearby fact.

### 5.5 Mixed data + policy questions

"Why was ₹500 deducted?" → transaction API (narration says "AMB charge") → KB policy on AMB charges → one explanation from both. This is the `charges` task as a workflow (§3.4). It already exists for minimum balance; it extends to EMI bounce, card fees and ATM charges.

---

## 6. Voice: turn-taking, interruptions, recognition

### 6.1 Barge-in, what you asked to verify

| Question | Today | Plan |
|---|---|---|
| Stops immediately when interrupted? | Yes for real speech (Phase 2, A1–A4) | Measure: barge-in stop latency p50/p95 in the call record |
| Keeps unfinished context? | Partly: the interrupted text is logged, not resumable | `last_list` and `open_requests` (§3.2): "go on" continues |
| Knows correction vs addition vs topic change vs acknowledgement? | backchannel / interrupt only | Add "continue" ("go on", "haan aage") and "repeat" ("what?", "phir se") as cheap rules; corrections go through understanding with state |

### 6.2 Don't answer half-sentences

"So, you know," was answered as a full turn. The endpointer already has `incomplete()`. Strengthen it: if the transcript is only fillers or ends in a connector ("and", "so", "aur", "ki"), wait up to 1.2 s more for continuation before replying. No LLM call.

### 6.3 Fewer, better questions

- **Explicit transfer request** ("connect me to fraud") → transfer directly, no "Is that okay?". Consent is asked only when **we** propose it.
- **Explicit full action** ("permanently block my card ending 6712"): still **one** confirmation (a regulatory-grade action), but read back in one sentence.
- Never re-ask what the caller already said (state §3.2).

### 6.4 Recognition (Hindi "चाटना", garbage turns)

- **STT hint prompt** with banking vocabulary: AU, Zenith, Laksya, Ananta, Swadesh, FD, EMI, UPI, NEFT, IMPS, KYC, the 16 account names, and common Hindi banking verbs. Whisper's `initial_prompt` is free and already supported by our STT.
  - A hint can also **bias** recognition toward those words. It is kept only if an A/B test on our saved recordings shows fewer errors **without** new wrong substitutions.
- **Unclear-speech handling** (not a blanket "reject unusual text"; accents, soft voices and unfamiliar names are normal):
  - "Sorry, I didn't catch that" only when **two** signals agree: low STT confidence plus repetition or nonsense in context (`e9de0371`);
  - after 2 in a row → offer the keypad or a person.
- **High-impact values are read back before anything consequential:** amounts, account and card digits, UTR or reference numbers, and payees. If speech recognition hears ₹15,000 for ₹5,000, nothing happens until the caller confirms.
- **Production:** Sarvam or an Indic STT with a custom vocabulary (later, as decided).

### 6.5 Language consistency

Reply in the language of the caller's **current** turn. Bank-data templates exist in en/hi/Hinglish; the phrasing LLM gets the same instruction. One test per journey in each language.

---

## 7. Latency and reliability

### 7.1 Measure the right thing

Today `total_ms` is measured in the brain. Add **end of caller speech → first agent audio** (what the caller hears) to every voice turn.

Report:
- p50 / p95 / p99;
- each stage separately (STT, understanding, retrieval, bank calls, LLM first token, TTS);
- the rate-limit (429) count;
- the fallback success rate.

Targets from the guides: perceived **< 800 ms**, visible degradation **> 1.2 s**.

The old slowdowns (GPU idle, LLM outage) stay in the regression checks; they aren't treated as solved forever.

**Small concurrency check:** 20 simultaneous text sessions with the fake LLM, to catch our own locking and GPU-queue problems. The real LLM can't be load-tested on the free tier.

### 7.2 Where the time goes and what to cut

| Step | Now (approx.) | Plan |
|---|---|---|
| Endpointing silence | 500–700 ms | Keep; it's what avoids cutting people off |
| STT | ~1.1 s on long utterances (`7bd64f2e`) | Already streamed; check the GPU path |
| Understanding | ~0.9 s | Unchanged (v2 must not be slower) |
| Retrieval | 85 ms (warm) | Unchanged |
| Answer first token | ~0.6–1 s (120b) | Unchanged |
| Bank tools | ~5 ms in-process | ~20–60 ms over HTTP **in parallel** (§3.3) |
| Filler "one moment" | on every slow turn | Only when the expected time is > 1.2 s (Sharvin Shah: filler on every turn sounds like stalling) |

**Prefetch** is the one new speed-up. While understanding runs, start the account lookups the turn will probably need (for a verified caller who mentions balance or transactions, prefetch the accounts summary). The GPU warm-up already works this way.

### 7.3 Failures: honest and graceful (bug N)

- **LLM 429 / timeout on the answer model:**
  - one bounded retry with backoff;
  - then the fallback model, **only if that model passed the same smoke set** (§10) for that role;
  - a simple breaker: 3 failures in 60 s means go straight to the fallback for the next 60 s. This is a counter in code, not a library.
- **Understanding fails:** fall back to a keyword router for the top 10 tasks, so "balance" and "block card" still work.
- **Still failing:** "I'm having trouble with my systems right now. I can take your number for a callback or connect you to a colleague." Then raise the system-health alert (exists).
- **Bank API timeout:** never claim an outcome. For a write, re-query the status before saying anything. If it's still unknown: "I couldn't confirm the block went through. I'm connecting you to the card team to make sure."

---

## 8. Pronunciation of "AU"

Today: English (Neerja) says "AU" from text; the Hindi voice gets "ए.यू.". You find both unnatural.

Plan:

- **One listening test, 2 minutes of your time.**
  - I generate the greeting with 5 candidate spellings for each voice:
    - English: "A.U.", "A U", "Ay You", "A-U";
    - Hindi: "ए.यू.", "ए यू", "एयू", "ए-यू", "A.U." inside Hindi.
  - These go in a small page with play buttons.
  - You pick one per voice and the lexicon stores it.
- Also add "AU 0101" (the app name) → "AU zero one zero one", and the rest of the product names in the list.
- **Production TTS** (Azure / Sarvam / ElevenLabs) supports custom lexicons or phonemes, so the same lexicon file carries over. This mirrors the vendor slide's "lexicon and pronunciation" box in the TTS layer.

---

## 9. Guardrails, PII, security

### 9.1 What exists vs the three components you named

| Component | Today | Plan |
|---|---|---|
| **Intent router and guardrails** | One understanding call, then routing in code. Prompt-injection detection is an LLM intent (`manipulation`). Auth-gated data in code. | Guard step **before** the LLM: a regex/keyword screen for injection ("ignore previous", "system prompt") and for PII. Allow-list routing (registry). **Retrieved passages** are wrapped as data and never as instructions; the KB ingest flags documents containing instruction-like text. |
| **State machine and memory** | `sessions.pending` + facts + history | §3.2: one state object; long-term = open tickets and handoffs from the last 30 days |
| **PII redaction engine** | Amounts masked in history for some turns; OTP never via STT/LLM (keypad); masked caller number | One `redact()` that knows the difference between **sensitive identifiers** (masked), **financial facts** (kept: "₹10,000 was debited" stays useful) and **secrets** (never stored at all). Details below. |

**`redact()`: what it catches and how:**

| Identifier | How it's detected |
|---|---|
| Card number | 13–19 digits passing the Luhn check |
| Aadhaar | 12 digits passing the Verhoeff checksum |
| PAN | Format check |
| Phone number | Indian format, after normalising |
| Email | Format check |
| Account number | No checksum exists, so by context ("account number …") **plus** matching the customer's known account numbers from bank data |
| OTP or PIN | Not pattern-matched at all; handled by the verification state (§9.2) |

It doesn't mask every run of digits, so amounts, dates and "ending 4521" survive.

**Where it applies (every place text leaves the core):**
- what's sent to the LLM;
- stored turns and history;
- application and exception logs;
- HTTP access logs (no request bodies);
- tool arguments and results in traces;
- the LLM replay cache and eval data;
- handoff payloads and the desk;
- the judge.

Regex and checksums are **defence-in-depth, not a guarantee**; free speech can carry PII in forms they miss. Raw audio isn't covered by text redaction: today speech-to-text is Groq's hosted Whisper, so caller audio does leave the machine. Production STT (Sarvam or another vendor, or self-hosted) needs its own data-handling decision.

The **audit record** is separate from operational logs. It keeps the structured evidence of each banking action (operation, masked IDs, status, time) and nothing more.

### 9.2 Must-fix security items found in this review

1. **OTP typed in the chat box was stored as a turn** (`64e9d431`). This is enforced in code, not by prompt:
   - Input goes to verification **only** when a code step is active **and** the input matches the expected format (exactly 6 digits, nothing else). "My balance is ₹5,000" during a pending code is a normal turn.
   - The code never reaches the LLM, history, transcripts, logs or analytics. It is stored as `[code]`, and never read back.
   - The console's code entry becomes a masked keypad field, separate from the chat box.
   - Expiry, attempt limits and resend limits already exist (MVP 2) and are kept and tested.
2. Masking is not applied to the question text of every turn. Apply `redact()` to every stored question and to everything sent to Groq. A card number said aloud must never reach Groq or the logs.
3. **Write actions get idempotency keys** (§3.3).
4. **Customer-scoped token on bank APIs** (§3.3): authorisation outside the LLM.

### 9.3 Not doing (and why)

- **A separate ML PII model** (Presidio and similar). For Indian banking identifiers, regex plus checksums is more precise and needs no new dependency, matching the "minimal trusted libraries" rule.
- **Voice biometrics.** Auth stays as you decided.

---

## 10. Testing without getting rate-limited

**The problem:** Groq's free tier allows 1,000 requests per day **per model** and 8k tokens per minute. One full benchmark (routing 54 + decisions 20×2 + journeys) uses ~200–300 requests. Your own testing uses the same budget, so the benchmark stalls.

**New design: four tiers, most of it free.**

| Tier | What | LLM calls | When |
|---|---|---|---|
| **T0 unit** | The 100 offline tests with the fake LLM; journeys, tools, state, redaction, status mapping, merging lists, variant check | 0 | Every change |
| **T1 replay** | Every real LLM response in an eval is **cached on disk**, keyed by a hash of the **exact request**: model, all messages (system prompt, history, state, retrieved passages), output schema and decoding settings. Any change to any of them is a cache miss, so a stale answer is never reused. Re-running an unchanged item costs 0. The cache holds redacted text only and can be cleared. | Only what changed | Every prompt change |
| **T2 smoke** | **40 items**, chosen to cover each failure class in §1 (en/hi/Hinglish) | ≤ 80 | Before you demo or after a prompt change |
| **T3 full** | Golden set (105) + routing (~150) + multi-turn scripts (15) | ~400, paced across the day | Manually, at most once a day |

Plus:

- **Spread across models.** Groq limits are per model. The judge and evaluation use a different model from the agent (e.g. Llama 3.3 70B on Groq for judging), so testing never eats the agent's own quota.
- **Most checks are deterministic, not LLM-judged.** Outcome, task, scope, number of transactions, status words, figures present in the source and language are checked in code. The LLM judge is only for "was it natural / concise / did it answer the question".
- **Multi-turn scripts check state, not prose.** "After turn 3, `declined` contains complaint and the reply lists exactly 5 transactions, newest first."
- **The reviewer's 1,100-utterance suite:** too big for our quota and for a hackathon. Instead, ~150 routing items weighted to the risky classes (fraud, multi-intent, general vs personal, corrections), reported with **per-class** recall, especially **fraud recall** and **false-fraud rate**. Queue assignment is tested in T0 because it's deterministic code, as the reviewer says.
- **Reported per class, not just overall:**
  - fraud recall and false-fraud rate;
  - multi-intent recall;
  - general vs personal accuracy;
  - correction handling;
  - **false action execution (must be 0)**;
  - missed verification (must be 0).
- **RAG report adds:**
  - variant accuracy (right product variant used);
  - groundedness (figure check pass rate);
  - answer correctness (golden facts).

  Hit@k and MRR stay, but they only say a relevant passage was *found*, not that the answer was right. Precision@5 is skipped: it needs every retrieved chunk labelled, which is a lot of manual work for little decision value.
- **Every failure in §1 becomes a regression test** (T0 where code decides, T2 where the LLM does).
- **Request counts are estimates.** Retries and multi-turn scripts change them. Groq limits are per model, but check for org-level limits too.
- **Paid Groq tier: not now.** Caching and code checks first; decide only if limits still block testing.

---

## 11. After calls: automatic quality review (code checks always, LLM judge selectively)

**Which calls get the LLM judge** (researched; the common production pattern is "machine checks on everything, deeper review on a risk-weighted slice"):

| Calls | Judge? | Why |
|---|---|---|
| Any call that failed a code check (below) | Always | Something already looks wrong |
| Fraud / risk flag, any card action or other write, any complaint, dispute, handoff or callback | Always | Highest harm if wrong; also the calls auditors ask about |
| The caller repeated themselves, asked for a human, got "I don't know" twice, or hung up mid-task | Always | The best signs of a failed conversation |
| p95 latency over 2 s, or a 429 / fallback used | Always | Experience was degraded |
| Everything else (clean info calls) | **Random 10%**, spread across languages | Catches problems the triggers miss (drift) |
| **During demo and testing now** | **Every call** | Volume is tiny, and you're labelling calls |

**One setting controls this:** `JUDGE_MODE=all` (the default now, in development) or `selective` (the rules in the table, for production). The judge uses a different Groq model from the agent, so judging every dev call doesn't use the agent's quota.

**People review:** every `critical_failure`, everything the judge flags `needs_human_review`, and a small random sample of judged calls to keep checking the judge.

**Is the judge over-engineering?** No, as long as it stays this size. Most of the value comes from the free code checks. The judge adds what code can't see: did it answer the actual question, was it too long, did it ask the right thing. What would be over-engineering: a big analytics platform, multi-judge voting, judging every production call before the agent is reliable. **We're not building any of that.**


From the research (Hamming, the 2026 judge-reliability preprint, Cekura): score **separate dimensions**, not one number; require **evidence quotes**; flag critical failures; calibrate against a few human-labelled calls.

**How it works:**

1. When a call ends, a background job (the existing worker) builds a **call packet**:
   - the redacted transcript;
   - each turn's outcome, sources and bank facts used;
   - actions and their API results;
   - handoff details;
   - latencies.
2. **Deterministic checks first (free):**
   - disclosure said;
   - auth before data;
   - no unredacted PII;
   - errors;
   - p95 first-audio;
   - repeated questions;
   - the same question asked twice by the caller (the agent missed it).
3. **One judge call** (a different model from the agent, §10) with an anchored 1–5 rubric:

   | Dimension | Question |
   |---|---|
   | Task completion | Did the caller get what they asked, for each request? |
   | Correctness | Is every fact backed by the sources or bank data shown? |
   | Conversation | Right questions, not too long, kept context, right language |
   | Compliance and safety | No promises, no disclosure before auth, fraud handled per policy |
   | Handoff (if any) | Right team, right moment, context passed |

   Output: `dimension_scores`, `evidence`, `critical_failure`, `needs_human_review`, `failure_categories`, `recommended_fix`.
   - **Evidence must point at real turn IDs, source IDs or API results.** Code checks that every quoted line actually exists in the transcript and drops invented evidence.
   - Facts the code can check (right count, right account, status matches the bank) are **not** left to the judge.
4. Stored in a `call_reviews` table. Shown on `/calls`:
   - a score chip per call;
   - the review panel on the call page;
   - "needs review" calls at the top;
   - a human can override a score (that's the calibration data).
5. **Calibration:** you label your first 15–20 new calls yourself (thumbs and a note per dimension). That's a sanity check, not proof. The labelled set keeps growing, and we track:
   - agreement per dimension (within one point on 85%+);
   - **critical-failure recall** (the judge must catch every unauthorised disclosure or false "done");
   - false positives.

   If agreement is low, adjust the rubric.
6. **UI:** a review panel on each call and a "needs review" filter on `/calls`. No analytics dashboard yet.

**Cost:** at most 1 judge request per judged call, on another model's quota. Runs after the call, so it adds **0 latency**.

---

## 12. Handoff: what the human sees

Today the desk gets a deterministic brief (reason, facts, actions done). Add:

1. **An AI summary of the conversation so far.** Three to five lines: the caller's goal, what was answered, what was done (with references), what's still open, and the caller's mood. Generated when the transfer starts, from the redacted transcript and facts, with a **figure check** (every number in the summary must be in the record). If it fails the check, the deterministic brief is shown alone. The deterministic brief stays as the trusted part; the summary is marked "AI summary".
2. **The full conversation on the desk**: a scrollable transcript of every turn (caller and agent) with timestamps, the verification status and actions taken. Read-only and redacted. This uses the same data as the call record page.
3. **The reason for transfer and the open request** at the top, in one line: "Unrecognised ₹2,000 debit on card 4410; card blocked (ref AU…); wants dispute."
4. **Case record written before the transfer** (Dograh's point). If the transfer fails, the record already exists. That's already true for tickets; make it true for every handoff.

**Not changed:** queues. The reviewer agrees they're right. Two adjustments from that review:
- **ATM cash not dispensed** becomes its own dispute subtype (owning queue: Payments & Disputes);
- **grievance escalation levels** (Nodal Officer → Principal Nodal Officer → RBI Ombudsman) are added to the complaint flow's wording and tickets, not as new queues.

NRI and Business banking stay out until AU confirms they're in scope.

---

## 13. Deployment and scale (plan only, not for now)

> **Reference only. Nothing here is built for the demo.** All numbers below are **illustrative scenarios, not capacity commitments.** Real sizing needs AU's call arrival pattern, durations and peaks, measured turns per call and LLM calls per turn (many turns need none), the retrieval rate (not every turn uses RAG), and GPU benchmarks for the chosen model.
>
> **Data residency** (keeping data in India) is likely AU's required design, but the exact rules (RBI payment-data localisation, outsourcing rules, AU's own policy) must be confirmed with AU's compliance team. Hosting the app in the bank doesn't by itself mean no data leaves. A full data-flow inventory is needed: LLM, STT and TTS providers, monitoring and error reporting, support tools.
>
> The only preparation now: keep the ports and adapters clean, and keep mock-bank assumptions out of the conversation code.

How the same design runs for a bank with millions of customers. The sizing driver is **concurrent calls**, not requests: each live call holds a media stream. As a rough example, 1 M calls a day with a peak hour at ~12% and 4-minute calls gives **~8,000 concurrent calls** at peak.

```
PSTN / toll-free ──► SBC (Cisco CUBE) ──SIP/RTP──► Media gateway (FreeSWITCH or LiveKit SIP)
                                                        │ 16 kHz PCM per call (WebSocket/gRPC)
                                                        ▼
                                      Voice workers (stateful, 1 pod ≈ 50–100 calls; VAD on CPU)
                                        │ streaming          │ text              │ audio
                                        ▼                    ▼                   ▲
                                   STT pool (GPU)       Brain API (stateless)   TTS pool (GPU/vendor)
                                                        │   │   │   │
               ┌────────────────────────────────────────┘   │   │   └──────────────┐
               ▼                                            ▼   ▼                  ▼
     LLM serving (vLLM on GPUs,        Embed+rerank (GPU, TEI/Triton)   API gateway → AU systems
     India region; or bank-approved                                      (CIF, CBS, cards, LMS, CRM…)
     hosted model)
               State: Redis (session state, pending, rate limits, desk pub/sub)
               Data: Postgres HA (calls, audit chain, KB + pgvector; read replicas)
               Recordings: object storage, AES-GCM, keys in KMS/HSM, retention policy
               Async: queue (Kafka or Redis Streams) → post-call judge, analytics, alerts, CRM sync
               Observability: OpenTelemetry traces per call (audio→STT→LLM→tools→TTS), Prometheus/Grafana, Loki
```

- **Packaging:** each service is a Docker image (brain, voice-worker, mockbank/adapters, UI, judge-worker); images go to the bank's container registry.
- **Kubernetes:**
  - voice workers scale on a **custom metric (active calls per pod)** with graceful drain, so a call isn't cut during scale-in;
  - the brain scales on CPU and latency;
  - GPU node pools for STT, LLM and embeddings;
  - pod disruption budgets for all of them.
- **Data residency:** all in an Indian region (RBI data localisation), with no customer data sent to providers outside India. This is why "India-hosted LLM" is on the production list.
- **Resilience:**
  - two availability zones;
  - per-dependency circuit breakers and fallbacks (§7.3);
  - if the AI tier is down, the IVR routes straight to human queues ("fail to human").
- **Rough capacity at 8k concurrent:**
  - one LLM request per ~6–8 s per call ≈ 1,000–1,300 LLM requests/s at peak, so a self-hosted vLLM fleet (tens of GPUs for a 20B model) or an enterprise contract;
  - embeddings and rerank ≈ same QPS, a few GPUs;
  - STT streaming ≈ 8k streams, the biggest GPU cost.

  These are order-of-magnitude numbers to size a POC, to be load-tested.
- **Rollout:** shadow mode (the agent listens, a human answers) → information-only journeys → account reads → actions, each gated by the eval tiers and a canary at 1–5% of calls.

What we have today maps onto this cleanly: the ports and adapters, the separate voice session and brain, Postgres, the audit chain and the desk. Missing for production: SIP, Redis, the queue, OpenTelemetry and K8s manifests. None are needed for the demo.

---

## 14. The vendor architecture slide: how we compare

**Whose slide it is:** **Gnani.ai**. The boxes are labelled "Gnani SIP Server", "Gnani Agent Orchestrator", "Gnani ASR layer" and "Gnani TTS layer".

**What it shows:** everything sits inside a **"Customer Cloud"** box. The whole stack is deployed in the bank's own cloud or data centre, so call audio and customer data never leave the bank. The pipeline is cascaded: speech to text, then an LLM bot, then text to speech. Their speech models are built in-house for Indian languages and phone audio.

**About the company:**
- CEO: Ganesh Gopalan.
- Products: the Inya.ai agent platform, Armour365 voice biometrics, Assist365 agent assist, Automate365 and Aura365 analytics.
- Under the IndiaAI Mission they:
  - were picked to build a 14B-parameter voice foundation model;
  - released Inya VoiceOS, a 5B voice-to-voice research preview trained on 14M+ hours of speech (Feb 2026);
  - released Vachana STT.
- Unverified vendor claims: 200+ clients, 30M interactions a day, Indian banks among their customers.

The comparison and conclusion are below.

| Their box | Ours | Adopt? |
|---|---|---|
| SIP server, customer dialer | Browser WebSocket | Production (Cisco), as planned |
| Agent orchestrator | `voice/session.py` | ✅ have |
| ASR layer: **VAD, language identification, ASR** | Silero VAD + Whisper; language from the understanding call | Add the STT vocabulary hint (§6.4). Separate language ID not needed (Whisper detects it). |
| TTS layer: SSML parser, text preprocessing, **lexicon and pronunciation**, prosody | `speech_text` (numbers, lexicon) + Edge TTS | Pronunciation test (§8); SSML only with a production TTS |
| Bot: **prompts layer, ranking and resolver engine, RAG, LLM, business logic, integrations, response generator** | Prompts, reranker, RAG, LLM, journeys, `Bank` port, templates | **Adopt the explicit "response generator"** (§4.2: facts → validated → phrased) and an **integrations layer** (§3.3). The rest we have. |
| Call storage | Encrypted recordings | ✅ have |
| Dashboard, monitoring/alerting | `/calls`, alerts | Add per-call quality scores (§11) |
| Logging server + log storage, Redis, container registry | Postgres + files | Production (§13) |

**How well have we done?** The structure is the same as the production stack. The gaps are in the quality of the middle layer (understanding with several requests, state, response generation) and in production plumbing, not in missing big boxes.

---

## 15. UI fixes

Measured at 390 px (phone) and 1024 px:

- **`/calls` overflows to 1,557 px wide** on a phone. A card header row doesn't wrap: a long call id or question with no `min-w-0` / `truncate`, and buttons in a non-wrapping flex row. This is the main "doesn't fit the screen" bug.
- **`/kb` documents table** is wider than the screen (577 px at 390). It needs a horizontal scroll container on small screens, or columns that collapse into the row.
- `/` and `/desk` fit at 390 px.

**Plan:**
- one pass over all four pages at 390 / 768 / 1024 / 1440:
  - `min-w-0` on flex children;
  - wrapping action rows;
  - tables in `overflow-x-auto` with sticky first columns;
  - transcript panels with `break-words`;
  - a collapsible side nav under 1024 px.
- **Additions:** quality-score chips and a review panel on `/calls` (§11); the transcript plus the AI summary on `/desk` (§12).

---

## 16. Clean slate for your new test calls

As you asked, delete all current call data before you record new conversations:

- **deleted:** sessions, turns, call events, alerts, recordings (DB rows + encrypted files), handoffs and messages, tickets;
- **reset:** the mock bank's card states and scenario data, re-seeded.

The knowledge base and eval history are **not** touched. I'll do this as the first step once you approve the plan; it can't be undone.

---

## 17. Order of work

Each step ends with T0 tests + the T2 smoke set, and "no worse than before" on accuracy and first-audio latency.

| Step | What | Why first |
|---|---|---|
| 1 | Clean slate (§16); OTP and PII redaction (§9); KB fix: 7.90% → 7.75% (§5.3) | Security first |
| 2 | Eval tiers T0–T2 with the replay cache and judge-model split (§10) | So everything after is measured without burning quota |
| 3 | Understanding v2 + state + registry (§3.1, §3.2) | Fixes A, E, F and multi-intent; everything else builds on it |
| 4 | `mockbank` service with separate APIs + `HttpBank` adapter + relative dates (§3.3) | Realistic tools; status table; idempotency |
| 5 | Workflows: unrecognised debit, lost card, charge explanation (§3.4, §5.5) | Fixes K and the fraud gaps |
| 6 | Response generation + style rules + small talk + fragments (§4, §6.2, §6.3) | The "human feel" |
| 7 | KB document tags + variant / authority / conflict checks (§5) | Fixes G, H and the 7.75 vs 7.90 case |
| 8 | Post-call judge + `/calls` review UI (§11); desk transcript + AI summary (§12) | Quality visibility |
| 9 | Pronunciation test (§8); STT vocabulary + garbage rejection (§6.4); fallback model (§7.3) | Polish and robustness |
| 10 | UI responsive pass (§15); docs; final T3 run with numbers | Finish |

**Rough size:** steps 1–3 are the biggest (≈ 40% of the work); 4–7 ≈ 45%; 8–10 ≈ 15%.

---

## 18. Deliberately not doing (and why)

- **LLM function-calling / agent loops:** slower (+0.6–1 s per hop) and less safe than the code registry (§3.3).
- **Eight separate mock microservices:** one `mockbank` process with per-system routers and schemas gives the same realism (HTTP, separate IDs, failures) without the ops overhead. Splitting is a deployment choice later.
- **A 1,100-item test suite:** 150 risk-weighted routing items + 40 smoke + 15 multi-turn scripts fit the quota and still give per-class numbers (§10).
- **Per-figure policy records:** document-level tags are enough (§5.1).
- **ML PII models, voice biometrics, Kafka on the laptop:** not needed for the demo; they're in the production plan (§13).
- **New queues (NRI, Business):** until AU confirms scope.
- **From the second review, skipped:**
  - Precision@5: needs every chunk labelled; low decision value.
  - "Entity confidence" from the LLM: models' self-reported confidence isn't reliable. We read back high-impact values instead.
  - A circuit-breaker library: a counter does the job.
  - A conflict-resolution engine: the one real conflict was a stale document, fixed in the data.
  - LLM-judging every production call: risk-weighted selection instead.
  - Real-LLM load testing: not possible on the free tier; we load-test our own code with the fake LLM.
  - Fixing speaker echo: browser-only; use earphones (§2).

---

## 19. Questions for your PM (the short list that changes what we build)

From the 37 in your notes, these are the ones whose answers change design:

1. **Top 20 call reasons** at AU's contact centre, and which must be **completed** by the agent vs only **explained**?
2. **API catalogue:** is there an API gateway with OpenAPI or Swagger for CIF, CBS, cards (debit vs credit), payments, LMS and CRM? Is there a sandbox and test credentials? One CIF across all of them?
3. **Which actions are allowed by voice** after OTP, and which need step-up or a human?
4. **Approved product documents:** who owns them, how often do rates and charges change, and is there an effective-date register (for §5)?
5. **Disclosure and recording wording** that compliance requires, plus the call-recording retention period.
6. **Queues:** are the 10 departments real, what are their hours, and is there a priority or RM desk?
7. **Languages** beyond English, Hindi and Hinglish for the first release?
8. **Benchmarks** they expect: containment, latency, accuracy and escalation targets.

---

## Sources

- LLM-as-judge for calls: [Hamming: LLM grader rubric](https://hamming.ai/blog/llm-grader-voice-agent-call-scoring-rubric), [Benchmarking LLM judges for voice agents (arXiv)](https://arxiv.org/pdf/2608.24314), [OpenRouter: LLM-as-a-judge](https://openrouter.ai/blog/tutorials/llm-as-a-judge-evaluate-ai-agents/), [Cekura custom metrics](https://docs.cekura.ai/documentation/key-concepts/metrics/custom-metrics)
- Which calls to review: [Opus Research / destinationCRM: rethinking 100% QA](https://www.destinationcrm.com/Articles/Columns-Departments/The-Last-Line/Too-Much-Too-Little-Too-Late-Rethinking-%e2%80%98100-Percent-Coverage-in-Contact-Center-QA-173145.aspx), [Etech: AI QA beyond sampling](https://www.etechgs.com/blog/ai-quality-assurance-scaling-beyond-sampling/), [Lorikeet: 100% QA](https://www.lorikeetcx.ai/articles/best-ai-100-percent-ticket-qa-2026)
- Production voice-agent practice: [Dograh: banking inbound support](https://www.dograh.com/hub/blogs/ai-voice-agents-banking-inbound-support), [CARI-Tech architecture](https://cari-tech.com/strategy/ai-voice-assistant-banking-architecture/), [Backbase: governance](https://www.backbase.com/blog/voice-agents-in-banking-use-cases-governance), [Bearworks tutorial](https://www.bearworks.com/post/build-inbound-voice-ai-agent-tutorial), [Master of Code](https://masterofcode.com/blog/voice-bots-in-banking), [Sharvin Shah: voice AI agents](https://www.sharvinshah.com/guides/voice-ai-agents), [Naitive case studies](https://blog.naitive.cloud/voice-ai-in-banking-5-case-studies/), [voice.ai banking](https://voice.ai/ai-voice-agents/banking/)
- Vendor stack: [Gnani.ai voice AI stack](https://www.gnani.ai/resources/blogs/unlock-next-gen-engagement-with-gnani-ais-voice-ai-stack)
- The ResearchGate paper could not be read (the site refused automated access).
