# AU SFB Inbound Voice Agent: Scenario Map (v0.1, research stage)

Purpose: list every situation the agent can face, group them, and decide what should happen in each, before we choose any technology.
Sources: the problem statement, Sample Questions, the dummy data workbook, the 4 KB docs and the rates PDF. The playbook md was used only as background reading.

---

## 1. What we are building

An inbound voice agent for AU Small Finance Bank. It has to:

- Take a call, settle the caller's language (English, Hindi, Hinglish, with room for more Indian languages), and cope with language switching, accents, noise, interruptions and corrections.
- Know which of five moves to make on each turn: **answer, clarify, confirm, act, transfer**. The brief's closing line says this is what strong submissions get right.
- Authenticate the caller before telling them anything about their own accounts or taking any action.
- Read customer data and take *permitted* actions (card block, card controls) through mock bank interfaces, and get explicit confirmation before acting.
- Hand the call to the right team, with context, when it can't or shouldn't handle the request itself.
- Keep an audit trail of the whole call: intents, authentication, confirmations, system calls, outcomes and escalations.

**Minimum demo:** 1 informational request, 1 personal-information request, 1 controlled action and 1 human transfer.
**Scoring weights:** customer experience 30, multilingual 20, banking outcome 20, security and audit 15, latency 10, innovation 5.

---

## 2. Findings from the inputs that change the plan

### 2.1 The dummy data can't answer many of the sample questions
Customer_Master has **one row per customer**. Each row holds 1–3 savings accounts, but only **one** FD total, **one** loan, **one** debit card and **one** credit card.

| Missing from the data | Sample questions that need it |
|---|---|
| A flag marking the salary account | "Which is my salary account", "debit card on my salary account" |
| More than one FD, FD numbers, principal, tenure, open date, auto-renew, payout type | Multiple FDs, FD ending 7824, maturity amount, renewal, break value |
| More than one loan, loan type, rate type, EMI history and status | Home + personal loan, EMI failed or increased, bounce charges, foreclosure amount |
| Credit card transactions, minimum due, payment history, lounge usage, block status | ₹8,499 unrecognised charge, minimum due, "payment not reflecting", lounges left |
| A second credit card | "Block only the card ending 6712" |
| Card block status (temporary or permanent), a domestic/international flag, a contactless flag | Unblock, domestic-only online use, travelling abroad, disabling contactless |
| Merchant name, counterparty, UPI/card channel and status on transactions | "Restaurant ₹1,250", "from Rajesh", "merchant says failed", "UPI or card" |
| Ledger vs available balance, holds, charges | "Why is my available balance lower", "minimum-balance charge deducted" |
| Branch type (urban or core) | The minimum balance for Value, Women and Senior Citizen accounts depends on it |

**Data quality issues:**
- Transaction dates run across all of 2026, including dates after today (2 Nov 2026).
- Some due dates are already past, and some bill dates fall after their due dates.
- EMI debit amounts in the transactions don't match the EMI amount on the customer record.
- 25 customers have a credit card but no card type. One credit card last-4 is used by two customers.
- The sheet is named "Last_5" but holds 2–20 transactions per account.

### 2.2 The knowledge base has gaps and conflicts
- The Platinum Savings account has no section of its own; it only appears in comparison tables.
- The Royale section appears twice.
- Neither the KB nor the rates sheet gives an FD premature-withdrawal penalty.
- The Personal Loan rate is "from ~11%" in the KB but "up to 30%" in the rates sheet. The KB also doesn't list the documents needed for a personal loan.
- The Vikaas account's monthly credit limit is given as both ₹1 lakh and ₹5 lakh. The KB itself flags this.
- The Zenith+ fee differs between the CC KB and the rates sheet.
- The KB says savings interest is "up to 6.75%", while the rates sheet gives a tiered slab table.
- The rates PDF is a two-column layout and its text comes out scrambled when extracted. We will need to transcribe the rates into a clean table by hand. Tenure boundaries such as "1 year" (12 months vs 12 months + 1 day) change which rate applies.
- The KB has nothing on salary accounts, dispute or complaint turnaround times, or ATM-failure reversal rules.
- **Rates change.** The rates sheet is dated 23 Apr 2026. Every approved answer card should carry an "effective from" date, with a simple process to update cards when rates change.

