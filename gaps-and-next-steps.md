# What's covered, what's left (v0.1)

This checks everything so far (`scenario-map.md`, `tech-architecture.md` and the two diagrams) against the problem statement, the sample questions and the dummy data, plus research on running an AI voice agent at an Indian bank.

**Short answer:**
- **Done:** the scenarios (what the agent does) and the architecture and stack (what it's built with).
- **Not designed yet:**
  - how we **prove** it works: evals, audit trail, demo;
  - how it **sounds**: persona and conversation design;
  - the **compliance touches** a bank call needs: AI and recording disclosure, data residency.
- **Missing operational situations:** a few, such as no human agent being available.

---

## 1. Coverage against the brief

✅ done · 🟡 partly · ❌ not yet

| Brief asks for | Status | Where / what's missing |
|---|---|---|
| Inbound call, set language | ✅ | G0, X3, architecture |
| Understand, clarify | ✅ | X1, decision ladder |
| Verify before personal info or actions | 🟡 | Front door + X2. **The exact method is still open**, including how OTP works in a browser demo |
| Fetch data / do permitted action via mock APIs | 🟡 | Flows B and C. **API list and data model not written down yet** |
| Explicit confirmation before card block | ✅ | Flow C |
| Outcome or transfer with context | 🟡 | Flow E. **Missing: what happens when no human is available** |
| Auditable record | 🟡 | Mentioned everywhere. **Audit event schema and a sample trail not designed** (it's a deliverable) |
| English, Hindi, Hinglish + extensible | 🟡 | Covered. **Which extra language for the bonus is undecided** |
| Fallback for unavailable services / low confidence | ✅ | X4, G2-E |
| Masking, encryption, data minimisation | 🟡 | Masking and KMS covered. **PII redaction before the LLM, retention and data residency not designed** |
| Document limitations and risks | ❌ | No risk register yet |
| Disclose third-party models and services | 🟡 | The stack lists them. **No disclosure document yet** |
| Measurable evidence (functionality, latency, multilingual, CX) | ❌ | **No evaluation plan or benchmark set yet** (an evaluation report is a deliverable) |
| Natural, human-like voice and tone (30% of the score) | 🟡 | TTS chosen. **Persona, script and response-style rules not designed** |
| Bonus: noisy calls | ✅ | RNNoise, confirm critical details |
| Bonus: accessibility, adaptable speech | 🟡 | X5 mentions it. Not specified |
| Bonus: quality analytics (unresolved needs, high-friction journeys) | ❌ | **Not designed** |
| Deliverables: demo, recorded walkthrough, code, architecture, eval report, sample audit trail | 🟡 | Only the architecture exists |

---

## 2. Gaps to close

### A. Needed to *prove* it works (deliverables and scoring)
1. **Evaluation plan and benchmark.**
   - **Test set:** 79 questions × 3 languages + 8 multi-turn cases, plus noisy, accented and interrupted variants, plus a **red-team set** (social engineering, "read my full card number", prompt injection spoken aloud, third-party callers).
   - **Metrics:**
     - STT word error rate;
     - intent and detail accuracy;
     - task completion;
     - correct escalation;
     - control compliance (no disclosure without verification, no action without confirmation);
     - time to first audio and per-turn latency;
     - cost per call.
   - **How:** scripted and simulated callers (TTS audio + noise), scored automatically and by an LLM judge, then reviewed by a human.
2. **Audit trail design.**
   - **Event schema:** call_started, language_set, intent, verification_attempt/result, tool_call (masked request and response), confirmation_asked/given (with transcript snippet), action_result, escalation, call_ended.
   - **Hash-chaining**, so tampering is detectable.
   - A **sample audit trail** for one full call, which is itself a deliverable.
3. **Demo design.**
   - **Which 4+ journeys to show:** for example a rates question → a balance after OTP → a card block with a mid-way "wait, which card?" → "connect me to someone".
   - **Personas** from the dummy data.
   - **A backup recording** in case the live demo fails.
4. **Demo screen (UI).** A call widget, a live transcript with the detected language, a side panel showing the state and audit events as they happen, a **mock SMS panel** (where the OTP and "card blocked" SMS appear), and a **human-agent console** that receives the handoff note. This is what makes controls and auditability *visible* to judges.
5. **Quality analytics (bonus).** After each call: intent, resolved yes/no, where it failed, sentiment. A small dashboard showing top unresolved needs, high-friction journeys and escalation reasons.
6. **Limitations and risk register.** Known limitations (dummy data gaps, KB gaps, STT weak spots, latency on phone lines) and risks, each with a mitigation.

### B. How it should *sound* (customer experience, 30% of the score)
7. **Persona and voice:** an assistant name, which TTS voice, gender-neutral or not, respectful Hindi ("aap"), and a natural rather than robotic register. It must not imitate a real person.
8. **Opening script:** greeting, **"you're speaking with AU Bank's AI assistant"**, **recording notice**, and an invitation to speak in any language. Keep it under about 6 seconds.
9. **Response style rules:**
   - one idea per sentence;
   - say the answer first;
   - read at most 3 items in a list;
   - read numbers in lakh/thousand form;
   - fillers during tool calls;
   - closing lines;
   - how to apologise (once, briefly).
10. **Per-language phrasing for templates:** every figure template written in English, Hindi and Hinglish, reviewed by a native speaker.

### C. Banking and compliance touches
11. **Tell the caller it's an AI, and give them a right to a human.** RBI's FREE-AI framework (Aug 2025, recommendatory) expects banks to disclose AI use and let customers reach a human on request. We have the human path; add the disclosure to the opening.
12. **Recording and data consent (DPDP Act / Rules 2025).**
    - Give a recording notice at the start.
    - Use data only for servicing the call.
    - Don't use recordings for model training without separate consent.
    - Define a retention period: keep transcripts and drop raw audio early, where possible.
13. **Data residency and the LLM choice.** Common practice is to keep audio and transcripts in India and send only **redacted text** (no names, account numbers or OTPs) to any overseas LLM. This favours Sarvam (India), or Bedrock/Gemini through an India region, or adding a **PII redaction step** before the LLM. Add redaction as a pipeline stage either way.
14. **Card block follow-through.** After a block: a confirmation SMS (simulated), a reference number, and the replacement-card next step. Banks are expected to act immediately when a loss is reported.
15. **Complaint reference numbers.** Complaints and disputes (flow D) get a ticket ID. Optionally, tell the caller about the escalation path (bank first; RBI's complaint portal if unresolved after 30 days).
16. **Impersonation and voice-cloning risk.** The voice is never trusted as identity. Verification stays OTP-to-registered-mobile, and high-risk actions always need it, even after an earlier verification if the call has run long. Note voice-clone detection as a future layer, not for the MVP.

### D. Operational situations not yet in the scenario map
17. **No human available** (out of hours, queue full): offer a callback or ticket, give the expected time, and log it.
18. **Vendor outage:** if STT or TTS is down, fall back to a second provider. If the LLM is down, use a minimal scripted mode or transfer.
19. **Concurrency limits:** Sarvam pay-as-you-go allows about 20 concurrent STT streams. Fine for a demo; note it for scale.
20. **Speech keeps failing:** after repeated low-confidence turns, offer keypad options ("press 1 for card block, 2 for balance…") before transferring.
21. **OTP in a browser demo:** use an on-screen keypad, with the OTP shown in the mock SMS panel. On a real phone call, use keypad tones.
22. **Repeat caller within minutes** (optional): recognise "I called earlier about my card" through recent-call memory.
23. **Knowledge upkeep:** rates change, so the approved-answer cards need an "effective from" date and a simple update process.

### E. Decisions still open (from earlier)
- Verification details: OTP length, retries, how long a verification lasts.
- The demo's fixed "today" date.
- Whether transfer is simulated (console) or real.
- How far product suggestions go before they count as advice.
- An extra language for the bonus.
- Approved fallback content for KB gaps (FD penalty, turnaround times).
- **Parked by you:** the flow C/D simplification and the flow A "or website" wording.

---

## 3. Suggested order from here
1. Close the open decisions (E) and add the scenario gaps (D) to the scenario map.
2. Design: persona and script (B), audit schema (A2), mock bank API list + data model, PII redaction step (C13).
3. Prepare knowledge: transcribe rates by hand, write answer cards, resolve KB conflicts.
4. Build the evaluation set (A1) **before** building the agent, so every build can be measured.
5. Build: pipeline → front door + verification → flows A/B → C → D/E/F → demo UI.
6. Evaluate, write the report, limitations and risks, sample audit trail, demo script, recording.

---

## Sources
- RBI FREE-AI: [AuthBridge summary](https://authbridge.com/blog/rbis-free-ai-framework-key-highlights-summarised/) · [Scrut](https://www.scrut.io/post/rbi-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence) · [Khaitan & Co (PDF)](https://www.khaitanco.com/sites/default/files/2025-08/Ergo%20-%20FREE%20AI%20Framework%20-%2028%20Augusut%202025.pdf) · [RBI AI guidelines 2026](https://cybersigmacs.com/blog/rbi-ai-guidelines-model-risk-management-2026/)
- DPDP and voice AI: [DPDP Rules 2025](https://en.wikipedia.org/wiki/Digital_Personal_Data_Protection_Rules,_2025) · [Voice AI data residency India](https://caller.digital/blog/voice-ai-data-residency-sovereignty-india-dpdp-2026) · [DPDP vs TRAI consent for recordings](https://www.caller.digital/blog/dpdp-vs-trai-consent-voice-recordings-audit-trail-india-2026) · [DPDP call recording checklist](https://digitalcallers.com/pages/blog/dpdp-act-call-recording-checklist.html)
- Card loss and grievances: [Business Today on card blocking](https://www.businesstoday.in/magazine/banking/story/read-between-the-cards-127195-2008-08-21) · [RBI credit card grievance route](https://cardtrail.in/india-rules/rbi-credit-card-grievance-redressal-india/)
- Voice fraud: [ABA Banking Journal: voice fraud threat](https://bankingjournal.aba.com/2026/08/the-voice-fraud-threat-to-banking/) · [Famulor: deepfake call detection](https://www.famulor.io/blog/deepfake-call-detection-ai-phone-agent-vs-voice-fraud-2026)
