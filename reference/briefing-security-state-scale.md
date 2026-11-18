# Briefing: security, authentication, guardrails, evaluations, state, scale and cost

Prepared 9 Nov 2026 for review meetings. It answers ten questions about this platform:

1. Row-level security
2. Authentication
3. Hard-coded rules (the "firewall" question)
4. Guardrails
5. Evaluations
6. Conversation history and context
7. AWS Bedrock APIs and cost
8. Scale (100 to 1,000 calls at once)
9. Deployment
10. Other questions to be ready for

Each answer says what the code does **today**, with file and line, **why** it's done that way, and what production needs.

**How to read it:**
- **Measured** means a real number from this project.
- **Estimate** means a worked calculation. It must be confirmed by a load test.
- **Web** means it comes from the research sources at the end. AWS prices change, so check the AWS pricing page for Mumbai before quoting a figure.
- Nothing was run or changed in the code to write this.

**Every suggestion is labelled with when it's needed**, so nothing gets built just because it's possible:

| Label | Meaning |
|---|---|
| **Now** | Worth doing for the demo or POC itself |
| **Before go-live** | Needed before real customers call; not needed for the demo |
| **At scale** | Needed only when running more than one server or high call volumes |
| **Not needed** | Would be over-engineering for this project; kept here so you can say why it was rejected |

---

## 0. Summary for the meeting

| Topic | Today | Recommendation | Why |
|---|---|---|---|
| Row-level security | No database RLS. Access is enforced in code and at the bank's APIs by a customer-scoped signed token. | Keep the token check at AU's APIs as the main control. Add Postgres RLS on our calls database **before go-live**, together with SSO. | Customer data lives in AU's systems, so that's where "only your own rows" must be enforced. Our database holds call records; RLS there protects staff access, which only exists once SSO and roles exist. |
| Authentication | Server-side session in Postgres. 6-digit code by SMS, typed on the keypad, only its HMAC stored. **After** the code passes, a short-lived customer token is made for each bank call. | Shared secrets in Secrets Manager / KMS (at scale); per-mobile SMS limit (before go-live); narrower tokens issued by AU's gateway (before go-live, with the real APIs). | Keeps identity out of the LLM; secrets per process break with 2+ servers; SMS flooding costs money and harms customers. |
| Hard-coded rules | Many already exist: permission table, people-only tasks, "yes" read in code, keypad-only OTP, injection and fraud word lists, figure check, templates, refusal on weak evidence. | Outgoing-speech filter **done (11 Nov)**. Still to add: per-mobile SMS limit, kill switches, an abusive-caller rule (before go-live), and a network firewall (infrastructure, before go-live). | They hold even if the LLM is fully fooled. Anything bigger (a second AI moderator, Bedrock Guardrails) adds latency and cost for little extra protection. |
| Guardrails | Input, routing, retrieval, output and post-call layers, mostly in code. | Add the four items above plus a red-team test set. | RBI FREE-AI expects adversarial testing; the rest closes specific gaps found in the review. |
| Evaluations | Offline tests, smoke set, routing, decisions, golden set; per-turn retrieval gates; post-call judge. | Before go-live: re-run the golden set on the current prompt, a red-team set, human labels for the judge, phone-audio speech tests. | Current accuracy numbers are old or partial; the judge isn't calibrated; phone audio is harder than browser audio. |
| History and context | Stateless LLM calls; state in Postgres (6 trimmed turns, whole-call facts, call state). | Keep it. Add Redis only for live shared data when running 2+ servers. | Cheapest, safest and most portable; Redis would save about 10 ms of a 2–4 s turn. |
| Bedrock API | Our adapter uses the **stateless Converse API**. | **Converse, stateless**, with prompt caching. Not the Responses API with stored history. | With a stored chain, every turn re-bills the whole call: about **2.3× more input tokens** on a 20-turn call. It also breaks model fallback and keeps a second copy of conversations at AWS. §7. |
| Scale | One process; some state in memory. | About 6 small changes before running 2+ copies (§8.4). Get the Bedrock token quota raised early. | At 1,000 calls at once the LLM quota (about 20M input tokens a minute, estimate) is the first limit, not the database. |
| Cost (LLM only, estimate) | gpt-oss-120b: about **$0.02 per 4-minute call**; about $0.01 with caching. | Measure real token counts on Bedrock before committing. | Claude Haiku 4.5 is about 7× the price per token (about $0.14 per call). Speech-to-text and TTS aren't included and are likely larger. |
| Deployment | Nothing containerised yet; the plan is in PHASE-4-PLAN §13. | EKS in Mumbai across 2 availability zones; containers for the live path; serverless only for background jobs. | Live calls hold connections for minutes and the brain loads 2 GB of models, so neither fits serverless functions. |

**One finding, fixed on 11 Nov.**
- The comments in [bedrock.py](../brain/app/adapters/bedrock.py), `config.py` and `.env.example` suggested an `apac.` inference profile.
- **Why it mattered:** that profile can process requests **outside India**, which conflicts with RBI data localisation expectations and AU's likely policy.
- **Fix:** they now say to use the **India geo profile** (Mumbai + Hyderabad). Claude Haiku 4.5, Sonnet 5 and Opus 5 were added to it on 29 Sep 2026 (web).
- The decision is recorded in ADR 0026.

**Decisions recorded as ADRs:**
- [0026](adr/0026-stateless-llm-calls-india-only.md): stateless LLM calls, India-only inference.
- [0027](adr/0027-access-control-token-at-bank-rls-with-sso.md): customer data guarded at the bank's APIs; RLS with SSO.

---

## 1. Row-level security (RLS)

### 1.1 What it is, and why it's used

Row-level security means the **database itself** decides which **rows** a user may see or change. The application cannot bypass it.

- Ordinary permissions (`GRANT SELECT ON traces`) work per table: all rows or none.
- RLS adds a rule per row, for example "a desk agent sees only the handoffs of their own department".

In Postgres:

```sql
ALTER TABLE handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE handoffs FORCE ROW LEVEL SECURITY;            -- even the table owner obeys it
CREATE POLICY desk_sees_own_department ON handoffs
  FOR SELECT TO desk_agent
  USING (department = current_setting('app.department'));
```

For each request, the app runs `SET LOCAL app.department = 'cards'` inside the transaction. The database then filters every query, including ones written wrongly.

**Why teams use it:**
- **Defence in depth.** App code has bugs. With RLS, a missing `WHERE` clause can't leak another team's or customer's rows, because the database adds the filter itself.
- **Multi-tenant systems.** Many customers share one table, and the database guarantees separation.
- **Least privilege for staff.** Support, QA and admin see different rows without separate copies of the data.
- **Easier review.** The rule sits in one place, which a bank's security team can read and approve.

**Known pitfalls (web), and why each matters:**

| Pitfall | Why it matters |
|---|---|
| Use `SET LOCAL` per transaction, not a plain `SET` | A pooled connection is reused, so a plain `SET` leaks one request's identity into the next request |
| Don't connect as the table owner or a superuser; or use `FORCE ROW LEVEL SECURITY` | Owners and superusers skip RLS silently |
| Use `WITH CHECK` on write policies | Without it, a user can insert or move a row into another team's view |
| Index the column the policy filters on | The filter is added to every query, so a missing index makes everything slow |
| Don't use `current_user` as the identity | A connection pool shares one database user, so `current_user` is the same for everyone |

### 1.2 Where "who may see which row" matters in this project