**What this means:** the agent will regularly face questions it can't fully answer from its sources. "Data not available" is a major scenario group in its own right (Group G2-E below), not an edge case. We also have to decide (see §6) whether to extend the mock data.

---

## 3. How the scenarios are grouped

Four independent axes. Every call combines one value from each:

1. **What is asked**: information, personal data, action, or complaint/dispute.
2. **What it needs**: public KB only, authentication, authentication + confirmation, or a human.
3. **How the conversation behaves** (cross-cutting): ambiguity, corrections, switches, context, language, noise, emotion.
4. **What can go wrong** (cross-cutting): failed authentication, missing data, backend failure, low confidence, misuse.

The front door (GF) decides who is calling. The domain groups (G1–G6) are the journeys. The cross-cutting groups (X1–X4) are layers that can apply on top of any journey.

---

## 4. Scenario groups and plan of action

### G0. Call lifecycle (every call)

| # | Scenario | Plan of action |
|---|---|---|
| G0-1 | Call connects | Short greeting, ideally bilingual (Hindi + English), under about 6 seconds. It must say: this is **AU Bank's AI assistant**, **the call is recorded**, and the caller can speak in any language. Then let the caller speak. Detect the language from the first utterance rather than using a "press 1 for Hindi" menu. |
| G0-2 | Language unclear or not supported | Ask once in Hindi and English. If the language isn't supported, apologise and offer a transfer to a human. |
| G0-3 | Caller is silent at the start | Prompt again after a few seconds, then once more, then end the call politely and log it as "no input". |
| G0-4 | Request finished | Restate the outcome in one line (including any reference number), ask "anything else?", then close. |
| G0-5 | Caller hangs up mid-flow | Finalise the audit record with the state reached. If an action was in flight, its outcome must still be recorded (see X4). |
| G0-6 | After the call | Write the interaction record: summary, intents, result, escalation and timing figures. |
| G0-7 | Repeat caller (optional) | "I called earlier about my card": pick up the recent call's context (intent, outcome, reference number) and say so, instead of starting from scratch. |

### GF. Front door: who is calling + what they want (every call)
The agent checks the caller ID silently against the registered mobile numbers (the dummy data has one for every customer). It never opens with "are you a customer?". It then combines **who** the caller is with **what** they want:

| | General question | Personal information | Action |
|---|---|---|---|
| **Known customer** (caller ID matches) | G1, no verification | Verify (OTP) → G2 | Verify (OTP) → G3 / G4 |
| **Unknown number** (may be a customer on another phone) | G1, no verification | Ask for registered mobile or account number → OTP to the *registered* mobile → G2 if it matches | Same → G3 / G4 |
| **Not a customer** | G1 | Explain politely that personal details need a registered account, then offer G6 | Same as the cell to the left |

| # | Scenario | Plan of action |
|---|---|---|
| GF-1 | Known customer, general question | Answer (G1). Don't verify until something personal is asked (X2-3). |
| GF-2 | Known customer, personal info or action | Verify with OTP, then G2 / G3 / G4. |
| GF-3 | Customer calling from another number | Ask for registered mobile or account number, then send OTP to the *registered* mobile (never to the calling number). |
| GF-4 | Caller says they have no account | Go to G6. This is a sales opportunity, not a failure. |
| GF-5 | Verification fails repeatedly | Security event: no personal disclosure, offer general help or a human, log it (X2-4). |

**Rule:** "couldn't verify" (GF-5) and "not a customer" (GF-4) are different outcomes with different tone and logging.

### G1. General information (no authentication)
Source: KB plus the rates table. Sample sections 8, and the general parts of 1, 2, 3 and 4.

