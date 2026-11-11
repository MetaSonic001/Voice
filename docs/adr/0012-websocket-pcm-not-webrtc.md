# ADR-0012: Browser test calls over WebSocket audio, not WebRTC

- **Status:** Accepted
- **Date:** 2026-11-04 (built); confirmed 2026-11-06
- **Deciders:** project owner

## Context

Developers need to talk to the agent from a browser. Production calls will come over the phone network (Cisco: SIP/RTP, usually 8 kHz), not from a browser. Phone providers typically stream call audio to applications over a plain WebSocket.

## Decision

The browser captures the mic with Web Audio (AudioWorklet) and sends **16 kHz PCM over a WebSocket**. Agent audio comes back the same way. The voice session only takes audio frames, so production adds a **phone transport adapter** (media gateway → voice session). The engine stays the same.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **WebSocket + PCM** | No media server, no STUN/TURN, no codec work; same shape as phone-provider streams | Chrome doesn't echo-cancel Web Audio playback ([0013](0013-speaker-echo-not-fixed.md)); no packet-loss handling (fine on localhost) |
| WebRTC (aiortc / LiveKit) | Echo cancellation, jitter handling, NAT traversal | A media server and dependency for a dev console; not used by phone calls anyway |

## Consequences

- **Easier:** a simple dev setup; telephony plugs into the same engine.
- **Harder:** speaker echo in the browser demo; 16 kHz tests overstate phone-line STT accuracy (downsample to 8 kHz before Cisco work).
- **Revisit when:** AU wants a "call from the app or website" channel. Real customers on mobile data need WebRTC.

## Links

`ui/lib/live-call.ts`, `brain/app/api/voice.py`.
