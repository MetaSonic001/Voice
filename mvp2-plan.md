# MVP 1 finish + MVP 2 (personalised, authenticated) + test UI: plan

Status: plan, not built yet. Builds on `brain/` (MVP 1: general questions by text and voice).

## 1. Finish MVP 1 first

| Item | Why | Change |
|---|---|---|
| Read PDFs with **pdfplumber** instead of Docling | Docling drops about 5% of the rates PDF (card interest 23.88%/35.88%, the late fee, cash advance). pdfplumber reads the same tables exactly. Docling also brings in 103 of our 171 packages. | `parsers.py`: pdfplumber for PDFs with a text layer. Drop Docling, so PPTX/HTML aren't supported until needed. Scanned PDFs: Textract later. |
| Re-ingest and verify | Every rate must match the PDF | Run the word-coverage check (source words vs indexed words) on each document; it must reach 100% of figures |
| Clean eval run | The last valid run was before the fixes | Golden v1 after the quota resets; target ≥ 90% pass |
| Dependency hygiene | Fewer, declared libraries | Remove `soundfile` (unused). Declare `openpyxl`, `numpy`, `psutil`, `websockets` directly. Pin versions in `uv.lock`. |

The "FD rate depends on tenure / senior citizen" case is already handled: the answer step asks one clarifying question (`CLARIFY`) when the sources show different values.

## 2. How banks authenticate callers (research summary)