| # | Scenario | Example | Plan of action |
|---|---|---|---|
| G1-A | Direct fact lookup | "Senior citizen 1-year FD rate", "minimum balance for Value" | Answer from approved content only. Ask when the answer depends on something unknown, such as tenure boundaries or urban vs core branch. |
| G1-B | Needs-based product suggestion | First account with no minimum balance; salary ₹40k; ₹5L for 1 year; flexible FD; lounge card; online-shopping card | Ask 1–2 qualifying questions. Offer 2–3 options with the trade-offs. Present this as product information, not personal financial advice. Offer to connect sales or a branch. |
| G1-C | "Is this rate fixed or does it depend on my profile?" | Personal loan rate | State the published range and explain that the actual rate is profile-based. Don't quote a personalised rate. |
| G1-D | The KB has no answer or conflicts with itself | FD premature penalty, PL documents, Vikaas limit | Say that this detail isn't available on the call. Never invent an answer. Offer a transfer, a callback or the website. |
| G1-E | A general question that is really about the caller | "Minimum balance for *this* account", "benefits of *my* account" | Step up to authentication (X2), look up the account type, then answer from the KB (G2-D). |
| G1-F | Out of scope or off-topic | Stock tips, other banks, chit-chat | Politely decline, steer back, offer a human if they persist. |

### G2. Personal information, read-only (authentication required)
Source: customer data. Sample sections 1–7.

