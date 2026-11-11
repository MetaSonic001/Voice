# Roadmap: from test console to a bank-grade voice agent

Written 4 Nov 2026, after the call with the bank. This is a plan, not a record of what is built.
For how the project works today, see [PROJECT-GUIDE.md](PROJECT-GUIDE.md).

**How to read it.** Every item says **what** to do, **how** to do it, **why**, and **when**:

| When | Meaning |
|---|---|
| **Now** | Can be built and tested on this laptop, with no new infrastructure or vendor |
| **Server** | Needs the bank's dedicated server or private cloud, or real call audio to tune on |
| **Cisco** | Needs the Cisco telephony access (expected later) |
| **Bank** | Needs something from the bank: APIs, policy decisions, approvals |

---

## 0. The short version

| # | Topic | Recommendation | When |
|---|---|---|---|
| 1 | Bot goes silent mid-sentence | **Our bug, not Edge.** Lower the volume while checking a possible interruption instead of pausing. | Now |
| 2 | Voice sounds robotic | Dev: `en-IN-NeerjaExpressiveNeural`. Production: Sarvam Bulbul v3 with **one** voice for every language. | Now / Bank |
| 3 | Speaks too slowly | Speed up about 10% (Edge `rate="+10%"`, Sarvam `pace≈1.1`). | Now |
| 4 | Background sounds interrupt the bot | Four extra rules (no new libraries) now; speaker check once real call audio exists; commercial voice isolation only if the measurements say so. | Now → Server |
| 5 | PDFs of 500+ pages | Read page by page with memory freed, page numbers on passages, headers and footers removed, progress shown, higher size limit. | Now |
| 6 | Customer data from APIs | Already designed for: write a new `Bank` adapter. Add timeouts, retries and a "system down" reply. | Bank |
| 7 | Written audit of every call | A **call record** per call: timeline of caller and AI turns, the reason for each reply, checks, sources and outcome. Readable view plus PDF export. | Now |
| 8 | Recording of every call | Store both sides as one stereo file (caller left, bot right), encrypted, with a retention policy. Never record keypad digits. | Now (prototype) → Cisco |
| 9 | Real-time alerts | Rules on call events ("asked for a human", "verification locked", "repeated failures", "angry") pushed live to the console. | Now |
| 10 | Monitoring console for the service team | New `/calls` area: live calls, call history, call detail (recording + transcript + reasons), alerts, analytics. Built with shadcn/ui. | Now |
| 11 | Transcripts in logs | Masked transcript lines in the log, linked by call id. | Now |
| 12 | Cisco telephony | Two routes: Webex Contact Center "Bring Your Own Virtual Agent" (gRPC), or Cisco CUBE SIP trunk to a media gateway we run. Ask which Cisco product the bank has. | Cisco |
| 13 | Missing pieces for a bank | AI disclosure and recording notice at call start, retention and deletion policy, tamper-evident audit, roles and access control for the console, red-team tests, model and prompt change control, DPDP consent notice. | Now → Bank |