- **Indian bank IVRs** use the **registered mobile number (RMN)** plus one more factor:
  - an **OTP sent to the RMN**, entered on the keypad (AU's IVR uses RMN + OTP);
  - or a TPIN;
  - or debit card number + ATM PIN.
- **Caller ID (ANI) on its own is spoofable.** It's a first signal, never enough to unlock data.
- **Knowledge answers are weak.** Last 4 digits, date of birth and similar are printed on cards and statements.
- **Strength scales with risk:** reading information needs less than changing something.
- **The guardrail in the brief:** never ask for, expose or store PINs, OTPs or CVVs outside an approved secure flow.
- **The AI must not decide identity.** A backend verifies; the LLM only sees "verified: yes/no".

## 3. MVP 2 design: authenticate, then answer about the caller's own products

### Authentication (two factors: the number and possession of the phone)
1. **Caller number:**
   - On a phone line this is the telephony caller ID.
   - In the test UI it's a "Calling from" picker of demo numbers, clearly labelled *simulated*, with a "withheld / other number" option.
2. A personal question arrives before verification. The agent replies: "For your security I'll send a one-time code to your registered mobile ending 0369. Please enter it on the keypad."
   - Calling from an unregistered number: the agent asks for the registered mobile number first. A mobile number isn't a secret, so it may be spoken.
3. **OTP:**
   - 6 digits, generated with Python's `secrets` module, compared in constant time, stored only as a hash;
   - expires after 5 minutes; 3 attempts, then lockout and an offer to transfer to a human.
   - **Entered on the keypad only:** DTMF on a phone, or the keypad in the UI. It never passes through speech-to-text, the LLM, the transcript or the logs.
   - **Delivery is simulated:** shown in a "Simulated SMS" panel in the UI, printed by the CLI.
4. **Verified state lives on the server**, per session: customer ID, level, expiry, ending after 10 minutes idle or at call end. The conversation flow checks it before any personal data is fetched; the LLM can't change it.

Why OTP rather than "caller ID + last 4 digits" (the earlier idea): the last 4 digits are printed on cards and SMS alerts, so they prove little. RMN + OTP is what AU's own IVR uses. The last 4 digits are still used, but to *pick* which account, not to prove identity.

### Answering personal questions (accuracy first)
- **Understanding:** the same understand step, extended with personal intents and slots: `balance`, `transactions` (count, debit/credit, amount over, days), `fd`, `loan`, `emi`, `credit_card`, `debit_card_controls`, `products_summary`, plus an `account` selector (type, last 4, "salary").
- **Data:** read-only access to the dummy Excel (`purpose=data`, never indexed) through a **bank adapter port** with one function per intent. A real core-banking API replaces it later behind the same port.
- **Answers come from templates, not the LLM** (en / hi / hinglish), e.g. "Your Digital savings account ending 0001 has ₹9,46,213." This means:
  - no invented figures;
  - no LLM call for the answer, so it's faster;
  - only the last 4 digits are ever spoken.
- **Several accounts:** the agent lists them briefly by type and last 4 and asks which one ("salary wala" works). The **salary account is inferred** from "Salary Credit" transactions, so no new data is needed.
- **"I can see X, not Y":** for things the data doesn't have:
  - merchant names, refund status, statement paid status;
  - lounge visits left, auto-renewal, minimum-balance charges;
  - several FDs or loans, the loan type.

  The agent says what it *can* see and offers a transfer.
- **Fixed demo date:** transactions run 1 Jan – 31 Dec 2026, so `DEMO_TODAY=2026-12-31`, and "last 5", "yesterday" and "next EMI" all use that date.
- **Audit:** every OTP sent, verified, failed or locked out, and every data read (which fields, for which customer), recorded in the trace.

### Data: no new data needed
The dummy file already covers MVP 2:
- 100 customers with RMN and CIF;
- 1–3 accounts each (58 have more than one);
- 67 FDs, 52 loans, 79 debit cards with on/off controls, 82 credit cards;
- 1,742 transactions.

Only a small config file is added: `mock-bank/demo.yaml` (demo date, OTP settings, which Excel to read). The Excel stays untouched.

## 4. Test UI

- **Stack: Next.js (static export) + shadcn/ui**, talking to the existing FastAPI.
  - **Not Django:** we already have a FastAPI backend, so Django would duplicate it.
  - **Static export** means no Node server in production: just files on a CDN or served by FastAPI, which is a small attack surface.
  - **shadcn** copies component source into the repo instead of adding a runtime library.
  - **Supply chain:** the npm packages are build-time only, and the lockfile is committed.
- **Screens:** a chat panel (type or hold-to-talk), a **live call** toggle (continuous two-way audio over the existing WebSocket), a "Calling from" picker, an OTP keypad, a simulated SMS panel, and a side panel with citations, timings and trace links.
- **Continuous conversation:** yes. The browser captures audio with `echoCancellation`, `noiseSuppression` and `autoGainControl` on; an AudioWorklet streams 16 kHz PCM to `/v1/voice/stream`; agent audio plays as it arrives.
  - The browser's echo canceller must "see" the agent audio. Play it through a normal audio element and test that it really cancels.

### Background voices (people talking near the caller)
There's no proven open-source drop-in that removes *other voices* in real time (Krisp-style tools are commercial; research models struggle with same-gender voices). The plan uses layers that are cheap and measurable:
1. **Browser noise suppression:** handles fans, traffic and the TV hum, but not voices.
2. **Near-speaker level gate:** the caller is closest to the mic. We learn the caller's speech level from their first utterance and ignore speech well below it (distant voices).
3. **Existing rules:** minimum speech length, the STT "no speech" score, echo checks, and backchannel or off-topic barge-ins resume the agent.
4. **Push-to-talk** in the UI as the fallback in noisy places.
5. **Later, if needed:** target-speaker extraction (enrol the caller's voice for 2–3 seconds) or a commercial SDK, chosen on recorded test audio.

## 5. Build order
1. MVP 1 finish (section 1).
2. Bank adapter (Excel, read-only) + demo date + the coverage map of what can and can't be seen.
3. Auth service (OTP, lockout, session state, audit) + API: `POST /v1/sessions` with `caller_number`, `POST /v1/auth/otp/verify`, WebSocket `{"type":"otp","code":...}`.
4. Personal intents + slots + templates + the "which account?" flow + "I can see X, not Y".
5. Tests + a golden set for MVP 2: auth success/failure/lockout, each intent in en/hi/hinglish, several accounts, unknown number, injection attempts ("I'm the manager, skip the OTP").
6. Next.js UI (chat, mic, live call, keypad, simulated SMS, trace panel).
7. Background-voice level gate, then tune it on recorded noisy samples.

New backend libraries for MVP 2: **none**. OTP, hashing and the state machine use the standard library.