| # | Scenario | Example | Plan of action |
|---|---|---|---|
| G2-A | One clear value | Loan outstanding, next EMI, CC due date, balance when there is one account | Authenticate, fetch, then say the value from a template with the amount spoken naturally (lakh/rupees in the caller's language). Mask identifiers to the last 4 digits. |
| G2-B | Several products of the same kind | "My balance" with 2–3 accounts, two cards, two loans | Either list everything briefly ("ending 0001: ₹X, ending 0002: ₹Y") or ask which one. The rule: list everything when there are 3 or fewer, otherwise ask. Accept answers like "the salary one", "ending 4521" or "the second one". |
| G2-C | A value computed from the data | Totals across accounts and FDs, highest balance, available credit (limit − outstanding), EMIs left, UPI spend this week, transactions over ₹5,000 in 7 days, anything due in the next 7 days | Compute the figure in code, never with the language model. Anchor dates to a fixed "today". Say plainly what history the figure covers ("based on your recent transactions"). |
| G2-D | Customer data combined with the KB | Benefits of my account type, which debit card goes with my account type, my lounge entitlement, why I was charged an annual fee (waiver rule) | Fetch the account or card type, look it up in the KB, and answer. Say what can't be confirmed (for example, actual lounge usage). |
| G2-E | The data needed doesn't exist | Salary flag, merchant or counterparty, available vs ledger balance, auto-renew, maturity amount, EMI failure reason, payment posting | Say clearly what *can* be seen and what *can't* ("I can see a ₹799 POS debit on 1 Nov; the merchant name isn't available to me"). Then offer the next step: raise a request, transfer, or point to the app or statement. Never guess. |
| G2-F | Inference from the data | Which account is the salary account (the one receiving "Salary Credit" entries); FD still active (maturity date in the future); FD already matured | Present it as an inference ("salary credits come into the account ending 0003"), not as a fact from the system. |
| G2-G | Privacy request | "Don't read out full numbers" | Always mask by default. Never read out full account or card numbers, even when asked. |

### G3. Controlled actions (authentication + explicit confirmation)
Source: the card APIs. Sample sections 4 and 5, multi-turn case C, and Hinglish case H3.

| # | Scenario | Example | Plan of action |
|---|---|---|---|
| G3-A | Permanent block of a lost, stolen or ATM-retained card | "Card missing, block it now" | Authenticate. Identify the card: type (debit or credit) and last 4; if the customer has more than one, they must choose. Confirm in one sentence that names the card and says the block can't be undone. Wait for a clear yes. Execute. Check the system's result. Give the reference number, send a confirmation SMS (simulated), and explain the next step (replacement card). Make this flow urgent but not rushed. |
| G3-B | Temporary block or unblock | "Not sure if it's in my wallet", "re-enable yesterday's temporary block" | Same flow. Make the difference between temporary and permanent explicit. Unblocking needs block status data, which we don't have yet (§6). |
| G3-C | Change channel controls (online/ECOM, POS, ATM) | "Enable online", "disable online and contactless, keep ATM" | Read the current state first. Restate the full change as one summary and confirm once. Explain anything we can't do (domestic vs international split, contactless). Apply, verify, then read back the new state. |
| G3-D | "Tell me before you change anything" | Show what's enabled first | Read-only answer. No action until a new request is made. |
| G3-E | Customer stops or redirects mid-action | "Wait, which card?", "don't block yet" | Pause the action immediately and answer the question. Restart confirmation from the beginning. An earlier "yes" no longer counts once the target changes. |
| G3-F | Unclear confirmation | "Haan… actually wait", silence, "okay okay" during a correction | Treat it as *not confirmed* and ask again. Only a clear yes in a clean turn counts. |
| G3-G | Card not found | "Block 6712" when there is no such card | Say no card matches and list the cards on file (masked). Never block something else by default. |
| G3-H | Explicit "don't do it" | "Don't close the FD, just tell me charges", "don't make any payment" | Lock the turn as information-only and say back that nothing will be changed. Log it. |
| G3-I | An action the agent isn't allowed to do | Break an FD, pay a bill, foreclose a loan, raise a limit, change an address | Say it can't be done on this call and give the next step: transfer, branch or app. |

### G4. Complaints, disputes and fraud (protect first, then hand off)
Source: sample sections 4–6 and Hinglish case H1.

| # | Scenario | Example | Plan of action |
|---|---|---|---|
| G4-A | Unrecognised transaction (possible fraud) | ₹8,499 card charge, ₹799 debit | Find the transaction where the data allows. **Offer to block the card first**, then raise a dispute or transfer to the fraud team with context. Don't promise a refund. |
| G4-B | Money debited but the transaction failed | ATM gave no cash, merchant says failed, declined twice | Acknowledge, show the matching debit, and raise a complaint or transfer. Only quote a turnaround time if it's in approved content (it isn't today). |
| G4-C | Payment not showing | CC payment made yesterday, EMI failed with enough balance | Show what the data says, explain the gap honestly, and transfer to cards or loans. |
| G4-D | Charge grievance | Annual fee, minimum-balance charge, bounce charge | Explain the rule from the KB (fee or waiver). For a reversal, transfer or raise a request. |
| G4-E | Possible duplicate charge | "Charged twice" | Look for matching amounts on the same date. Report what was found and route to disputes. |

**For all of G4:** every complaint or dispute gets a **ticket ID**, read back to the caller and logged. If the caller asks how to escalate, explain the route: the bank first, then RBI's complaint portal if it's unresolved after 30 days.

### G5. Human escalation
| # | Trigger | Plan of action |
|---|---|---|
| G5-A | Caller explicitly asks for a person | Don't argue. Say you're connecting them and do so. At most one quick "can I help with X first?", and only if it doesn't add friction. |
| G5-B | Frustration or repeated failure (2–3 misunderstandings or negative sentiment) | Apologise once and offer a transfer proactively. |
| G5-C | Sensitive or out-of-policy request | Fraud dispute, death of an account holder, legal matters, hardship or restructuring, complaints about staff | Route straight to the right team. |
| G5-D | Low confidence or unsupported language | Transfer once one clarification attempt has failed. |
| G5-E | Backend unavailable | Transfer, or offer a callback. |
| G5-F | No human available (out of hours, queue full) | Say so plainly. Offer a callback or a ticket, give the expected time, and log it with the handoff packet so the callback starts with full context. |

