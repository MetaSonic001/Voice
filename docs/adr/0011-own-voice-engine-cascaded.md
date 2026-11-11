# ADR-0011: Own voice engine on a cascaded pipeline

- **Status:** Accepted
- **Date:** 2026-11-04
- **Deciders:** project owner

## Context

Natural phone conversation needs:
- detecting when the caller has finished speaking;
- ignoring coughs and background noise;
- telling a real interruption apart from "hmm" or echo;
- streaming replies so the first words come fast.

Banks need a **text record** of every step for audit. Production vendors (e.g. Gnani) use the same staged design for banks, even though voice-to-voice models exist.

## Decision

- **Cascaded:** speech-to-text → understanding/answer → text-to-speech, streamed sentence by sentence.
- **Own turn-taking engine** (~400 tested lines):
  - Silero VAD on 32 ms frames, timed by the audio itself;
  - an unfinished-sentence wait;
  - barge-in that pauses, transcribes the overlap, then decides interrupt / backchannel / echo / noise;
  - ducking, a phrase cache and fillers.
- **Providers behind ports:**
  - speech-to-text: Groq Whisper large-v3-turbo now, Sarvam (or similar) in production;
  - text-to-speech: Edge TTS (Neerja / Swara) now, a production TTS later.
- **Pronunciation lexicon** applied before text-to-speech (bank name, products, names).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Cascaded + own engine** | Auditable text at every step; grounding checks possible; components swappable | Latency adds up across stages |
| Voice-to-voice model | Lowest latency, natural prosody | No reliable text to check before speaking; hard to ground and audit |
| Pipecat / LiveKit agents | Ready pipelines and connectors | Lack our interruption behaviour; large dependencies; a media server to run |

## Consequences

- **Easier:** grounding, audit, provider swaps.
- **Harder:** we own the turn-taking quality; STT accuracy on 8 kHz phone audio must be re-tested before telephony.
- **Note on data:** audio goes to the STT provider (Groq today), which text redaction can't protect. Production STT is a data-handling decision.
- **Revisit when:** voice-to-voice models offer text checks and audit, or a production STT/TTS vendor is chosen.

## Links

`brain/app/voice/`, `PROJECT-GUIDE.md` §11.
