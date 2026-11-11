# ADR-0013: Browser speaker echo: not fixed; use earphones

- **Status:** Accepted
- **Date:** 2026-11-05
- **Deciders:** project owner

## Context

On laptop speakers, the agent's own voice came back as caller speech in 3 recorded calls ("One moment, please" turned up in transcripts). Cause: Chrome, Edge and Brave don't use Web Audio playback as the echo-cancellation reference (a long-standing Chromium issue). Phone lines cancel echo in the handset and network, so this only affects the browser demo.

## Decision

Don't fix it. Use **earphones** for browser demos.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Earphones, no code** | Zero work; doesn't affect production | Demos on speakers can misfire |
| WebRTC loopback playback (~40 lines) | Known workaround | Not guaranteed; browser-only benefit |
| Server-side echo text filter | Also protects the phone path | Work for a problem phones don't have |
| Speakerphone mode (strict barge-in) | Safety net | Changes behaviour; more code |

## Consequences

- **Easier:** effort goes to production-relevant work.
- **Harder:** any speaker demo can show the agent hearing itself. A reviewer argued this should be fixed; the owner decided against it.
- **Revisit when:** a browser or app voice channel becomes a product (then use WebRTC, [0012](0012-websocket-pcm-not-webrtc.md)), or demos must run on speakers.

## Links

`PHASE-4-PLAN.md` §2.