**Routing table (draft):** Cards (debit and credit); Fraud and Disputes; Loans; Deposits (FD/RD); Accounts and General Care; Sales and Products.
Every transfer carries a **handoff packet**: authenticated yes/no, language, intent, entities, what was already said or done, and why we're transferring. The customer must not have to repeat themselves.

### G6. New customer (no account yet)
Source: KB (product suitability, eligibility, how to apply). Sample section 8 already covers the information side.

| # | Scenario | Example | Plan of action |
|---|---|---|---|
| G6-A | Wants to open an account or take a product | "First account, can't keep a minimum balance" | Ask 1–2 questions to understand the need, then suggest 2–3 suitable products from the KB (same logic as G1-B). |
| G6-B | Wants to talk to someone | "Can someone from the bank call me?" | Transfer to sales, with the need captured in the handoff packet. |
| G6-C | Wants a callback | "Call me later" | Take only **name and callback number, with explicit consent**. Never ask for Aadhaar, PAN or other ID documents on the call. Log as a lead. |
| G6-D | Wants to do it themselves | "How do I open it?" | Explain the route from the KB: video KYC, the AU 0101 app, or a branch. |
| G6-E | Asks for personal account details | "What's my balance?" (no account) | Explain politely that this needs a registered account; offer G6-A. |
| G6-F | Non-customer calling about someone else's account | "My father's account…" | No disclosure (X2-5); offer general help or a branch visit. |

---

## 5. Cross-cutting layers (apply on top of any group)

### X1. Conversation dynamics
| # | Scenario | Example | Plan of action |
|---|---|---|---|
| X1-1 | Ambiguous target | "My account" with several accounts (multi-turn case A) | Ask with the options listed. Accept answers by name, last 4, type, position or nickname. |
| X1-2 | Self-correction within one utterance | "4521… sorry, 4512" (case B) | The last value given wins. Briefly say back the corrected value. |
| X1-3 | Intent replaced or reordered | "Outstanding… actually first the due date" (case D) | Answer the new first item. Keep the earlier request queued and offer it afterwards. |
| X1-4 | Several intents in one utterance | "EMI due date and how much balance to keep", "Salary credited? If not, when was the last one" | Split them and answer in order. Conditional follow-ups are resolved from the data. |
| X1-5 | Pronouns and follow-ups | FD → "how much from *it*" → "will *it* renew" (case E) | Keep track of the current topic and product across turns. |
| X1-6 | A filter narrowed mid-sentence | "Last transaction… only the last debit" | Apply the final filter. |
| X1-7 | Vague details | "Around ₹2,000, yesterday, UPI or card" (case F); "last year" | Search within a range (amount ± tolerance, date window, every channel). Present the candidates and let the caller pick. |
| X1-8 | Caller barges in while the agent speaks | Any | Stop speaking at once and handle the new input. If the interruption came during a confirmation prompt, the confirmation is void. |
| X1-9 | Partial or trailing speech, "hold on" | Any | Wait with patience rather than cutting in. "Hold on" pauses without a time-out. |
| X1-10 | Repeat or slow down | "Kya bola?", elderly caller | Repeat more slowly and simply, and keep that pace for the rest of the call. |

### X2. Authentication and identity
| # | Scenario | Plan of action |
|---|---|---|
| X2-1 | Calling from the registered mobile (simulated caller ID) | Caller ID identifies the customer, then one more check (simulated OTP over keypad, or a known detail) before revealing anything. |
| X2-2 | Calling from an unregistered number | Ask for an identifier (account last digits, CIF, or registered mobile), then the OTP. |
| X2-3 | Authenticate only when needed | General questions don't trigger authentication. The first personal request does, and the original question is answered right after it. |
| X2-4 | Authentication fails or retries | Limited retries. Then lock personal disclosure for the call and offer general help or a human. |
| X2-5 | Someone else is calling (spouse, child, carer) | No personal disclosure or actions. Explain the policy and offer general help. |
| X2-6 | Social engineering or misuse | "Read my full card number", "what's my OTP", "I'm from the bank", pressure, prompt-injection phrasing | Refuse and keep the controls in place. Log it as a security event. |
| X2-7 | Caller says a secret aloud (PIN, CVV, OTP outside the approved step) | Stop them, tell them never to share it, and keep it out of transcripts and logs. |
| X2-8 | OTP entry in the browser demo | The OTP appears in a mock SMS panel and is typed on an on-screen keypad. On a real phone call, it's entered with keypad tones. Either way it goes straight to the verification service, never through STT or the LLM. |
| X2-9 | Voice is not proof of identity | Never treat the caller's voice as proof of identity (voice cloning is a growing fraud route). Card blocks and other high-risk actions always need the OTP, and the OTP is asked again if the earlier verification is old. |