| Data | Where it lives | Who must be kept apart | Enforced today | Where RLS would apply, and why |
|---|---|---|---|---|
| **Customer bank data** (accounts, cards, loans, FDs, transactions) | AU's core systems. Today the mock bank (`corebank` schema, [mockbank/](../brain/app/mockbank/)) | One customer from another | **At the bank API.** Every customer call carries a signed token naming one customer (CIF). Each system checks it: `caller()` checks the service and customer tokens; `owns()` returns 403 "this token is for a different customer" ([mockbank/core.py:94-122](../brain/app/mockbank/core.py)). | **Inside AU's systems, not ours.** AU owns this data and its databases, so only AU can put RLS there. Our part is sending a token that names one customer. RLS in our mock bank: **Not needed**; it would only demonstrate a control AU builds itself. |
| **Call records** (`sessions`, `traces`, `call_events`, `auth_events`, `recordings`, `call_reviews`, `fix_items`) | Our Postgres ([migrations/](../brain/app/migrations/)) | Staff roles (QA, desk, admin); one live call from another | **In code only.** The brain reads by `session_id`. Write endpoints need `X-API-Key` when `API_KEY` is set ([api/deps.py](../brain/app/api/deps.py)). **The `/calls` and `/desk` pages have no staff login.** | **Before go-live, together with SSO.** Roles: `brain_runtime` (only the current session's rows), `qa_reviewer` (reads calls), `desk_agent` (own department only). **Why then and not now:** RLS needs a known user and role per request. Today there are no staff logins, so there's nothing to filter on. |
| **Handoffs and desk messages** (`handoffs`, `handoff_messages`) | Our Postgres ([services/desk.py](../brain/app/services/desk.py)) | Department from department | In code (a queue per department) | **Before go-live**, same reason: a policy on `department` once desk staff log in. |
| **Knowledge base** (`documents`, `chunks`) | Our Postgres | Public product documents, not personal data | Not needed | **Not needed** unless internal-only documents are added. Then a tag plus a policy. |
| **Customer data sent to the LLM** | Never as raw rows | | Bank figures go through **templates**, not the LLM ([personal.py](../brain/app/services/personal.py), ADR 0024). History replaces such answers with "(account details were given)" ([store.py:32-45](../brain/app/services/store.py)). | Not a database question; already handled in code. **Why:** the LLM never needs raw bank data to phrase a reply, so not sending it removes the risk entirely. |

**What to say in the meeting:**
- The main boundary is "a caller sees only their own data". It is enforced where the data lives, at the bank's APIs, by a customer-scoped token that comes from the **verified session, never from the LLM**.
- Our own database stores call records, not bank accounts. RLS there protects **staff access** and adds defence in depth. It comes with SSO, because RLS needs to know who the user is.
- Today there are **no RLS policies** and only **one database user** (`pgserver`). That's acceptable for a single-machine demo with dummy data, not for production.

### 1.3 What adding RLS would take (before go-live, with SSO)

1. A migration with roles:
   - `brain_runtime`, `desk_agent`, `qa_reviewer`, `admin`;
   - `ENABLE` and `FORCE ROW LEVEL SECURITY` on the call tables;
   - policies using `current_setting('app.session_id')` and `current_setting('app.department')`.
2. In [db.py](../brain/app/db.py), when a connection is taken from the pool, run `SET LOCAL` for that transaction.
3. Connect the app with a non-owner role. Use RDS Proxy / PgBouncer in **transaction** mode, which is safe with `SET LOCAL`.
4. A test proving that a desk agent of one department cannot read another's handoffs, even with a hand-written query.

Effort: small to medium. Latency cost: negligible when the policy column is indexed.

**Not needed:** a separate database or schema per department or customer. RLS gives the same separation in one database with far less to run.

---

## 2. Authentication: what happens, when, and where it's stored

### 2.1 What kind of authentication this is, and why

There are **three layers**:

| Layer | Type | Where it's stored | Why this type | File |
|---|---|---|---|---|
| Caller identity | **Server-side session** (stateful). The browser or phone line holds only a session ID (a UUID). Verification state lives in the `sessions` row. | Postgres `sessions`: `caller_number`, `customer_id`, `verified_at`, `auth` (JSON: OTP hash, expiry, attempts, sends, stage). Every step goes to `auth_events`. | A phone caller has no browser to hold a token, and the call itself is the session. Keeping state on the server means the caller (or the LLM) can't tamper with it, and ending the call ends verification instantly. | [services/auth.py](../brain/app/services/auth.py), [migrations/002_auth.sql](../brain/app/migrations/002_auth.sql) |
| Brain → bank systems: who is calling | **Service token** (shared secret) in the `X-Service-Token` header on every bank call | Setting `bank_service_token` (`SecretStr`); random per process if empty ([config.py:96](../brain/app/config.py)) | The bank must know the request comes from our service at all, before it considers which customer | [http_bank.py:67-71](../brain/app/adapters/http_bank.py) |
| Brain → bank systems: for which customer | **Customer-scoped bearer token**, JWT-like (HMAC-SHA256 signed: customer, systems, scopes, expiry) | **Not stored anywhere.** Made fresh for each bank request and thrown away. Never shown to the LLM, the transcript, the desk or the browser. | Each bank system can check the customer on its own, without trusting the brain's code. Not storing it means nothing to steal. | [tokens.py](../brain/app/tokens.py), [http_bank.py:64-65](../brain/app/adapters/http_bank.py) |

So: **session-based** for the caller, **token-based** between our service and the bank. That split is normal for banks: the channel holds the session, and the core systems trust signed, short-lived tokens.

**Why not a JWT given to the caller's browser instead of a server-side session?**
- A token held by the client can't be withdrawn early: if it leaks, it works until it expires.
- A server-side session ends at hang-up, after 10 idle minutes, or on lockout, immediately.
- For a voice call there's no client to hold a token anyway.

### 2.2 Step by step

```
Call starts
  └─ a session row is created (UUID). caller_number = caller ID (simulated in the UI). Not verified. No token.
Caller asks something open ("FD rates?")        → answered from the KB. No verification needed.
Caller asks for their own data ("my balance")   → registry says access = "verified" (registry.py:45)
  └─ auth.start()  (auth.py:105)
       ├─ find_by_mobile(caller_number)  → bank lookup with the SERVICE token only; returns just enough to send a code
       ├─ unknown number → ask for the registered mobile (stage need_mobile)
       └─ _send() (auth.py:211)
            ├─ code = secrets.randbelow(1_000_000)        6 digits, cryptographically random
            ├─ store only HMAC-SHA256(secret, session_id:code), expiry (5 min), attempts = 0, sends += 1
            ├─ auth_events: otp_sent (masked mobile only)
            └─ "SMS": simulated in dev (shown in the test UI); a real gateway in production. Never logged.
Caller types the code on the KEYPAD → POST /v1/auth/verify (api/auth.py:32). Never speech, never the LLM.
  └─ auth.verify() (auth.py:139)
       ├─ locked / expired / wrong stage checks
       ├─ hmac.compare_digest  (constant time)
       ├─ wrong: attempts + 1; after 3, LOCKED for this session → transfer to a person
       └─ right: sessions.customer_id = CIF, verified_at = now, auth = {stage: verified}; auth_events: otp_verified
          The question asked before verification is answered now.
Every later turn: verified_customer() (auth.py:68)
  ├─ reads customer_id from the session row; expires after 10 min of no activity (auth_idle_s, sliding window)
  └─ for each bank call: a customer token is MINTED NOW, AFTER verification:
       {cif, aud: [8 systems], scp: [read, card_update, crm], exp: now + 900 s} signed with HMAC-SHA256
       Each bank system checks: signature, expiry, audience, scope, and that the CIF in the URL = the token's CIF.
Hang-up → auth.end(): verification cleared, the cached customer read forgotten.
```

**So the auth token is created after the SMS code is verified, never before.** Before verification, only the service token exists, and it can do nothing except "find by mobile" (enough to send the code).

**Why each design choice:**

| Choice | Why |
|---|---|
| Ask for verification only when the caller wants their own data | Most calls are product questions. Asking everyone for an OTP adds friction and SMS cost for no benefit. |
| Code typed on the keypad, never spoken | Spoken codes would pass through speech-to-text, the LLM, transcripts and recordings, all places a secret must never be. CLAUDE.md makes this a hard rule. |
| Only the HMAC of the code is stored | A database leak or a log line can't reveal a usable code |
| Constant-time compare | Stops timing attacks that guess the code digit by digit |
| 5-minute expiry, 3 tries, 3 sends | Industry-standard OTP limits; brute-forcing 1,000,000 combinations in 3 tries is hopeless |
| 10-minute idle expiry, sliding | A caller who walks away isn't left verified; an active caller isn't asked again |
| Token made per bank call, 15-minute life, never stored | If one ever leaked, it works briefly and for one customer only |
| Identity from the session, never from the LLM | A prompt injection ("I'm customer X") can't switch customer |

### 2.3 How secure it is today, and what to fix

| Good today | Why it matters |
|---|---|
| OTP keypad-only; never via speech-to-text, the LLM, logs or transcripts (`is_code_entry`, [privacy.py:100-103](../brain/app/services/privacy.py); `_code_turn`, [conversation.py:714](../brain/app/services/conversation.py)) | Recorded, transcribed or logged OTPs are a classic leak |
| HMAC-only storage; constant-time compare; expiry and attempt limits | Brute force and replay are limited |
| Identity comes from the session, **never from the LLM** | A prompt injection can't switch customer |
| The customer token is short-lived, signed, scoped by audience and action, and the CIF is checked by each bank system | Even a bug in the brain can't read customer B with customer A's token |
| Every auth step and every data disclosure is audited (`auth_events`: `otp_sent`, `otp_failed`, `locked`, `data_read` with field names, [auth.py:189](../brain/app/services/auth.py)) | Investigations; DPDP accountability ("who saw what, when") |

| Weakness | Risk | Fix | When, and why then |
|---|---|---|---|
| `_SECRET = secrets.token_bytes(32)` **per process** ([auth.py:30](../brain/app/services/auth.py)); `bank_token_secret` random if empty | With 2+ copies of the brain, a code sent by copy A fails on copy B. A restart breaks pending codes. | Secrets in **AWS Secrets Manager**, or signing in **KMS**; the same key for every copy | **At scale.** With one process it works correctly; it breaks only with 2+ copies. |
| One token covers all 8 systems and read + card_update + crm, even for a balance question ([http_bank.py:65](../brain/app/adapters/http_bank.py)) | More access than the turn needs (least-privilege principle) | Narrow it per request (systems and scopes for this topic), tied to ADR 0025 selective reads | **Before go-live, with AU's real APIs.** AU's API catalogue decides the scopes, so designing them on the mock would be guesswork. |
| Symmetric HMAC token: whoever can verify can also mint | A compromised bank system could mint tokens | Use **AU's** gateway tokens (OAuth2 token exchange), or KMS-signed asymmetric tokens | **Before go-live, decided by AU's gateway.** Most banks already run an API gateway that issues tokens; use theirs. Building our own token service would be **over-engineering**. |
| SMS limit is **per session** (`otp_max_sends = 3`) | An attacker opens many sessions and floods a customer with SMS ("SMS bombing"), harming the customer and costing money | A per-mobile daily limit (e.g. 10/day). A Postgres count works today; Redis at scale. | **Before go-live.** It's small, and SMS flooding is a known attack on OTP systems. |
| Text chat: whoever has the session UUID acts as that session for 10 minutes | Session IDs leak through logs or URLs | Voice: the telephony connection owns the session. Web chat (if ever launched): an HttpOnly cookie, never a URL. | **Before go-live only if there's a web chat channel.** For voice-only, **not needed**: the phone line itself is the session. |
| Caller ID is used to pick the mobile | Caller ID can be spoofed cheaply | Already handled: caller ID only chooses where the SMS goes; the OTP to the registered mobile is the real factor. Ask AU whether a SIM-swap date is available. | **Before go-live: ask AU.** If their systems expose it, block actions shortly after a SIM swap (a common bank control). If not, nothing to build. |
| Staff pages have no login | Anyone on the network can read `/calls` | SSO with roles, then RLS (§1.3) | **Before go-live.** SSO is already on the production list. |

**Does RLS use this token?**
- Conceptually, yes. The token's `cif` claim is exactly the value a bank-side RLS policy would key on. A bank service backed by Postgres would run `SET LOCAL app.cif = <cif from the verified token>`, and a policy `cif = current_setting('app.cif')` would filter.
- In **our** calls database, RLS would key on `session_id` and staff role, not on CIF. **Why:** most calls are never verified, so they have no CIF, and our database's risk is staff seeing too much, not customers.

**Not needed:** voice biometrics.
- Voice clones can fool them (web), and a bank still needs OTP as the real factor.
- They add cost and a sensitive biometric store.

---

## 3. "Firewall": hard-coded rules for when guardrails fail

The concern in the meeting was this: if the LLM guardrails fail, something deterministic must still stop misuse. That's the right concern, and the design already follows it (ADR 0008, "LLM understands, code decides").

**Why this principle:** an LLM can be talked into anything with the right words. Code can't. So every decision with consequences (who you are, what you may see or change, which numbers may be said) is made by code. The LLM only reads the request and writes the words.

There are two meanings of "firewall", and both are needed.

### 3.1 Already hard-coded (works even if the LLM is fully fooled)

| Rule | File | Why it holds |
|---|---|---|
| A fixed **permission table**: each request type is open / verified / confirm / people | [registry.py:34-54](../brain/app/services/registry.py) | The LLM only names a request type; code decides access. An unknown type becomes "out of scope". Any change not listed goes to a person (`people.change`, line 49). |
| **People-only actions**: closing an FD, payments, limits, address or mobile change, refunds | registry.py, docstring line 10 | The agent has no code path for these, so no prompt can trigger them. The bank's risk owners decided these need a human. |
| Card actions only on the caller's own cards, from an **allow-list** of settings | [actions.py:21](../brain/app/services/actions.py) (`CONTROL_KEYS`), `plan()` at line 119 | Code picks the card from bank data, not from the LLM |
| **Explicit "yes"** read in code before any change | [confirm.py](../brain/app/services/confirm.py), [conversation.py:448-470](../brain/app/services/conversation.py) | An LLM could "hear" a yes that wasn't said; code reads only clear yes/no words |
| **OTP keypad-only**, HMAC-stored, lockout | [auth.py](../brain/app/services/auth.py), [api/auth.py](../brain/app/api/auth.py) | §2 |
| **Prompt injection** list checked before any LLM call | [guard.py:12-19](../brain/app/services/guard.py), used at [conversation.py:570](../brain/app/services/conversation.py) | "ignore your instructions", "system prompt", "I am the manager"… get a fixed reply. **Why in code:** the check can't itself be talked out of its rule, and it costs no LLM call. |
| **Fraud / risk** words force the fraud journey | [guard.py:22-34](../brain/app/services/guard.py) | Either the code or the LLM flagging risk is enough, so a fraud report is never treated as a plain question |
| **Documents with instruction-like text** flagged at upload | guard.py:35 (`_DOC_INSTRUCTION`) | Stops indirect injection hidden in a KB document |
| **No invented numbers**: each sentence is checked against its sources before it's spoken | [numbers.py:54](../brain/app/services/numbers.py), [conversation.py:1173-1179](../brain/app/services/conversation.py) | A wrong rate or fee is the most harmful thing a bank agent can say; the check makes it impossible to say an unsourced number |
| **Bank figures through templates**, never LLM wording | [personal.py](../brain/app/services/personal.py), ADR 0024 | Balances can't be misread or rounded by the model, and bank data (which could contain injected text in a transaction description) never reaches the LLM |
| **Refuse on weak evidence** | [conversation.py:966-967](../brain/app/services/conversation.py), `min_rerank_score = 0.03` ([config.py:50](../brain/app/config.py)) | No reliable source → "I don't have that", never a guess |
| **PII masking** before the LLM, logs and history (Luhn for cards, Verhoeff for Aadhaar, PAN, phone, email, UPI) | [privacy.py:42-98](../brain/app/services/privacy.py), [conversation.py:438](../brain/app/services/conversation.py) | Checksums tell a real card or Aadhaar number apart from an amount, so useful figures survive and identifiers don't |
| **Rate limit** per client (120/min); upload type and size checks; CORS allow-list | [main.py:43, 90](../brain/app/main.py), [config.py:113-115](../brain/app/config.py) | Basic abuse protection for any web service |
| **Bank-side customer check** | [mockbank/core.py:120-122](../brain/app/mockbank/core.py) | Even a broken brain can't read another customer |

### 3.2 Hard rules worth adding, and which ones are not worth it

**Application rules:**

| Rule | Why | Where | When |
|---|---|---|---|
| **Outgoing-speech filter** run on every sentence before TTS: never ask for OTP / PIN / CVV / password / card number; never give a web address not in the cited sources | The last line if the LLM is jailbroken. Asking for an OTP or giving out a fake link is exactly what fraudsters do; a bank agent must never do either. It's regex, takes microseconds and needs no new library. Unknown phone numbers and long digit runs were **already** blocked by the figure check (every number must be in a cited source). | **Done (11 Nov):** `guard.unsafe_output`, called in `_check_sentence` and on the clarify question ([conversation.py](../brain/app/services/conversation.py)); tests in `tests/test_security.py`; ADR 0006 note | **Done.** The single most valuable addition for the "what if guardrails fail" question. |
| **Per-mobile SMS limit** | §2.3: stops SMS flooding across sessions | auth.py `_send` | **Before go-live** |
| **Kill switches**: three config flags: actions off; all calls to people; LLM off | When something goes wrong, the bank can stop it in minutes without a code deploy. Banks expect this in incident plans. | config.py plus a check in the registry | **Before go-live** |
| **Abusive-caller rule** | I found no specific rule. Without one the agent keeps answering abuse politely forever, or the LLM reacts badly. | guard.py word list → a polite warning → transfer to a person | **Before go-live** (small) |
| Card actions per customer per day | Slows down a caller who has passed the OTP fraudulently | | **Not needed now.** Card actions are block/unblock and on/off settings, which are protective, not money-moving, and each needs an OTP plus a "yes". Revisit if money-moving actions are ever added. |
| Caps on turns, call length, tokens per call | Stops runaway loops | | **Not needed.** `max_tokens` already caps each LLM call, and the telephony layer caps call length. |
| A second AI that moderates every reply, or Bedrock Guardrails | Extra screening | | **Not needed.** It adds a network call (latency) and cost on every turn. The risky outputs (numbers, secrets, links) are already blocked by exact code checks, which are more reliable for these cases. |

**The real network firewall (infrastructure, before go-live):**
- **AWS WAF** in front of public endpoints (rate rules, bot control). **Why:** blocks floods and common web attacks before they reach our code.
- **Security groups and Kubernetes NetworkPolicies**, so the brain can talk only to Postgres, Redis, the Bedrock VPC endpoint, AU's API gateway and the SMS gateway (an **egress allow-list**). **Why:** if a server were ever compromised, it couldn't send data anywhere else.
- **Private subnets**, no public IPs on workers, and Direct Connect / VPN to AU's data centre. **Why:** nothing internal can be reached from the internet.

These are standard settings, not a project to build.

### 3.3 Is it needed?

Yes, but only the small set above.
- Banks and the RBI FREE-AI framework expect controls that don't depend on the model behaving.
- Most are already built. The outgoing-speech filter is the single most valuable addition: small, fast, and a direct answer to "what if the guardrails fail?".

---

## 4. Guardrails: what's implemented vs what voice agents need

The research (web) lists these as the main threats for a bank voice agent:
- direct prompt injection;
- **indirect** injection through data the agent reads (CRM notes, transaction descriptions, documents);
- impersonation and social engineering ("I'm from IT, skip verification");
- voice cloning;
- caller-ID spoofing;
- an agent with too much authority;
- PII leaks;
- data leaving the country;
- and the need for audit trails.

| Guardrail | Status | Why it's done this way | Where |
|---|---|---|---|
| Direct injection screen (code, before the LLM) | Done | Code can't be persuaded; no LLM cost | guard.py:12, conversation.py:570 |
| Indirect injection through bank data | **Strong by design** | Bank data never reaches the LLM, so text hidden in a transaction description has no model to fool | personal.py templates; store.py:40-44 |
| Indirect injection through the KB | Done | Documents are flagged at upload; passages are sent to the LLM labelled as data | guard.py:35; ANSWER_SYSTEM |
| Impersonation ("I'm staff") | Done | No spoken claim changes identity; only the OTP does | guard.py:18 |
| Voice cloning | Not relevant to our design | No voice biometrics; the OTP to the registered mobile is the factor, and a cloned voice can't receive the SMS | PHASE-4-PLAN §9.3 |
| Caller-ID spoofing | Handled | Caller ID only chooses where the SMS goes | auth.py:105-115 |
| Agent authority | Done | Permission table, confirmation, people-only list, bank-side token check | §3.1 |
| PII masking (input, logs, history, LLM, judge) | Done | Regex plus checksums: exact for Indian identifiers and needs no extra model | privacy.py |
| No invented facts | Done | Figure check, templates, refusal threshold | numbers.py, conversation.py |
| Fraud and risk handling | Done | Word lists plus LLM flags; either is enough | guard.py:22, journeys.py |
| Hand-off to a person | Done, with a summary | A caller must always be able to reach a human (bank expectation) | desk.py, summary.py |
| AI disclosure at call start | Done and checked after each call (`disclosure_said`) | RBI FREE-AI and fairness: customers must know they're talking to AI | review.py:160 |
| Audit trail | Done | Tamper-evident turn chain, auth_events, recordings: needed for complaints, disputes and the regulator | audit.py, store.py:98-110 |
| Outgoing-speech filter | **Done (11 Nov)** | §3.2 | guard.py `unsafe_output` |
| Red-team / adversarial test set | **Missing, before go-live** | RBI FREE-AI asks for adversarial testing (prompt injection, data-leak probes) with documented results. A fixed set also proves fixes stay fixed. | `evals/redteam.yaml` (about 40 attacks in English, Hindi and Hinglish), run with the existing smoke runner, so no new tool |
| Abusive caller | **Missing, before go-live** | §3.2 | |
| Per-mobile SMS limit | **Missing, before go-live** | §2.3 | |
| Data residency | **Not met in dev** | Groq (speech-to-text and LLM) and Edge TTS are outside India. Acceptable only because the data is dummy. | Production: Bedrock India profile, India-hosted STT/TTS (Sarvam) |
| Managed guardrail service (Bedrock Guardrails) | Not used: **not needed** | Its grounding check costs about $0.10 per 1,000 text units (web, unconfirmed) and adds a network call each turn. Our figure check already covers the most important part (numbers), exactly. | |

---

## 5. Evaluations: what, where, when, and why

### 5.1 Why there are several layers

The layers are ordered **from cheapest and fastest to most expensive**:
- Most mistakes are caught by free offline tests in a minute.
- The paid LLM runs are kept for what only a real model can show.
- Groq's free tier allows 1,000 requests a day per model, shared with the owner's own testing, so expensive runs must be rare.

Each layer also answers a different question, so one can't replace another.

| Layer | When it runs | What it measures | Why this layer exists | Files |
|---|---|---|---|---|
| **Offline unit and journey tests** | Every code change: `uv run pytest`, about 1.5 min, no Groq | Routing rules, scope, trimming, the figure check, masking (Luhn, Verhoeff), auth, card actions with confirmation, the HTTP bank, voice turn-taking, barge-in, the judge's quote checks | The code-decides parts (permissions, masking, figure check) must be **exactly** right every time; a fake LLM tests them for free and in seconds | [brain/tests/](../brain/tests/), about 25 files, e.g. `test_security.py`, `test_dialogue.py`, `test_kbmeta.py`, `test_voice_session.py`, `test_review.py` |
| **Smoke set** (real LLM, cached) | Before and after a change: `python -m evals.smoke` | Pass rate by failure class, routing, latency, real LLM requests made; a second run with nothing changed makes 0 requests | A quick real-model check that a prompt change didn't break common cases. Replay caching makes repeat runs free. | [evals/smoke.py](../brain/evals/smoke.py), [evals/smoke.yaml](../brain/evals/smoke.yaml), cache [adapters/replay.py](../brain/app/adapters/replay.py) |
| **Regression pack** (real LLM, cached) | After fixing call problems: `python -m evals.smoke --pack regression --keep` | 33 whole conversations from the PM's test plans; the report has full transcripts | Catches problems that show up only across several turns; `--keep` leaves the calls on /calls for review | [evals/regression.yaml](../brain/evals/regression.yaml), run by [evals/smoke.py](../brain/evals/smoke.py) |
| **Golden set** (RAG accuracy) | Occasionally, about 1 hour on the free tier | Pass rate, correct behaviour, **fact accuracy**, **retrieval hit@1/3/5 and MRR**, latency per step, **cost per question** | Retrieval is measured **separately** from the answer: if the right passage wasn't found (hit@k), it's a search problem; if it was found but the answer is wrong, it's a prompt problem. Fact accuracy matters most for a bank. | [evals/golden_run.py](../brain/evals/golden_run.py), [evals/golden/v1.yaml](../brain/evals/golden/v1.yaml) (105 questions: English 71, Hindi 17, Hinglish 17), [services/evals.py](../brain/app/services/evals.py) (prices line 32, cost line 56); compare two runs by their reports |
| **Every live turn** (§5.2) | Every turn, in real time | Retrieval scores, refusal gate, figure check, grounding level | Protects the caller **during** the call, not just afterwards | conversation.py, [audit.py:65](../brain/app/services/audit.py) |
| **After each call** | In the background when a call ends (no latency) | 10 code checks plus an LLM judge on 5 dimensions with verified quotes, findings, a fix list | Catches problems no single-turn check sees (repetition, lost context, a missed hand-off) without slowing the call | [services/review.py](../brain/app/services/review.py), UI `/calls` and `/fixes` |
| **Ops metrics** | Always | p50/p95/p99 per stage, outcomes, retries, fallbacks | Latency and failures are measured all the time, so slowdowns are seen early | `/v1/stats`, `/metrics` ([api/ops.py](../brain/app/api/ops.py), [metrics.py](../brain/app/metrics.py)) |

**Why the judge is built the way it is:**

| Choice | Why |
|---|---|
| Code checks run first, on every call | They're free and certain: "account data before verification" is a fact, not an opinion |
| The judge is a **different model** (gpt-oss-20b) from the agent (120b) | A model grading its own answers tends to approve them, and a separate model has its own quota |
| The judge must quote the transcript, and code verifies each quote | LLM judges sometimes invent evidence; unverifiable quotes are blanked |
| A failed hard rule is always "critical", whatever the judge says | The judge can't overrule a safety check |
| Findings name the source (agent, system failure, bank system, knowledge gap…) and the fix area | So a Groq outage isn't blamed on the agent, and engineers know where to look |
| `JUDGE_MODE` = all / selective / off | All calls in development; risk-weighted plus a sample in production, to control cost |
| Judge findings are only suggestions | The judge isn't calibrated yet; the owner decides what changes |

### 5.2 Is retrieval evaluated at the moment it happens?

**Partly, on purpose.** Each turn makes **deterministic checks** on retrieval, but there is **no LLM grader per turn**.

**Why no per-turn LLM grader:**
- It would add about a second to every answer and double the LLM cost.
- For a voice call, a second of silence feels long.
- The highest-risk failure (a wrong number) is already blocked exactly by code.
- The rest is caught after the call.

What does happen on every turn:
- Dense, keyword, RRF and reranker scores for every candidate are saved in the turn's trace (shown in the UI's "why"). **Why:** any answer can be explained later.
- **Gate:**
  - if the best reranked passage scores below `min_rerank_score` (0.03), the agent **refuses** instead of answering (conversation.py:967);
  - passages below half that are dropped (line 966);
  - then `kbmeta.trim_context` drops weak, duplicate or wrong-product passages ([kbmeta.py:128-180](../brain/app/services/kbmeta.py)).
  - **Why:** weak or wrong-product passages are the main cause of confident wrong answers (e.g. another card's forex rate).
- **Faithfulness for numbers:** every number spoken must appear in a cited passage, or the sentence is stopped (conversation.py:1179).
- **Grounding label** high / medium / low from the cited passage's reranker score and speech clarity (audit.py:65-85). It's for reviewers; it never changes the answer. **Why:** reviewers can look at low-grounding answers first.
- The **post-call judge** scores correctness after the call.

### 5.3 Latest numbers (from the tracker)

| What | Result | Date |
|---|---|---|
| Golden set (best clean run) | pass 72.4%, hit@3 91.1%, hit@5 93.7%, MRR 0.81, fact accuracy 78.1% | 3 Nov, **older prompt** |
| Routing (partial, real LLM) | 32/38 = 84% (stopped at the daily limit) | 5 Nov |
| Passage trimming | 0 evidence lost; 22–24% less text | 8 Nov |
| Router latency | about 1.5 s p50 on 120b (today's `/v1/stats`, 2 turns: too few to rely on) | 9 Nov |
| Retrieval | about 85 ms warm | Phase 3 |

These are **old or partial**. The golden set hasn't been re-run on the current prompt and router because of the Groq quota. Be ready to say so.

### 5.4 What's not done, and what should be

| Gap | What production systems do | Suggestion | When, and why |
|---|---|---|---|
| Golden set on the current prompt | | One clean golden run on Bedrock with the chosen model | **Before go-live.** The model will change from Groq to Bedrock anyway, so run it then; running it now on Groq would be redone. |
| End-to-end latency | "Time to first audio" per turn is the key voice metric | **Done (11 Nov).** Voice turns already recorded `first_audio_ms` (first sound, including the "one moment" filler) and `/v1/stats` reported it. `voice_to_voice_ms` (first audio of the **real answer**) is now reported too. **Why both:** the filler makes `first_audio_ms` look better than what the caller actually waits for. It needs voice test calls to fill in. | Done |
| Red team | Adversarial suite run before each release | §4 | **Before go-live** (RBI FREE-AI) |
| Human calibration | People label a sample; judge agreement is measured | Label about 30 calls in the existing calibration UI | **Before go-live.** Until then nobody knows how far to trust the judge. 0 labels so far. |
| Speech recognition accuracy | Word error rate on real **8 kHz phone audio**, by language | A small recorded test set | **Before go-live, when telephony arrives.** Phone audio is much harder than browser audio; there's nothing to test until Cisco is connected. |
| Online quality monitoring | Score a **5–10% sample** of live turns with reference-free judges and alert on drift (web) | Already mostly covered: `JUDGE_MODE=selective` judges risky calls plus a sample | **At scale**, only a weekly summary on `/calls`. A separate monitoring product (Langfuse, Arize) is **not needed**: the judge and stats already do this. |
| Voice behaviour report | Barge-in false triggers, interruptions, silence time-outs, transfer rate | The events are recorded; add counts to `/v1/stats` | **Before go-live** (small) |
| Regression gate in CI | CI blocks a merge if scores drop | Run the offline tests plus the smoke set in CI | **Before go-live**, once there's a CI pipeline |
| RAGAS or another eval library | | | **Not needed.** Our runner is exact for numbers, has no extra dependencies, and stores results next to the traces (ADR, minimal libraries). |

---

## 6. Conversation history and context

### 6.1 How it works today

**The LLM calls are stateless.** Every turn, the brain reads the state from Postgres, builds a small prompt and calls the model. The model remembers nothing between calls.

| What | Stored in | Read each turn? | Sent to the LLM? | Why | File |
|---|---|---|---|---|---|
| Last **6 turns** (answers cut to 300 characters; account answers replaced by "(account details were given)"; amounts in action turns hidden) | `traces` (one row per turn, also the audit record) | Yes, `history(session_id, 6)` | Router: all 6. Answer step: only the last turn (220 characters). | Enough to resolve "and for 2 years?" or "that card". More would cost tokens and dilute the prompt. Account data is hidden so it never goes back to the model. | [store.py:32-45](../brain/app/services/store.py), [conversation.py:394, 585-586](../brain/app/services/conversation.py), `history_turns` [config.py:51](../brain/app/config.py) |
| **Call facts** (senior citizen, product, tenure, amount, account) for the **whole call** | `sessions.facts` (JSON) | Yes | Yes (small) | Important facts must outlive the 6-turn window; a sticky note is cheaper than a long history | store.py:47-54 |
| **Call state**: open requests, done or declined tasks, focus card or account, the list being read, guided flow | `sessions.state` (JSON) | Yes | Focus as text | Things chat history can't carry reliably, like "the second one" or a request waiting behind a question | [state.py](../brain/app/services/state.py) |
| **Waiting question** ("Shall I block card 6712?") | `sessions.pending` | Yes | No: the yes/no is read in code | The confirmation must be read by code (§3) | store.py:56-65 |
| Verification | `sessions.customer_id`, `verified_at`, `auth` | Yes | Never | Identity is never the LLM's business | auth.py |
| Bank data | Not stored; cached in memory for 8 s per session | When verified | **Never** | We don't keep bank data we don't need (DPDP minimisation) | auth.py:46-65 |
| Full transcript | `traces` (hash-chained) | Only for audit | No | The permanent, tamper-evident record | store.py:98-110, audit.py |

**Why long calls don't get slower or costlier:**
- The window is fixed at 6 trimmed turns.
- Important facts sit in `facts` and `state` instead of in the history.
- A 40-turn call sends the same amount of history as a 6-turn call.
- Tested live: "I'm a senior citizen" in turn 1, eight unrelated questions, then the FD rate came back with the senior-citizen rate (PROJECT-GUIDE §10).

### 6.2 Tokens per turn (measured on the gpt-oss tokenizer, tracker)

| Part | Tokens |
|---|---|
| Router instructions | 2,068 |
| Router JSON schema | 1,352 raw; Groq reports about 3,000–3,190 prompt tokens per router call in total |
| History plus facts (router user message) | **10 to 140** |
| Answer instructions | 715 |
| Answer passages after trimming | about 767 (was 972) |

**History is under 5% of the input.** The cost is the router's fixed instructions and schema, which is exactly what **prompt caching** discounts (§7.4).

Output, an estimate: router JSON about 150–300 tokens plus gpt-oss reasoning tokens; answer about 100–250 tokens. Total about 300–700 a turn.

### 6.3 Latency of the state layer (estimate)

Each turn does about 10–12 small Postgres queries:
- history, facts, pending, state;
- the verification check and its update;
- saving the trace with a row lock;
- saving the state.

| Setting | Per query | Per turn |
|---|---|---|
| Same machine (today) | under 1 ms | about 5–10 ms |
| RDS in the same AZ | about 1–2 ms | **about 10–25 ms** |
| Redis (ElastiCache) instead | about 0.3–0.5 ms | about 4–6 ms |

A turn takes about 2–4 s to first audio:
- end-of-speech wait 0.7 s;
- speech-to-text 0.3–1.1 s;
- router 0.9–1.9 s;
- retrieval about 0.1 s;
- answer first token about 0.6 s;
- TTS first audio about 0.3 s.

**Redis would save about 10–20 ms, under 1% of the turn. So history doesn't need Redis.** Moving it there would add a second store to keep in step with the audit record, for a gain no caller could hear.

Reading the four session values in one query instead of four would save about 5 ms: **not needed**, too small to matter.

### 6.4 Why "stateless LLM + database" is the right method

It's the standard production pattern (web: "production agents store state in Postgres, not the model"). For a bank it has specific benefits:

| Reason | Why it matters here |
|---|---|
| **We choose what the model sees** | Account data and OTPs never go back to the LLM. A provider-held history would contain everything ever sent. |
| **One record** serves memory, audit, judge, hand-off and deletion | One place to secure, retain and erase; no copies drifting apart |
| **Model fallback works** | When the main model is rate-limited, [resilient.py](../brain/app/adapters/resilient.py) switches to another model mid-call. That only works because the state is ours; a provider's stored chain can't be continued by a different model. |
| **Portability** | Groq → Bedrock → self-hosted with no migration of conversations |
| **Any server can take the next turn** | Copies of the brain are interchangeable, which is what makes scaling simple (§8) |
| **Cost stays flat** | We send 6 trimmed turns, not the whole call (§7.3) |

---

## 7. AWS Bedrock: which API, why, and what it costs

### 7.1 The options

| API | Holds the conversation? | Notes |
|---|---|---|
| **Converse / ConverseStream** (and the older InvokeModel) | **No (stateless)**: you send the context each time | Works with **all** Bedrock models, including Claude; supports prompt caching and structured output through a forced tool call. **Our adapter already uses it** ([adapters/bedrock.py](../brain/app/adapters/bedrock.py)). |
| **Responses API** (OpenAI-compatible; bedrock-runtime and bedrock-mantle endpoints) | **Optional.** With `store=true` (the default) Bedrock keeps input and output for **30 days**, and you chain turns with `previous_response_id`. `store=false` keeps nothing. | Mainly OpenAI models (gpt-oss, GPT-5.6). Stored responses stay in the Region that processed them (web). |
| **Bedrock Agents** (`InvokeAgent` with `sessionId`) | Yes, a managed session | The LLM plans and calls tools itself |
| **Session management APIs** (`CreateSession`, `PutInvocationStep`) | Stores checkpoints you write | **Preview.** 1-hour idle time-out, data deleted after 30 days. It's storage only: you still build the prompt. |
| **AgentCore Memory** | A short- and long-term memory service | Priced per event or per GB; long-term extraction uses extra LLM calls (web) |

### 7.2 Recommendation and why

**Use Converse, stateless, with prompt caching. Keep Postgres as the record.**

Reasons, in order of importance:

1. **Cost.** The stored-chain option bills the whole call again every turn (§7.3). Our design sends only what's needed.
2. **Control over what the model sees.** We deliberately hide account data and amounts from history. A stored chain contains everything ever sent and generated, and the provider replays it.
3. **Model fallback.** A chain stored for model A can't be continued by model B. Our fallback (resilient.py) needs our own state.
4. **Two prompts per turn.** Each turn uses two different system prompts (router and answer). A single chained conversation doesn't fit that design; you'd need two chains per call or to rebuild the prompts anyway.
5. **Works with every model.** Converse supports Claude (available India-only) as well as gpt-oss. The Responses API is mainly for OpenAI models.
6. **Compliance** (§7.5): no extra copy of conversations held outside the bank's records.
7. **Already built**, with nothing new to approve.

**When the Responses API makes sense:**
- Only if the chosen model is an OpenAI one (gpt-oss, GPT-5.6) **and** we want to reuse our existing OpenAI-compatible client ([llm.py](../brain/app/adapters/llm.py)) instead of the Converse adapter.
- Then use it with **`store=false`**. That is still stateless; it's just a different request format.
- Either way the state stays in Postgres.

**Agents, session APIs and AgentCore Memory: not needed.**
- Agents let the LLM choose tools, which conflicts with "code decides" (ADR 0008) and our permission table.
- The session APIs and AgentCore Memory duplicate what `sessions` and `traces` already do. They'd be a second store to secure, pay for and keep in step.

### 7.3 The cost difference, worked out (estimate)

Both APIs charge the **same price per token** for the same model; I found no separate fee for the Responses API itself (web). The difference is **how many tokens are billed**.

- **Stateless (ours):** each router call sends about 3,000 fixed tokens plus up to 140 tokens of trimmed history. Every turn costs about the same, however long the call is.
- **Stored chain (`previous_response_id`):** OpenAI documents that all earlier input tokens in the chain are billed as input on each turn (web; I found no Bedrock statement that differs, so confirm with AWS). Each turn's billed input = instructions + **everything said so far, untrimmed**. It grows every turn.

Example: router calls only, 20-turn call, about 400 tokens added to the chain per turn (question + JSON + reasoning):

| | Turn 1 | Turn 10 | Turn 20 | **Whole call (input tokens)** |
|---|---|---|---|---|
| Stateless (ours) | about 3,100 | about 3,140 | about 3,140 | **about 63,000** |
| Stored chain | about 3,400 | about 7,000 | about 11,000 | **about 144,000 (about 2.3×)** |

- At gpt-oss-120b prices (about $0.15 per 1M input), that's about $0.009 vs $0.022 of router input per call. With Claude Haiku 4.5 (about $1 per 1M), about $0.06 vs $0.14.
- The longer the call, the bigger the gap: a 40-turn call is about 4× more.
- Prompt caching narrows the gap for both, because repeated beginnings are discounted. The stored chain still pays for the growing middle.
- **Why this matters for a bank:** long calls (complaints, fraud) are the ones that matter most, and the stored chain makes exactly those the most expensive.

### 7.4 Prompt caching: the real cost lever

- Cache **reads cost up to about 90% less** than normal input.
- Cache **writes cost about 1.25×**.
- The cache time-out resets on each hit; a 1-hour option exists on Converse (web: AWS docs and blog).

**Why it fits us:** the router sends the same about 3,000 tokens of instructions and schema on every turn of every call. That's the ideal case; caching could roughly halve LLM input cost (§8.5).

Check that the chosen model supports caching on Bedrock and its minimum cacheable size. Needed: **before go-live**. It's a request option, not a project.

### 7.5 Why a stored conversation at AWS is a DPDP and retention issue (exactly)

**It is not illegal for AWS to store it.**
- Under the DPDP Act, AU is the **data fiduciary**, the one responsible.
- AWS would be a **data processor** working under contract. Banks use processors all the time.

**The issue is that the stored chain is an extra copy of customer conversations that brings duties and no benefit:**

| DPDP / RBI duty | What a stored chain does to it |
|---|---|
| **Storage limitation**: keep personal data only as long as the purpose needs, then erase | AWS keeps every stored response for **30 days, set by AWS**, not by AU's retention policy. AU can't make it shorter by policy alone; each response would have to be deleted by API. |
| **Erasure** (a customer's right; our "delete call" feature) | Deleting a call in our database wouldn't delete AWS's copy. We'd have to track every response ID and delete each one, extra code and a new way to fail. |
| **Data inventory and breach duties** (72-hour breach reporting from May 2027; RBI outsourcing rules: know where data is and audit vendors) | One more place holding personal data to list, secure, audit and include in breach plans |
| **Data minimisation** | We deliberately keep bank data out of the model's context. A stored chain keeps everything the model saw and said, including reasoning. |
| **Localisation** | Stored responses stay in the Region that processed them (web). With the India profile that's Mumbai or Hyderabad, which is fine. With an `apac.` or global profile it could be outside India, which is not. |

**What should be done:** don't create the copy at all.
- Use stateless calls (Converse, or Responses with `store=false`).
- Keep our own record as the single source of truth, under AU's retention policy and our delete feature.

**Bedrock invocation logging** (off by default) is the same question in another form:
- It records prompts and responses to S3 or CloudWatch in **our** AWS account, under our retention.
- It can be set to leave out the text (`textDataDeliveryEnabled=false`) and keep only metadata such as token counts. **Verify** this in the AWS console.
- **Recommendation:** turn it on **without text**, and keep the text in our own traces. **Why:** we get AWS-side token and usage audit without a second copy of conversations. **Before go-live.**
- AWS states Bedrock does not use customer prompts to train models; confirm it in AWS's data-protection page for the contract.

### 7.6 Model, region and data residency

| Option | India-only processing | Price per 1M tokens, input / output (web, third-party, verify) |
|---|---|---|
| gpt-oss-120b on Bedrock | **Check:** the India in-country launch (Aug 2026) names GPT-5.6 Terra and Luna; gpt-oss in Mumbai not confirmed | about $0.15 / $0.60 |
| gpt-oss-20b on Bedrock | Same check | about $0.07 / $0.30 |
| Claude Haiku 4.5 | **Yes**, through the **India geo inference profile** (Mumbai + Hyderabad), from 29 Sep 2026 | about $1 / $5 |
| GPT-5.6 Luna | Yes (India profile) | about $0.20 / $1.20 (secondary source) |

- **How to choose:** pick the cheapest model that is **India-only** and passes our golden set and red-team set.
  - **Why:** the evals measure what matters here (facts, routing, refusals). A price list doesn't.
  - Switching model means re-running the evals, because the prompts were tuned on gpt-oss (strict JSON schema, figure check).
- Use the India profile, and allow both ap-south-1 and ap-south-2 in IAM/SCP. **Do not use `apac.` or global profiles** for customer data.

---

## 8. Scale: 100 to 1,000 calls at once

### 8.1 Assumptions (estimate) and what breaks first

- One turn about every 12 s per call; a 4-minute call is about 20 turns.
- About 1.4 LLM calls per turn: the router almost always; the answer model on about 40% of turns. Bank answers, small talk and yes/no use no answer-model call.
- About 4,000 input and about 560 output tokens per turn on average.
- About 12 database queries per turn.

| Calls at once | Turns/s | LLM requests/s | LLM input tokens/min | DB queries/s | Voice worker pods (50–100 calls each) |
|---|---|---|---|---|---|
| 100 | about 8 | about 12 | about 2M | about 100 | 2 (two for redundancy) |
| 300 | about 25 | about 35 | about 6M | about 300 | 3–6 |
| 800 | about 67 | about 93 | about 16M | about 800 | 8–16 |
| 1,000 | about 83 | about 117 | **about 20M** | about 1,000 | 10–20 |

**What breaks first, and what to do:**
1. **The LLM token-per-minute quota.**
   - Bedrock on-demand quotas per account and Region are far below 20M a minute by default.
   - **Do:** ask AWS for a quota increase early (it takes time), and use prompt caching.
   - **Not needed** unless AWS can't meet the quota: provisioned throughput or self-hosting (it costs a lot even when idle).
2. **Embed and rerank GPU** (about 35 knowledge-base turns a second at 1,000 calls).
   - 1–2 L4-class GPUs with batching are likely enough. **Do:** load-test it.
3. **Speech-to-text and text-to-speech vendor concurrency** (Sarvam limits). **Do:** ask the vendor for limits in the contract.
4. **Not the database.** 1,000 queries a second of small indexed reads and writes is light for RDS/Aurora Postgres.
   - Use RDS Proxy because of connection counts (each brain copy has a pool of 10, [db.py:81-84](../brain/app/db.py)).
   - **Why a proxy:** many pods × 10 connections can exceed what Postgres handles well; the proxy shares them.

**The target number of calls at once is unknown.** Ask AU for their peak-hour call volume and average call length. Everything above is illustrative until then.

### 8.2 State per call, and why each piece lives where it does

| State | Where (production) | Why there |
|---|---|---|
| Live audio, VAD, playback, turn-taking | **In memory on one voice worker** for the call's life | Real-time (every 20 ms); can't be shared or stored remotely. A call stays on one pod (sticky). |
| Which call is on which worker; live-call list for the desk bridge and the delete guard | **Redis** | Today `LIVE_SESSIONS` is an in-process dict ([voice/session.py:66](../brain/app/voice/session.py)). With several pods, the desk must find the pod holding the call. |
| OTP send counters, rate limits, LLM fallback switch, desk live updates (pub/sub), customer read cache | **Redis** (ElastiCache / Valkey, Multi-AZ, Mumbai) | Short-lived, shared by all pods, needs automatic expiry. This is exactly ADR 0021. |
| History, facts, call state, pending question, verification, audit chain, reviews, fix list, KB plus vectors | **Postgres** (one Multi-AZ RDS/Aurora cluster) | Must survive crashes and be auditable; latency is fine (§6.3) |
| Recordings | **S3** (SSE-KMS, Object Lock, lifecycle, retention set by AU) | Large files don't belong in a database. Object Lock makes them tamper-proof for disputes. Today: encrypted files on local disk ([recordings.py:91](../brain/app/services/recordings.py)); uploads already support S3 ([adapters/storage.py](../brain/app/adapters/storage.py)). |
| Background jobs (ingest, judge, retention purge) | Postgres `jobs` table (`FOR UPDATE SKIP LOCKED`, already safe with many workers, [worker.py](../brain/app/services/worker.py)) | Enough for our volumes |

**A second Postgres or Redis?**
- Not a second Postgres. One HA cluster is enough.
- **Why not a second one for history:** splitting history from the audit record means two stores to keep in step and secure, with no speed gain.
- **Redis:** yes, **at scale**, only for the in-memory pieces above.

**Not needed:**

| Item | Why not |
|---|---|
| A read replica | Add only if dashboards or the judge slow the main database |
| Kafka or Redis Streams | Add only if the Postgres job table becomes a bottleneck; ADR 0021 says use AU's Kafka if they have one, never run our own |

### 8.3 Serverless, servers, or mixed?

| Part | Serverless (Lambda)? | Why |
|---|---|---|
| Voice workers | **No** | Audio streams last minutes, Lambda stops at 15 minutes, and cold starts add delay mid-call |
| Brain API | **No** | Loads about 2 GB of models and takes about 1 minute to start; needs a GPU |
| Embed / rerank | **No** (GPU containers) | GPUs and warm models |
| Database access through Lambda | **No** | It adds a network hop (tens of ms) and floods Postgres with connections; the brain talks to RDS directly through RDS Proxy |
| Post-call judge, recording upload and retention purge, nightly evals, reports | **Yes, fine** | Event-driven, bursty, not latency-sensitive; you pay only when they run |
| Test UI | Static hosting or a small container | |

So: **mixed.** Containers for the real-time path, serverless only for background jobs. Using Lambda for the background jobs is optional; running them as a small worker container is just as good. Choose whichever AU's platform team prefers.

### 8.4 Code changes needed before running more than one copy (at scale)

| # | Change | Why | Where |
|---|---|---|---|
| 1 | Shared OTP secret (Secrets Manager / KMS) | Codes must verify on any copy and survive restarts | auth.py:30 |
| 2 | Shared bank-token secret and service token (required, not random) | Same reason for bank calls | config.py:96-97 |
| 3 | Live-session map in Redis | The desk and the delete guard must find a call on any pod | voice/session.py:66, api/voice.py:196 |
| 4 | Rate limiter in Redis | Otherwise each copy has its own limit, multiplying it | main.py:90 |
| 5 | Recording key from KMS; recordings to S3 | Every copy needs the same key; local disks are lost when pods move | config.py:86, recordings.py:91 |
| 6 | pgserver → RDS (`DATABASE_URL`, already supported) | Managed, Multi-AZ, backed up | db.py |
| 7 | (Optional, at high volume) embed/rerank as their own GPU service | So brain copies can run on cheap CPU nodes and the GPUs are shared | adapters/models.py |

The ingest worker and the stateless conversation core are already safe to run as several copies.

### 8.5 LLM cost (estimate, LLM only)

Per turn about 4,000 input and 560 output tokens; per 4-minute call 20 turns.

| Model | Per turn | Per call | Per call with caching | 1,000 calls at once for 1 hour (about 15,000 calls) |
|---|---|---|---|---|
| gpt-oss-120b (about $0.15 / $0.60) | about $0.0009 | **about $0.019** | about $0.011 | about $280 (about $160 cached) |
| Claude Haiku 4.5 (about $1 / $5) | about $0.0068 | **about $0.14** | about $0.08 | about $2,000 (about $1,200 cached) |
| Post-call judge on gpt-oss-20b (about 3,500 in, 800 out) | | about $0.0005 per judged call | | |

Not included:
- speech-to-text and text-to-speech (vendor per-minute pricing; likely **larger** than the LLM cost);
- SMS;
- AWS infrastructure.

gpt-oss reasoning tokens can raise output. **Before choosing a model, measure real token counts on Bedrock.** Estimates can be off by 2×. Our adapter already reads `inputTokens` / `outputTokens` ([bedrock.py:50, 89](../brain/app/adapters/bedrock.py)), and the eval runner reports cost per question.

---

## 9. Deployment on AWS

### 9.1 Services to containerise (one Docker image each)

| Service | Kind | Scales on | Why separate |
|---|---|---|---|
| **voice-worker** (`voice/`, WebSocket or SIP media) | Stateful per call, CPU (Silero VAD) | **Active calls per pod** (custom metric) | It holds live calls, so it scales and drains differently: it must never be stopped mid-call (long termination grace period, pod disruption budget) |
| **brain-api** (FastAPI, `services/`) | Stateless, CPU (+GPU if models stay inside) | CPU and latency | Stateless, so it can scale freely. Today voice and brain run in one process; production splits them (PHASE-4-PLAN §13). |
| **embed-rerank** | GPU (L4 class) | Queue depth and latency | GPUs are expensive; one shared service uses them fully. **At scale only**; until then the models can stay inside the brain. |
| **ingest-worker** (`worker.py`) | GPU during ingest | Jobs table | Document uploads shouldn't slow live calls |
| **review-worker** (judge) | CPU, async | Queue | Background, so calls are never slowed; or Lambda |
| **desk and console UI** (Next.js) | Stateless | Requests | Behind SSO |
| **mock bank** | Dev and test only | | Replaced by AU's API gateway |

External: Bedrock (VPC interface endpoint), the STT/TTS vendor (India-hosted), the SMS gateway (DLT-registered templates in India), Cisco CUBE → SIP media gateway (production item).

### 9.2 AWS checklist

| Area | Service | Why |
|---|---|---|
| Region | Mumbai ap-south-1, 2 availability zones; DR in Hyderabad ap-south-2 | Data stays in India; two zones survive a data-centre failure |
| Network | VPC with private subnets; **VPC endpoints** for Bedrock, S3, Secrets Manager, ECR; Direct Connect / VPN to AU | Traffic never crosses the public internet |
| Compute | **EKS** (Karpenter: CPU node pools plus a GPU pool) | Kubernetes is the production plan, and it handles GPU scheduling, rolling deploys and per-service scaling. ECS Fargate is simpler but has no GPUs; plain EC2 means scaling by hand. |
| Load balancing | **ALB** for HTTPS and WebSocket (sticky per connection); **NLB** for SIP/RTP; WAF on the ALB | WebSockets need stickiness; SIP needs layer-4 balancing |
| Data | RDS / Aurora PostgreSQL with pgvector, Multi-AZ, KMS-encrypted; RDS Proxy | Managed backups and failover; pgvector keeps the KB in the same database (ADR 0003) |
| Cache | ElastiCache (Valkey / Redis) Multi-AZ, TLS (at scale) | §8.2 |
| Storage | S3 with SSE-KMS, Object Lock, lifecycle | Recordings |
| Secrets | Secrets Manager, KMS | §2.3 |
| Images | ECR with image scanning | Bank security teams require scanned images |
| Identity | IAM roles per service (EKS Pod Identity); SSO for staff | No long-lived keys in containers |
| Observability | CloudWatch, Managed Prometheus and Grafana, OpenTelemetry traces per call; CloudTrail; GuardDuty | Find slow or failing steps per call; detect attacks |
| CI/CD | Build → scan → offline tests and smoke set → canary at 1–5% of calls | A bad change reaches only a few callers before it's caught |

Check whether AU wants this in **their** AWS account, on-premises, or both. It changes who runs what.

---

## 10. Other questions to be ready for

| Question | Short answer |
|---|---|
| Where does data go today? | Dev only: **Groq (US) for speech-to-text and the LLM, Microsoft Edge for TTS**, with dummy data. Production: India-only (Bedrock India profile, India-hosted STT/TTS). **Why it's fine now:** the data is synthetic. A full data-flow inventory is needed before go-live (PHASE-4-PLAN §13). |
| DPDP Act | Rules notified Nov 2025. Main duties apply from **13 May 2027**; breach reports to the Board within 72 hours (web). Needs: notice and consent wording at call start (recording and AI), purpose limits, a retention schedule, erasure. "Delete call" exists ([maintenance.py](../brain/app/services/maintenance.py)); retention for recordings exists (`recording_retention_days`); **retention for traces doesn't yet**. Before go-live: a scheduled purge using AU's retention period. |
| RBI FREE-AI (Aug 2025) | Advisory framework: AI inventory, risk-tiered audits, **adversarial testing**, disclosure to customers, vendor clauses; the deploying bank stays accountable (web). We have disclosure, the audit trail, evals and the judge; the red-team set is missing (§4). |
| CERT-In | Report cyber incidents within 6 hours; keep logs for 180 days in India. Check AU's policy and set log retention to match. |
| Pen test / VAPT | Banks require a CERT-In empanelled audit before go-live. Plan time for it. |
| Vendor / outsourcing | RBI outsourcing rules apply to the LLM and speech vendors: contracts, audit rights, data location. |
| What if the AI is down? | "Fail to human": on provider trouble the agent says "I'm having trouble" and transfers. Production: the IVR routes straight to queues if the AI tier is unhealthy. A fallback model already exists ([adapters/resilient.py](../brain/app/adapters/resilient.py)). |
| Why not LangChain or Bedrock Agents? | Fewer libraries for the bank to approve (ADR 0002). Code must own permissions (ADR 0008). Full control of what the model sees. |
| Recordings | AES-256-GCM, stereo, SHA-256 checked, keypad codes never in them ([recordings.py](../brain/app/services/recordings.py)). Production: S3 plus KMS plus Object Lock. |
| Tamper evidence | Each turn is hash-chained to the previous one; the record shows "intact" or the broken turn ([audit.py](../brain/app/services/audit.py)). **Why:** in a dispute, the bank can show the transcript wasn't edited afterwards. |
| SIM swap | Ask AU whether their systems expose the last SIM-change date (§2.3). |
| How many calls at once is the target? | **Unknown: ask AU** (peak-hour calls, average length). The sizing in §8 is illustrative. |

---

## Sources

**Bedrock APIs and pricing**
- [Bedrock Responses API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-responses-api.html)
- [Bedrock session management APIs](https://docs.aws.amazon.com/bedrock/latest/userguide/sessions.html)
- [CreateSession](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_CreateSession.html)
- [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [AgentCore pricing analysis](https://www.factualminds.com/blog/amazon-bedrock-agentcore-pricing-12-components/)
- [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- [Prompt caching blog](https://aws.amazon.com/blogs/machine-learning/optimizing-cost-and-latency-with-amazon-bedrock-prompt-caching/)
- [Responses API: history billed each turn (OpenAI community)](https://community.openai.com/t/responses-api-question-about-managing-conversation-state-with-previous-response-id/1141633)
- [Responses API multi-turn billing](https://docs.routeway.ai/core-concepts/responses/multi-turn)
- [Bedrock price aggregator](https://pricepertoken.com/endpoints/bedrock)
- [Running Claude on Bedrock: cost](https://builder.aws.com/content/3JngQAbViYdfqJLY7kBdnAYOLtB/running-claude-on-amazon-bedrock-what-it-costs-vs-the-gpt-alternative-on-azure)
- [Bedrock Guardrails pricing guide](https://www.cipherprojects.com/blog/posts/bedrock-guardrails-production-guide-2026/)
- [Bedrock invocation logging](https://aws-samples.github.io/cloud-operations-best-practices/docs/recipes/AWS%20CloudTrail/monitoring_and_auditing_ai_workloads)

**India residency**
- [Claude India in-country inference](https://aws-news.com/article/2026-09-30-amazon-bedrock-expands-claude-model-availability-to-in-country-inferencing-in-india)
- [OpenAI models in India on Bedrock](https://aws.amazon.com/blogs/machine-learning/introducing-openai-models-on-amazon-bedrock-for-in-country-inferencing-in-india/)
- [Regional availability](https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html)

**Row-level security**
- [Common RLS footguns](https://www.bytebase.com/blog/postgres-row-level-security-footguns.md)
- [RLS in practice](https://telemetry-landing.pages.dev/blog/postgres-row-level-security-in-practice/)

**Voice security and guardrails**
- [ABA: voice fraud threat](https://bankingjournal.aba.com/2026/08/the-voice-fraud-threat-to-banking/)
- [Voice AI injection and jailbreaks](https://caller.digital/blog/voice-ai-security-prompt-injection-jailbreak-2026)
- [IVR to voice AI security](https://dev.to/rootlenses/from-ivr-to-voice-ai-security-challenges-developers-must-solve-in-banking-fap)

**Regulation**
- [RBI FREE-AI summary](https://www.scrut.io/post/rbi-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence)
- [DPDP Rules 2025 (PIB)](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf)
- [DPDP and Indian banks](https://www.bankingfinance.in/the-dpdp-act-and-indian-banks-what-must-change-before-may-2027.html)

**State and evaluation**
- [State for agents: Redis vs Postgres](https://www.sitepoint.com/state-management-for-long-running-agents-redis-vs-postgres/)
- [Why production agents store state in Postgres](https://dev.to/theautomate/why-production-agents-store-state-in-postgres-not-the-model-2jdg)
- [RAG evaluation in production](https://www.respan.ai/articles/rag-evaluation)
- [RAG evaluation metrics](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026)

Prices and model availability are from third-party pages unless they link to aws.amazon.com. The Responses API billing comes from OpenAI's documentation; Bedrock's behaviour is assumed to match and should be confirmed with AWS. Confirm prices on the official AWS pricing page for ap-south-1 before quoting them.
