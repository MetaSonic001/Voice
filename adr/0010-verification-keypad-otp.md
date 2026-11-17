# ADR-0010: Caller verification: registered mobile + keypad code

- **Status:** Accepted
- **Date:** 2026-11-04; tightened 2026-11-06
- **Deciders:** project owner

## Context

Personal data and actions need the caller verified. The problem statement allows a simulated method. Spoken codes would pass through speech recognition, the LLM and logs. One test call stored a code that was typed into the chat box.

## Decision

- Identify the caller by **registered mobile number** (caller ID, or spoken and matched).
- Send a **6-digit code** (simulated SMS). The caller enters it on the **keypad**.
- **Codes never go through speech-to-text, the LLM, history, transcripts or logs.**
  - Input goes to verification only when a code step is active and the input is exactly the expected format. Anything else is a normal turn.
  - A code heard by speech recognition is refused ("please use the keypad").
  - Stored as `[code]`; never read back.
- Expiry, attempt limits (3) and resend limits (3) apply.
- Verification is **authentication, not blanket permission**. Each task's access level ([0016](0016-card-actions-confirmation-access-levels.md)) decides what's allowed.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Mobile + keypad code** | Secrets never touch AI components; familiar to callers | Needs a keypad / DTMF path |
| Spoken code | Easier for callers | Secret goes through STT, the LLM and transcripts |
| Security questions (DOB, etc.) | No SMS | Weak; data leaks into transcripts |
| Voice biometrics | Smooth | Consent, spoofing and compliance work; a separate capability |

## Consequences

- **Easier:** a clean security story; a hard rule that's simple to test.
- **Harder:** production needs DTMF capture from the phone platform (Cisco) and the bank's real OTP service.
- **Revisit when:** AU defines its approved verification (and any step-up for higher-risk actions).

## Links

`brain/app/services/auth.py`, `privacy.py`, `PHASE-4-PLAN.md` §9.2.

## Implementation notes (6 Nov 2026)

- Built: while a code is pending, a lone six-digit chat message is the code, verified in code and stored as `[code]`. A code that came through speech recognition is refused ("use the keypad"). Open tickets from the last 30 days are only *asked about* after verification (kind and topic, no details).