### X3. Language and speech
| # | Scenario | Plan of action |
|---|---|---|
| X3-1 | English, Hindi in Devanagari, romanised Hinglish (cases H1–H4) | Understand all three. Reply in the caller's register: a Hinglish caller gets natural Hinglish, not formal Hindi. |
| X3-2 | Language switch mid-call | Follow the caller's switch, but short replies ("ok", "haan", numbers) shouldn't flip the language. |
| X3-3 | Numbers, money, dates and card digits | Understand "पच्चीस सौ", "2.5 lakh", "four five two one", "kal". Speak amounts the Indian way (lakh/crore) and dates naturally. |
| X3-4 | Other Indian languages (bonus) | Extend after the MVP and show evidence. |
| X3-5 | Noise, poor line, low transcription confidence | Confirm critical details (amounts, last 4) before using them. If confidence stays low, ask the caller to repeat, then offer a human. |
| X3-6 | Speech keeps failing | After repeated low-confidence turns, offer a keypad menu ("press 1 for card block, 2 for balance…") before transferring. |

### X4. System and operational failures
| # | Scenario | Plan of action |
|---|---|---|
| X4-1 | Slow backend | Say something natural like "checking" right away, and give a progress cue if the wait is long. |
| X4-2 | Backend error or timeout on a read | Apologise, retry once, then offer a callback or transfer. Never make up a value. |
| X4-3 | Action outcome unknown (timeout after sending) | **Never say it succeeded.** Check the status. If it's still unknown, tell the caller, escalate, and log it as needing attention. Retries must not double-execute. |
| X4-4 | Partial data (some products failed to load) | Give what loaded and name what's missing. |
| X4-5 | Call drops during an action | The action finishes or rolls back on the bank's side. The audit record shows the final state. Optionally send an SMS confirmation (simulated). |
| X4-6 | Data looks stale or contradictory (past due dates, future-dated transactions) | Define a fixed "today" for the demo. Treat past-dated items correctly (for example, "this FD matured on…"). |
| X4-7 | A provider goes down | STT or TTS down → switch to the backup provider (for example AWS Transcribe / Polly). LLM down → a minimal scripted mode for the most common requests, or transfer. |
| X4-8 | Too many calls at once (concurrency limit reached) | Play a short "all lines are busy" message and offer a callback. Note for scale: Sarvam pay-as-you-go allows about 20 concurrent STT streams. |

### X5. Caller emotion and accessibility
- **Panic** (lost card, fraud): calm and short, take the protective action first, then explain.
- **Anger:** acknowledge once, don't over-apologise, offer a human early.
- **Elderly or slow speech, or a request for simple explanations:** slower pace, plainer words, one figure at a time.
- **Abusive language:** one warning, then end or transfer as policy allows.

### X6. Data and privacy (every call)
| # | Rule | Plan of action |
|---|---|---|
| X6-1 | Redact before the LLM | Names, account and card numbers, OTPs and other personal details are masked before any text reaches the LLM. The bank tools work on the real values; the LLM never needs them. |
| X6-2 | Keep data in India | Audio, transcripts and the audit log are stored in an India region. Only redacted text may go to an overseas LLM. |
| X6-3 | Use data only for this call | Recordings and transcripts are used to service the call and for audit. No model training on them without separate consent. |
| X6-4 | Retention | Keep transcripts and the audit log for the agreed period. Drop raw audio early where possible. |

