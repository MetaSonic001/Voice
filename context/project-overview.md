# AU Small Finance Bank: inbound voice agent

## Overview

An inbound AI voice agent for AU Small Finance Bank customers. It answers product questions from the bank's approved
documents (RAG), verifies callers with a registered mobile number and a keypad code, reads the caller's own bank data,
does a few controlled card actions after an explicit "yes", raises tickets, and hands over to human agents with
context. English, Hindi and Hinglish. It is a hackathon build growing into a production proof of concept, so security
and compliance come before features.

## Goals

1. Behave like a good first-line bank representative: several requests in one sentence, kept context, short human replies.
2. Never invent a figure: every number comes from an approved document or from bank data.
3. Keep OTP, PIN and card/identity numbers out of speech-to-text, the LLM and the logs.
4. Accuracy and first-audio latency must never get worse than the last measured numbers (`progress-tracker.md`).
5. Every call is reviewed after it ends (code checks always, LLM judge on a risk-weighted slice).

## Core user flow

1. Caller connects (browser console today, Cisco telephony in production); the agent plays the disclosure.
2. General question: understand, retrieve, answer with a checked, cited reply.
3. Personal question or action: verify (registered mobile, then a keypad code), then answer from bank data or
   confirm and run a card action.
4. Problem or fraud: protect first (offer the card block), then a ticket or a transfer to the right team.
5. Hand-over to a person with a brief, the transcript and an AI summary; or a callback outside desk hours.
6. After the call: automatic review, visible on `/calls`.

## Scope

### In scope

- Product Q&A, balance and transaction reads, card block/unblock/controls, complaints, disputes, callbacks, leads.
- Mock bank APIs shaped like separate bank systems; the adapter is swapped for AU's real APIs later.
- Test console: `/` (console), `/kb`, `/calls`, `/desk`.

### Out of scope (people only, never the agent)

- Closing an FD, payments, limit changes, address or mobile changes, refunds.
- Production items: Sarvam STT/TTS, Cisco telephony, SSO, India-hosted LLM (Bedrock), the AWS deployment (proposed: ECS, one app). NRI and Business queues. What is left: `pending-work.md`.

## Success criteria

See `reference/PROJECT-GUIDE.md` §25.3 (the call review, issues A to Q) and the issue table in `progress-tracker.md`.