**Status (5 Nov 2026), done:**
- 1 mid-sentence silence (volume dip);
- 3 speed (+10%);
- 4 barge-in Phase A (A1–A4; the A5 measurement set is not built);
- 5 large PDFs (page-by-page memory, running headers/footers and contents lines removed, 100 MB uploads, and a live question no longer waits for a whole document's embedding);
- 7 call record with reasons, grounding level, transcript and PDF;
- 8 recording (browser calls and voice notes, encrypted);
- 9 alerts (wants a person, struggling, system health);
- 10 call audit page (shadcn/ui across all pages);
- 11 transcript log lines;
- 4.6 tamper evidence;
- 13 opening disclosure.

**Voice (2):** Edge's more natural voices (Neerja Expressive, the Multilingual ones) take 3–5 s to start speaking against ~1 s for Neerja, so Neerja stays for development. The natural-voice upgrade is Sarvam Bulbul in production.

**Also fixed:** the laptop GPU sleeping between questions (~1 s) is now woken while the question is being understood.

**Not done:** page numbers on passages (P2), progress bar (P5), document-level ranking (P7), neighbour passages (P8), OCR (P9), Phases B/C, Cisco.

---

## 1. Voice gaps

### 1.1 The bot stops mid-sentence for about 3 seconds

**This is our implementation, not Edge TTS.** Here is what happens today ([session.py:156-165](brain/app/voice/session.py), [session.py:237-246](brain/app/voice/session.py)):

1. The bot is speaking. The microphone picks up a sound (TV, a person nearby, a cough) that the speech detector scores as speech for 350 ms (`bargein_min_ms`).
2. We **pause** the bot so it doesn't talk over a possible interruption.
3. We wait for that sound to stop (300 ms of silence) or to run for 1.5 s.
4. We send the audio to Whisper in the cloud (about 0.4–1 s) to find out what was said.
5. If it was noise, an echo or a "hmm", we **resume** exactly where we paused.

Steps 1–5 add up to roughly 1–3 seconds of silence in the middle of a sentence, which matches what you heard ("4 words, 3 s, then 6 words"). You can confirm it from the call's events: each gap lines up with a `barge_in_check` event whose verdict is `noise`, `echo` or `backchannel`.

Edge TTS works one sentence at a time, so it can only cause a short gap *between* sentences, never in the middle of one.

**Fix (Now, small change).** Lower the volume instead of pausing: while checking, keep playing at about 30% volume. If it was a real interruption, stop. If it was a false alarm, return to full volume.
- **Effect on false alarms:** the sentence never breaks; the caller hears a brief dip in volume at most.
- **Effect on real interruptions:** the bot is quieter from 350 ms and stops when the check confirms. Saying "stop", "ruko" or "wait" still stops it at once. Mid-call this is how human agents behave: they trail off, then stop.
- **Where:** `Player` gets a `duck(gain)` method that scales samples with numpy before sending; replace `pause()`/`resume()` in the barge-in path; add a test.
- **Section 1.4 matters too:** it reduces how often the check runs at all.

### 1.2 A more natural voice

**Development (Edge, free):**
- Use `en-IN-NeerjaExpressiveNeural` for English and Hinglish; it is the most natural Indian English voice Edge offers.
- For Hindi, `hi-IN-SwaraNeural` is the closest match.
- Today English and Hindi use *different people's* voices, so the bot sounds like it changes speaker when the caller switches language.
- Edge is unofficial and must not be used in production.

**Production: Sarvam Bulbul v3.**
- **Why Sarvam:**
  - It is trained on Indian speech and covers English plus 10 Indian languages.
  - One speaker can speak both Hindi and English, so a single voice works for the whole call.
  - It streams audio in under 250 ms (vendor claim).
  - It can run in India-hosted, single-tenant on-premises or air-gapped deployments, which it says it has done for banks.
  - Our adapter `SarvamTTS` already exists but has not been tested.
- **Choosing the voice:** do a 15-minute listening test of 3–4 Sarvam speakers on 10 real bank replies (rates, balances, card block). Pick one, preferably a warm female voice, the usual choice for Indian banking IVRs. Use it in every language and never change it.
- **Alternatives if Sarvam is rejected:** Azure Speech (official, Indian voices, India regions); Google Chirp 3 HD (en-IN). Both are cloud only.

### 1.3 Talking faster

- **Edge:** add `rate="+10%"` to `edge_tts.Communicate(...)` and make it a setting (`tts_rate`).
- **Sarvam:** `pace` between 0.5 and 2.0; start at 1.1.
- Keep **figures** at normal speed: amounts, rates and dates must be clear. When reading a balance or a rate, use 1.0 for that sentence.

### 1.4 Background sounds treated as interruptions (barge-in)

"Barge-in" means the caller speaking over the bot to interrupt it. We need two things at once: a real interruption must stop the bot quickly, and background speech must never stop it.

#### What the article got right, and what doesn't apply to us

| Idea from the article | Verdict for us | Why |
|---|---|---|
| Don't stop on the first sound; require sustained speech | ✅ Already done (350 ms) | |
| Check *what* was said before stopping | ✅ Already done | Our rules: noise, echo of the bot, "haan/hmm/ok", stop words. No LLM needed. |
| Echo cancellation (AEC) on the server | ❌ Not needed | On a phone call, the bot's voice and the caller's voice arrive on separate channels. The handset cancels its own speaker echo, and the browser already does it (`echoCancellation: true`). Our echo rule catches what leaks through. |
| An LLM/SLM to judge every interruption | ❌ Don't | Adds 300 ms+ and cost to every check. Our rules already cover the cases the article lists. |
| Speaker diarization (NeMo etc.) | ❌ Don't | Labels "who spoke when" after the fact. It is heavy and doesn't stop background speech in real time. |
| DeepFilterNet / RNNoise denoising | ❌ Not first | Removes noise but keeps background *voices*, and it can hurt transcription accuracy. |
| Target-speaker check (is this the caller's voice?) | ✅ Later, high value | The only thing that really separates the caller from a person nearby. Needs tuning on real 8 kHz phone audio. |
| Voice isolation SDK (Krisp VIVA, ai-coustics) | ⏸ Only if measurements show the need | Strong, about 20 ms, runs on CPU on-prem, but it is commercial (licence, vendor security review). |
| LiveKit adaptive interruption model | ❌ Not usable | Runs on LiveKit's cloud, so call audio would leave the bank. |
| No interruptions during critical prompts | ⚠️ Narrowly | Only for a few seconds (e.g. "enter the code on your keypad"). A bank caller must always be able to stop the bot. |

#### The plan, in order

**Phase A: Now, on this laptop, no new libraries (numpy only)**

| Step | What | How | Why |
|---|---|---|---|
| A1 | Lower the volume instead of pausing | See 1.1 | Removes the mid-sentence silence |
| A2 | Caller loudness gate | During the caller's own turns, track how loud their speech is (median RMS of speech frames). While the bot talks, ignore a possible interruption more than about 10 dB quieter than that. | People nearby and TVs are almost always much quieter than the person holding the phone. Costs microseconds. |
| A3 | Stricter speech detection while the bot speaks | Raise the Silero threshold from 0.5 to about 0.7 and `bargein_min_ms` from 350 to about 450 ms, *only* while the bot is speaking | Higher bar for interrupting, normal bar for starting a turn |
| A4 | Short blocks where interruptions are ignored | The first 300 ms of each bot reply, and the keypad instruction | Most false triggers happen at the moment the bot starts talking |
| A5 | **Measure it** | A test set: 20 clean caller phrases plus 10 background clips (TV, office chatter, traffic, a second person). Mix them at several loudness levels and run them through `VoiceSession` offline. | Without numbers we are guessing. This is how we know whether Phase C is needed. |

Targets for A5:
- False interruptions: under 5% of background clips.
- Missed real interruptions: under 2%.
- Time from the caller starting to speak to the bot stopping: under 600 ms.

**Phase B: Server, with real Cisco call audio**

| Step | What | How | Why |
|---|---|---|---|
| B1 | Match telephone audio | Calls arrive as 8 kHz G.711. Silero supports 8 kHz natively, so run it at 8 kHz rather than upsampling, and retune A2/A3 on recorded calls. | Thresholds tuned on a laptop mic won't hold on phone lines |
| B2 | Target-speaker check | Build a voice print of the caller from their first 3–5 seconds of clear speech (SpeechBrain ECAPA-TDNN or WeSpeaker, both Apache-2.0, small, CPU). On a possible interruption, compare it with that voice print and reject a different voice. | The research shows this is what separates people. A published comparison measured false interruptions at 33% without a target-speaker check and 10% with one, at a similar 140–170 ms. Note: this is **not** identity verification; OTP stays. |
| B3 | Better end-of-turn detection | Pipecat Smart Turn v3 (BSD-2, about 8 MB ONNX, 20–80 ms on CPU, audio only). Use the model file only, not Pipecat itself. | Fewer cases of the bot replying while the caller is still thinking |

**Phase C: only if Phase B still misses the A5 targets**

- **C1. Evaluate Krisp VIVA voice isolation** in front of the speech detector.
  - On-prem, CPU, about 20 ms; Krisp says background-voice removal makes turn-taking 3.5× more accurate.
  - Needs: a licence, a vendor security review, and an on-prem deployment to confirm with Krisp.

**Why not do B and C now:**
- Tuning depends on the audio. Phone audio (8 kHz, compressed, handset echo cancellation already applied) behaves very differently from a laptop browser mic.
- Tuning now would be redone later.
- Phase A fixes most of what you're hearing today.

---

## 2. Very large PDFs (500+ pages)

**How it works today:**
- pdfplumber reads the whole file and keeps every page in memory.
- All passages are embedded, then the whole version is switched over in one transaction.
- Passages have no page number, and the upload limit is 25 MB.
- With a 500-page, 60 MB rate book this hits the size limit, uses a lot of memory, and gives the caller and the auditor no page reference.

| Step | What | How | Why |
|---|---|---|---|
| P1 | Read one page at a time and free memory | `page.flush_cache()` (or `page.close()`) after each page in [parsers.py:106](brain/app/adapters/parsers.py); yield blocks per page | Memory stays flat whatever the page count |
| P2 | Page numbers on every passage | Add `page_from`/`page_to` to `chunks` (migration 004); show them in citations, the audit and the KB page | Auditors and agents can check the exact page |
| P3 | Remove repeated headers and footers | Lines that appear in the same place on more than 50% of pages are dropped before chunking | Otherwise "AU Small Finance Bank · Page 37 of 512 · Confidential" lands in hundreds of passages and weakens search |
| P4 | Skip the contents and index pages | Detect pages that are mostly "title ..... 37" lines | They match almost every question and push real answers out |
| P5 | Embed in batches, report progress | Embed 64 passages at a time; store `processed_pages` on the job; show a progress bar on the KB page | A 500-page file takes minutes; people need to see it is moving |
| P6 | Higher limit, streamed upload | `max_upload_mb` to 200 for PDFs; write the upload to disk in chunks, never all in memory | Real bank PDFs are big |
| P7 | Narrow the search by document | The question-understanding step already extracts the product; store the product/category on each document and give matching documents a higher rank | With 50 big PDFs, the same table heading appears in many files |
| P8 | Wider passage on demand | When the top passage is a table piece, also pass the passage before and after it to the LLM | Long tables split across passages |
| P9 | Scanned PDFs | Today they are rejected. Later: OCR with Tesseract (Apache-2.0) on the server, flagged "OCR, check" in the KB | Some circulars are scans |

**Does search slow down?** No. The HNSW vector index handles millions of passages in milliseconds. 500 PDFs × 500 pages is about 250k–500k passages, which is fine for one Postgres. Embedding is the slow part: about 1–3 minutes per 500 pages on this GPU. That cost is paid once at upload and runs in the background worker, which **should become a separate process on the server** so heavy uploads never slow live calls.

**When:** P1–P8 Now. P9 and the separate worker process: Server.

---

## 3. Customer data from bank APIs (instead of Excel)

**How it works today:**
- The code was built for this swap: everything uses the `Bank` interface ([ports.py](brain/app/ports.py)).
- The Excel reader is one adapter.
- The new work is a `BankAPI` adapter with the same two calls (`find_by_mobile`, `customer`), plus more as the APIs allow (card block, statement).

| What | How | Why |
|---|---|---|
| `BankAPI` adapter | httpx with a connection pool, mutual TLS or OAuth client credentials as the bank requires | Same interface, so no other code changes |
| Timeouts and retries | 2 s timeout, 1 retry on network errors only, never on a 4xx; a circuit breaker opens after 5 failures in 30 s | A slow core system must not freeze calls |
| A "system down" reply | "I can't fetch your account details right now. Shall I connect you to an agent?" plus an alert | Never guess customer data |
| Fetch only what the question needs | Balance asks for balance, not the full profile | Data minimisation (DPDP), and faster |
| No caching beyond the call | Cache for the life of the call only, in memory | Customer data must not pile up anywhere |
| Log every data access | Already in `auth_events`; add which API, which fields, the result and the latency | Auditors ask "who saw what" |
| Contract tests | A fake API server in tests that returns the bank's real response shapes, including errors | Catches breaking API changes early |

**When:** Bank (needs API specs and a test environment). The adapter takes about 1–2 days once the specs arrive.

---

## 4. Audit of every call, recording, transcripts

### 4.1 What a bank expects

There is no single RBI format for a voice-bot audit. I found no public RBI circular that sets one, and vendor blogs disagree on retention: figures from 90 days to 5+ years are quoted. **The exact retention period is a policy decision for the bank's compliance team; it must be a setting, not code.**

What the RBI IT Governance Master Direction (Nov 2023, in force April 2024) and the customer-data-protection advisory do require:
- audit logs that are **centralised, tamper-proof and ready for forensics**;
- a written retention period;
- governance of AI tools and chatbots in proportion to their risk;
- for AI: tests of accuracy and bias, and disclosure of AI use by third parties.

DPDP adds consent, purpose limitation and deletion rights. Retention required by sector rules is the documented legal basis for keeping recordings.

So the audit record per call should answer six questions an inspector, a QA reviewer or a complaint handler asks:

1. **Who** called, from which number, was the caller verified, how and when.
2. **What** the caller said, turn by turn, with times.
3. **What** the AI replied, word for word as spoken.
4. **Why** that reply: what it understood, which documents and page it used, which bank data it read, which checks passed, which rule fired.
5. **What happened**: answered, transferred, refused, dropped, plus any alerts and human interventions.
6. **Proof** that the record was not changed (hash chain), and the recording.

### 4.2 How it should look (readable, no JSON)

This is the call detail screen, also exported as a PDF:

```
Call 7F3A-21 · 4 Nov 2026, 16:24–16:29 (4 m 51 s) · Inbound · Hindi + English
Caller  +91 98•••••210 · Verified 16:25:10 (registered mobile + one-time code)
Outcome Answered 5 · Transferred to agent 1 · Alerts 1 (asked for a human)
▶ Recording ━━━━━━━━━━●──────────── 2:14 / 4:51   [caller | bot]

16:24:08  CALLER   "Mera FD rate kya hai senior citizen ke liye, 13 mahine?"
16:24:10  AI       "Senior citizens ko 13 mahine ki FD par 7.60% milta hai."
          Why      General question → answered from the knowledge base.
                   Understood: FD rate · senior citizen · 13 months · Hinglish
                   Source: Rates at a glance (valid from 23 Apr 2026), p. 2, "Domestic TD < 3 Cr"
                   Checks: ✓ every figure is in the source  ✓ no figures in words  ✓ language matches
                   Grounding: high (source relevance 0.97)  ·  Reply in 2.1 s
16:25:02  CALLER   "Mera balance batao"
16:25:03  AI       "Aapke registered mobile par ek code bheja hai..."
          Why      Personal question → caller not verified → one-time code sent (keypad only).
...
16:28:40  CALLER   "Mujhe kisi insaan se baat karni hai"
16:28:41  AI       "Main aapko ek agent se jod rahi hoon."
          Why      Caller asked for a human → transfer.  ⚑ Alert sent to the supervisor.
```

The design rules behind this view:
- The caller's and the bot's lines are in plain language.
- The "why" is a short readable line built from the trace. The raw data is one click away for engineers, never in the main view.
- Clicking a line jumps the recording to that moment.
- Codes and PINs are never shown; they are typed on the keypad and never reach the transcript.

### 4.3 Is there a confidence score?

**Today: no single number, on purpose.** An LLM's own "I'm 92% sure" is not reliable. What we have are real, measurable signals:

| Signal | Source | Meaning |
|---|---|---|
| Speech-to-text certainty | Whisper `no_speech` / later Sarvam | Did we hear the caller correctly? |
| Retrieval relevance | Reranker score of the passages used (0–1) | Did we find a passage that actually answers this? |
| Answer checks | Number check, figures-in-words check, citation check | Is every figure in the reply really in the source? |
| Route certainty | Personal answers come from bank data via fixed templates | Deterministic: no AI wording |

**Recommendation:** show a **grounding level** per reply: **High / Medium / Low / Not applicable**, built from those signals by fixed rules. For example, High means the top source scored ≥ 0.8, all checks passed, and speech-to-text was clear. Show the components next to it. Banks prefer a rule they can explain to an auditor over a made-up percentage.

Low-grounding replies count toward alerts (section 5) and feed a QA review queue.

### 4.4 Call recording

| What | How | Why |
|---|---|---|
| Record both sides | Stereo WAV/Opus: left = caller (exactly what the bot heard), right = bot (exactly what it said). Written in pieces during the call, finalised at hang-up. | Stereo lets QA hear overlaps and interruptions exactly; Opus keeps size to about 1 MB per 10 minutes |
| Never record the code | The code is typed on the keypad, so it is already not in the audio. Also mark keypad periods in the timeline. | Same principle as PCI pause-and-resume and keypad-tone masking |
| Storage | Encrypted at rest (AES-256-GCM, key from the bank's KMS/HSM; on the laptop, a local key file). Path stored on the call record with a SHA-256 hash. | Recordings are personal data |
| Retention | Setting `recording_retention_days`; a nightly job deletes expired recordings and writes an audit entry for each deletion | DPDP plus the bank's policy |
| Access | Only roles with "listen to recordings"; every playback is itself logged | "Who listened to my call" is a standard audit question |
| With Cisco | Either we record (we hold both sides anyway) or Cisco records via CUBE SIPREC media forking to the bank's existing recorder. Ask the bank which recorder is the official one. | Banks often already have an official call recorder; ours may then be a QA copy |

**When:** browser prototype Now; final design Cisco.

### 4.5 Transcripts in logs

- Add one log line per turn: `call=7F3A-21 turn=3 role=caller text="mera balance batao"`, and one for the bot's reply. Text is masked by the same `privacy.redact` rules already used.
- Logs go to the bank's central log system (SIEM), which RBI requires to be centralised and tamper-proof.
- The full, readable record lives in the database (section 4.2).
- Logs are for operations and search; the call record is the official audit.

### 4.6 Tamper evidence

- Every turn record stores `hash = SHA-256(previous hash + this record)`, so the turns of a call form a chain.
- At call end, the final hash is written to the call record and the log.
- Any later edit breaks the chain, and a verify button in the console shows it.
- The standard library is enough (`hashlib`).

**When:** Now.

---

## 5. Real-time alerts

| Alert | Trigger (deterministic) | Severity |
|---|---|---|
| Wants a human | Intent `human`, or "agent/insaan/manager" twice | High |
| Complaint or fraud | Intent `issue` with fraud words ("fraud", "unauthorised", "card stolen", "paise kat gaye") | Critical |
| Verification locked | 3 wrong codes or 3 resends | High |
| Possible manipulation | Intent `manipulation` (prompt injection, asks for other customers' data) | High |
| Bot is struggling | 2 "I don't know" replies, or 3 low-grounding replies, in one call | Medium |
| Angry caller | Abusive words or the caller repeating the same question 3 times | Medium |
| Long silence or dead air | Bot or caller silent for more than 8 s mid-call | Low |
| System health | LLM/STT/TTS error rate above 5% over 5 min, latency p95 above 4 s, bank API down | Critical (ops) |

**How:**
- An `alerts` table (call, type, severity, time, acknowledged by, when).
- The rules run on events the conversation already emits.
- They are pushed to the console over a WebSocket (`/v1/live`), using no new libraries.
- Later the same events go to email, SMS or Teams through the bank's notification service.

**Why rules, not AI:**
- Every alert must be explainable ("raised because the caller said X").
- Rules add no extra latency.
- Sentiment can be added later as one more signal, never as the only trigger.

**When:** Now.

---

## 6. The monitoring console (for the customer-service team)

### 6.1 What others show (research)

**Verloop** (the current vendor). Verloop's site blocks automated reading, so this comes from its public pages and directories, not a hands-on demo. **Ask the bank's team for a screen-share of their Verloop dashboard**; it is the best reference for what they're used to. From public sources:
- real-time analytics across chat and voice: CSAT, first response time, resolution rate, drop-offs, agent performance;
- voice metrics: call drop rate, ASR accuracy, turn-taking, voice latency, resolution, sentiment;
- AI-based conversation tagging;
- live chat takeover. I found nothing public confirming live *voice* takeover.

**Others** (ElevenLabs, Dialpad, Pinecall, contact-center suites):
- a live call list with status and sentiment;
- an open-a-call live transcript;
- whisper or barge-in for supervisors;
- alerts by screen pop, email or SMS;
- call history with recording and transcript;
- QA scoring;
- latency and interruption analytics.

### 6.2 Screens

| Screen | What it shows | Actions |
|---|---|---|
| **Live** | Cards or rows for each active call: duration, language, verified or not, current step (listening / thinking / speaking / on keypad), last caller line, alert badge | Open, listen live, **take over** (transfer to an agent), end call |
| **Live call** | Live transcript scrolling as it happens, the "why" for each reply, latency per turn, verification state, audio level meter | Listen in (supervisor hears the call), take over, add a note, flag for QA |
| **Calls (history)** | Searchable table: date, caller (masked), duration, language, outcome, alerts, grounding, agent transfer, rating. Filters on all of these. | Open, export, flag |
| **Call detail** | The audit view in 4.2: header summary, recording player with waveform and a caller/bot track, timeline of turns with "why", alerts, notes, hash check | Play from any line, export PDF, add QA score, mark reviewed |
| **Alerts** | Live list by severity, unacknowledged first | Acknowledge, open the call, assign |
| **Analytics** | Calls per hour, containment (handled without a human), transfer rate and reasons, top questions, "I don't know" questions (gaps in the knowledge base), latency p50/p95 per stage, language mix, false-interruption rate, feedback | Date range, export |
| **Knowledge base** | What exists today, restyled | Same |
| **Test** | Today's test console: start a call from the browser | New call; every test call is saved and appears under Calls |

### 6.3 Look and technology

- **shadcn/ui** for the components. It is a good fit for a bank:
  - The code is **copied into our repo**, not loaded as a black-box package, so the bank's reviewers can read every line.
  - It is built on Radix UI (MIT, very widely used) and Tailwind, which we already use.
  - Run `npx shadcn add` once per component, pin versions in the lockfile, and review the diff. Use **only the official registry**; shadcn.io and similar third-party "blocks" sites are not affiliated with shadcn and should not be pulled from.
  - Charts: shadcn's chart component (Recharts, MIT).
  - The waveform player can be written by hand with the Web Audio API (no extra library).
- **Style:**
  - neutral greys with **one** accent colour: the bank's brand colour (AU's purple/orange, to confirm with their brand team);
  - colour used only for meaning: red = critical alert, amber = warning, green = verified/resolved;
  - dense tables, a left sidebar, a dark mode.
  - Reference look: Linear, Vercel dashboard, Stripe dashboard. For contact-center specifics, Dribbble searches "call center dashboard", "voice agent dashboard", "supervisor console".
- **Production readiness:**
  - Static export served by the bank's web server;
  - login through the bank's identity provider (SSO/SAML/OIDC) with roles: viewer, QA, supervisor, admin, auditor;
  - **every console action logged** (who opened which call, who listened, who took over);
  - phone numbers masked unless the role allows;
  - a strict Content Security Policy;
  - no third-party scripts or analytics.

### 6.4 Data model changes behind it

The console only needs the backend to store calls properly. **This is the foundation to lay now, even before any new screens**:

| Table | New / change | Holds |
|---|---|---|
| `sessions` → treat as **calls** | Add `channel` (test, web, phone), `started_at`, `ended_at`, `end_reason`, `outcome`, `recording_path`, `recording_sha256`, `chain_hash`, `transferred_at` | One row per call; test calls are saved like real ones |
| `traces` | Add `turn_no`, `prev_hash`, `hash`, `grounding`, `spoken_text` (what TTS actually said, including whether it was cut off by an interruption) | One row per turn |
| `call_events` | New | Timeline events: speech started, interruption check, keypad, filler, alert, transfer, errors |
| `alerts` | New | Section 5 |
| `audit_log` | New | Console actions: viewed, listened, exported, took over |

---

## 7. Cisco telephony (later)

Ask the bank first: **"Which Cisco contact-center product do you run: Webex Contact Center (cloud), UCCE/PCCE with CVP, or UCCX?"** The integration depends on the answer.

| Bank runs | Route | What we build |
|---|---|---|
| **Webex Contact Center** | **Bring Your Own Virtual Agent (BYOVA) over gRPC.** Webex streams the caller's audio to our gRPC server and plays our audio back; a "Virtual Agent Voice" step in the flow hands the call to us and back (e.g. to transfer). Cisco publishes a Python gateway sample. | A gRPC adapter in front of `VoiceSession` (replaces the WebSocket). Needs a TLS endpoint reachable by Webex. |
| **UCCE / PCCE with CVP**, or UCCX | **SIP trunk** from Cisco CUBE to a media gateway we run. Calls arrive as SIP + RTP (8 kHz G.711); transfer back with SIP REFER or via the CVP flow. Recording through **CUBE SIPREC** to the bank's recorder (CUBE's older WebSocket media forking is deprecated). | A small SIP/RTP gateway. Use a mature, bank-acceptable SIP stack (e.g. FreeSWITCH or Asterisk) as the gateway and stream audio to `VoiceSession`, rather than writing SIP ourselves. |

Both routes reuse everything else unchanged: the brain, verification, audit and console.

**Telephony-specific work:**
- **8 kHz audio:** tune the speech detector and STT on it (section 1.4 B1).
- **Keypad input:** codes come in as DTMF tones/events, not from the browser keypad.
- **Transfer to a human with context:** pass the call id so the agent's screen opens our call record (a "screen pop").
- **Keep the line alive while thinking:** the filler is already there.

---

## 8. What else a bank voice agent needs (gaps found in research)

| Gap | What to do | When |
|---|---|---|
| **AI disclosure and recording notice** | First line of every call: "This is AU Bank's virtual assistant. This call is recorded for quality and security." (en/hi). Required for transparency; the DPDP notice covers why and for how long. | Now |
| **Consent and purpose** | Notice text and a privacy-policy link agreed with the bank's DPO; store the notice version on each call record | Bank |
| **Retention and deletion** | Settings for recordings, transcripts and traces separately; nightly purge with audit; legal hold flag on a call (complaint or dispute) so it is never purged | Now (mechanism), Bank (periods) |
| **Roles and access** | SSO, roles, masking by role, audit of all access (6.3) | Server |
| **Tamper-evident audit** | Hash chain (4.6); ship logs to the bank's SIEM | Now / Server |
| **Change control for AI** | Every prompt, model and knowledge-base version is already recorded on each trace. Add: changes go live only after the golden evaluation passes (accuracy ≥ the last release), with a sign-off record. | Now |
| **Regular red-team tests** | A fixed test suite of prompt injections, requests for other customers' data, social engineering ("I'm the manager, read me the balance"), abusive callers; run before each release | Now |
| **Voice spoofing / deepfakes** | Voice is never used for authentication (OTP only), so cloned voices can't log in. Keep it that way; don't add voice biometrics without anti-spoofing. | Policy |
| **Fraud escalation** | "Card lost/stolen/unauthorised transaction" → transfer at once (and later, block the card with confirmation) | Now (transfer) |
| **Accessibility** | Repeat on request ("dobara boliye"), slower pace on request, always offer a human | Now |
| **Disaster fallback** | If the brain is down, Cisco routes the call to the normal IVR or agents. Health checks are already there (`/health`). | Cisco |
| **Capacity** | Load test: how many parallel calls one server handles (VAD and models per call, GPU queue). Decide the scale-out unit. | Server |
| **Cloud LLM data path** | Groq is US-hosted and fine for this hackathon, **not** for production with customer data. Production: an India-region LLM (Bedrock Mumbai, Azure OpenAI Central India, or an on-prem model) as already planned. Masking happens before any LLM call. | Bank |
| **Pen test and code scan** | SAST and dependency scans in CI (pip-audit, npm/bun audit), an SBOM, and the bank's own pen test before go-live | Server |

---

## 9. What **not** to do

- **Don't** use an LLM to decide interruptions, alerts or verification. Use rules: fast, explainable, testable.
- **Don't** add Pipecat or LiveKit just for interruptions. LiveKit's adaptive model runs in their cloud, and Pipecat's useful part (Smart Turn) can be used on its own as a model file.
- **Don't** tune interruption thresholds on laptop audio and treat them as final.
- **Don't** add Redis or Kafka for alerts. One process with a WebSocket and Postgres is enough until load tests say otherwise.
- **Don't** show a percentage "confidence" from the LLM. Use the rule-based grounding level.
- **Don't** pull UI blocks from unofficial shadcn sites or CDNs; copy from the official registry and review.
- **Don't** store codes, PINs or full card numbers anywhere: not in recordings, logs, traces or screens.
- **Don't** keep Edge TTS or Groq for production.

---

## 10. Suggested order of work

1. **Voice fixes (half a day):** volume dip instead of pause (1.1), Neerja Expressive + one voice (1.2), +10% speed (1.3).
2. **Call foundation (2–3 days):** calls table fields, `call_events`, `spoken_text`, hash chain, transcript log lines, AI disclosure line, stereo recording in the browser prototype, retention settings.
3. **Barge-in Phase A (2 days):** loudness gate, stricter detection while speaking, start-of-reply block, the offline test set with numbers.
4. **Large PDFs (2 days):** P1–P8.
5. **Alerts (1 day):** rules, table, live WebSocket.
6. **Console (5–7 days):** shadcn setup, Calls, Call detail with recording and readable audit, Live, Alerts, Analytics.
7. **When the bank delivers:** `BankAPI` adapter, Sarvam keys, India-region LLM, Cisco route, SSO.
8. **On the server with real calls:** barge-in Phase B (8 kHz, target-speaker check, Smart Turn), load tests, then decide on Phase C (Krisp).

## 11. Questions to ask the bank

1. Which Cisco product (Webex CC, UCCE/PCCE+CVP, UCCX)? Who is the Cisco contact?
2. Is there an official call recorder already (NICE, Verint, Cisco)? Should ours be the official recording or a QA copy?
3. Retention periods for recordings, transcripts and audit logs? Legal-hold process?
4. Which LLM, STT and TTS providers are approved, and where must data stay (India region / on-prem)?
5. Bank API specs, authentication method and test environment for customer data?
6. Who uses the console (roles)? SSO provider?
7. Can we get a screen-share of the current Verloop dashboard?
8. Which alerts matter most to the service team, and where should they arrive (console, Teams, email, SMS)?
9. Brand colours and voice persona (name, gender, tone)?
10. The required opening disclosure text?

---

## Sources

Verloop and monitoring UIs
- [Verloop.io](https://www.verloop.io/?p=29344) · [Verloop help: overview](https://help.verloop.io/article-categories/overview-getting-started-2) · [Verloop design on Dribbble](https://dribbble.com/verloopdesign)
- [ElevenLabs real-time monitoring](https://elevenlabs.io/docs/agents-platform/guides/realtime-monitoring) · [Dialpad AI Supervisor Insights](https://help.dialpad.com/docs//ai-supervisor-insights) · [Pinecall features](https://pinecall.io/features) · [Supervisor dashboard for AI voice agents (blog)](https://tarzalt-dev.pages.dev/blog/ai-voice-agent-supervisor)

Bank rules
- [RBI Master Direction: IT Governance (PDF)](https://website.rbi.org.in/documents/87730/39710850/Master+Directions+-+IT+Governance+and+Practices+-FINAL.pdf) · [RBI customer data protection advisory (Mondaq)](https://www.mondaq.com/india/financial-services/1772212/rbi-issues-advisory-on-customer-data-protection-best-practices-a-practical-guide-for-regulated-entities) · [RBI responsible AI (Mondaq)](https://www.mondaq.com/india/financial-services/1672260/rbis-move-toward-ethical-and-responsible-ai-in-financial-sector)
- Vendor views on recording retention (they disagree): [Caller.Digital](https://caller.digital/blog/voice-ai-compliance-india-2026) · [Exotel](https://exotel.com/blog/outbound-voice-ai-compliance/) · [Elision](https://www.elisiontec.com/cloud-telephony-for-bfsi-in-india-compliance-guide-for-2026.md)
- PCI and recordings: [Paytia: DTMF masking vs pause and resume](https://www.paytia.com/resources/blog/dtmf-masking-vs-pause-and-resume) · [Eckoh: pause and resume](https://www.eckoh.com/blog/why-so-called-pause-and-resume-systems-are-not-pci-dss-compliant)

Voice
- [Sarvam text to speech](https://www.sarvam.ai/text-to-speech) · [Bulbul docs](https://docs.sarvam.ai/api-reference-docs/models/bulbul) · [Sarvam trust center](https://www.sarvam.ai/trust-center)
- [Krisp VIVA 2.0 launch](https://www.businesswire.com/news/home/20260506612131/en/Krisp-Launches-VIVA-2.0-Introducing-Voice-Infrastructure-for-Voice-AI-Agents) · [Krisp on Vapi](https://vapi.ai/partnerships/directory/partner/krisp-technologies-inc)
- [LiveKit adaptive interruption handling](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/) · [LiveKit tuning](https://docs.livekit.io/agents/logic/turns/tuning/) · [FireRedChat paper (target-speaker barge-in numbers)](https://arxiv.org/pdf/2509.06502)
- [Pipecat Smart Turn](https://github.com/pipecat-ai/smart-turn) · [Smart Turn v3.2 ONNX](https://huggingface.co/soniqo/Smart-Turn-v3.2-ONNX) · [VAD for telephony (8 kHz)](https://edesy.in/docs/voice-agent/pipeline/vad.md)

Large documents
- [Chroma: chunking](https://docs.trychroma.com/guides/build/chunking) · [Hierarchical chunking](https://cobusgreyling.substack.com/p/hierarchical-chunking-in-rag-in-a)

Cisco
- [Webex BYOVA over gRPC](https://developer.webex.com/docs/bring-your-own-virtual-agent-over-grpc) · [Webex BYOVA gateway (Python)](https://webex.github.io/webex-byova-gateway-python) · [CUBE WebSocket media forking deprecation](https://www.cisco.com/c/en/us/products/collateral/unified-communications/unified-border-element/deprecation-notice-websocket-based-media-forking.html) · [Cisco community: SIPREC recording](https://community.cisco.com/t5/contact-center/whole-call-recording-in-uccx/m-p/5027384/highlight/true)

UI
- [shadcn.io (third-party, not official)](https://www.shadcn.io/blocks/account-audit-trail): example of the blocks to avoid pulling directly