---

## 6. Coverage: sample questions vs what our sources can support

Key: **Y** = answerable from the data or KB. **P** = partly (an inference, or missing a detail). **N** = not in the data or KB, so it falls under G2-E or a transfer unless we extend the mock data.

| Section | Y | P | N | Notes |
|---|---|---|---|---|
| 1. Savings (10) | 3 | 4 | 3 | Salary flag, AMB history and charges are missing |
| 2. FD (10) | 2 | 3 | 5 | Single aggregate FD; no FD numbers, renewal flag or penalty |
| 3. Loan (10) | 3 | 3 | 4 | Single loan, no type, no EMI status |
| 4. Credit card (12) | 3 | 5 | 4 | No CC transactions, minimum due, second card or lounge usage |
| 5. Debit card controls (10) | 5 | 2 | 3 | No intl/domestic flag, contactless, or block status |
| 6. Transactions (12) | 4 | 5 | 3 | No merchant, counterparty or status |
| 7. Relationship summary (7) | 6 | 1 | 0 | Mostly computed values |
| 8. Generic products (8) | 6 | 2 | 0 | PL documents and the rate conflict are partial |
| **Total (79)** | **32** | **25** | **22** | Only about 40% can be answered fully as things stand |

These counts are first-pass estimates. The per-question mapping is in Appendix A.

---

## 7. Decision ladder applied on every turn (tool-independent)

1. **Listen and understand.** Work out the intent or intents, the details, and the language. Check for safety triggers first: fraud, panic, a request for a human, secrets spoken aloud.
2. **Scope check.** Is this in scope? If not, decline or transfer (G1-F, G3-I, G5).
3. **Authentication check.** Does it need the caller's own data or an action? If they aren't authenticated yet, step up now (X2). Unknown number → ask for registered details (GF-3). No account → G6.
4. **Ambiguity check.** Is a critical detail missing or uncertain? Clarify (X1). Don't guess.
5. **Data check.** Does the source exist? If not, say so honestly and give the next step (G2-E).
6. **Action check.** Is this a write? Read the current state, confirm explicitly, execute, verify, then report (G3).
7. **Respond.** Use templated figures in the caller's language, short and masked.
8. **Close or continue.** Pick up any queued intents, ask "anything else?", and log.

---

## 8. Open decisions (needed before we discuss technology)

1. **Mock data: DECIDED.** Keep the dummy data exactly as given; do not extend it. Missing fields are handled through G2-E ("not available" plus a next step). The gaps are noted for later improvement.
2. **Authentication method** for the demo: simulated caller ID + OTP over keypad, caller ID + a known detail, or something else. The data has no DOB or PIN.
3. **"Today" for the demo:** a fixed date that the dates in the data make sense against.
4. **Escalation teams** and whether transfer is simulated (a handoff screen) or a real phone transfer.
5. **Where to draw the line on recommendations:** how far G1-B goes before it counts as advice.
6. **Languages:** English/Hindi/Hinglish only for the MVP, or add one more (Tamil, Marathi or Bengali) for the bonus.
7. **Channel:** a phone number (telephony) or a browser call for the demo.
8. **Approved fallback content:** turnaround times and penalty rules missing from the KB. Do we get them from AU, mark them "not available", or use public published values with the source cited?
9. **OTP rules:** OTP length, number of retries, and how long a verification stays valid before high-risk actions need a fresh OTP (X2-9).
10. **Retention period:** how long transcripts, audit logs and raw audio are kept (X6-4).
11. **Data-handling approach:** an India-hosted LLM (Sarvam, or Bedrock/Gemini via an India region) versus an overseas LLM behind redaction (X6-1, X6-2).

---

## Appendix A. Per-question mapping (first pass)

Format: question, then group / data support / note.

**1. Savings**
1. Balance after ₹2,500 payment: G2-A or G2-B / Y / data is static, so "just paid" may not show yet.
2. Both accounts' balances: G2-B / Y.
3. Which is the salary account: G2-F / P / inferred from Salary Credit entries.
4. Available balance lower: G2-E / N / no ledger figure or holds; general explanation only.
5. Minimum balance required: G2-D / P / branch type unknown.
6. AMB maintained this month: G2-E / N.
7. Minimum-balance charge deducted: G4-D / N.
8. Benefits of my account type: G2-D / P / Platinum has no KB section.
9. Debit card on the salary account: G2-D + G2-F / P.
10. Second account balance and charges: G2-B / P / balance only.

**2. FD**
1. FD from last year still active: G2-F / P.
2. Multiple FDs' values and dates: G2-E / N / single record.
3. Which matures first: G2-E / P.
4. Maturity payout: G2-E / N.
5. Rate on FD ending 7824: G2-A / P / no FD number.
6. Auto-renew: G2-E / N.
7. Value if broken today: G2-E / N.
8. Senior 1-year best rate: G1-A / Y / tenure boundary.
9. ₹2L for 3 years options: G1-B / Y.
10. Premature charges, don't close: G3-H + G1-D / N.

**3. Loan**
1. Outstanding: G2-A / Y.
2. Yesterday's EMI paid: G2-F / P.
3. Home vs personal outstanding: G2-E / N.
4. Next EMI date and amount: G2-A / Y.
5. EMIs paid and left: G2-A / Y.
6. Why EMI increased: G2-E / P / floating-rate explanation only.
7. EMI failed despite balance: G4-C / N.
8. Bounce charges: G4-D / N.
9. Foreclosure amount: G2-E or G3-I / P.
10. Closure amount, no payment: G3-H / P.

**4. Credit card**
1. Amount to pay: G2-A / Y.
2. Total, minimum due and due date: G2-A / P / no minimum due.
3. Payment not reflecting: G4-C / N.
4. Available limit: G2-C / Y.
5. ₹8,499 unrecognised: G4-A / N / no CC transactions.
6. Card missing, block: G3-A / Y.
7. "Which card?" interruption: G3-E / Y.
8. Block only 6712: G3-G / P.
9. International ₹50k: G2-E / P / limit only.
10. Lounge visits left: G2-D / P / entitlement only.
11. Why annual fee: G4-D / P.
12. Last statement, previous bill paid: G2-A / P.

**5. Debit card**
1. POS disabled?: G2-A / Y.
2. Enable online: G3-C / Y.
3. Domestic only: G3-C / P.
4. Disable online and contactless, keep ATM: G3-C / P.
5. Travelling abroad: G2-E / N.
6. ATM retained card, block: G3-A / Y.
7. Re-enable temporary block: G3-B / N.
8. Show status first: G3-D / Y.
9. Temporary block, lost wallet: G3-B / Y.
10. Declined twice, one SMS: G4-B / N.

**6. Transactions**
1. ₹1,250 restaurant: X1-7 / P.
2. Debited, merchant says failed: G4-B / N.
3. Transfer received by payee: G2-E / N.
4. Over ₹5,000 in 7 days: G2-C / Y.
5. ₹799 debit: G4-A / P.
6. Salary credited: G2-A / Y.
7. Refund arrived: G2-F / P.
8. From Rajesh: G2-E / N.
9. UPI spend this week: G2-C / Y.
10. Last debit (correction): X1-6 / Y.
11. Double charge: G4-E / P.
12. ATM no cash: G4-B / P.

**7. Relationship summary**
1–3, 5–7: G2-C / Y.
4. Inactive products: G2-E / P / no status field.

**8. Generic products**
1–6 and 8: G1-A or G1-B / Y.
7. ₹3L personal loan: G1-A / P / rate conflict, documents not listed.

**Multi-turn cases A–H:** X1-1, X1-2, G3-A + G3-E, X1-3, X1-5, X1-7, G5-A, X3-1. Case H also covers G4-A, X1-4, G3-B and X1-4.
