# ADR-0012: Browser test calls: WebSocket audio to the brain, WebRTC loopback for echo

- **Status:** Accepted
- **Date:** 2026-11-04 (transport); 2026-11-05 (echo: earphones); 2026-11-12 (echo: WebRTC loopback)
- **Deciders:** project owner
- **Merged on 14 Nov 2026:** this file now also holds the former ADR 0013 (speaker echo not fixed, superseded) and ADR 0028 (the loopback fix). Those numbers are not reused.

## Context

Developers and demo audiences talk to the agent from a browser. Production calls will come over the phone network (Cisco: SIP/RTP, usually 8 kHz), not from a browser. Phone providers typically stream call audio to applications over a plain WebSocket.

On laptop speakers the agent heard its own voice and interrupted itself ("One moment, please" turned up as caller speech in 3 recorded calls), even with `echoCancellation: true` on the mic. Cause: Chrome's echo canceller only subtracts audio that arrives over a WebRTC peer connection; our agent audio played through Web Audio (`AudioContext.destination`), which the canceller can't see. Firefox and Safari cancel all browser audio. Phone lines cancel echo in the handset and network, so this only affects the browser.

## Decision

1. **Transport:** the browser captures the mic with Web Audio (AudioWorklet) and sends **16 kHz PCM over a WebSocket**; agent audio comes back the same way. The voice session only takes audio frames, so production adds a **phone transport adapter** (media gateway → voice session) and the engine stays the same.
2. **Playback:** the agent's audio plays through a **loopback of two RTCPeerConnections inside the same page** and an `<audio>` element (`ui/lib/echo-safe-output.ts`). Chrome then treats it as a remote voice and removes it from the mic. If the loopback can't connect in 3 s, playback falls back to plain Web Audio and the console warns the caller to use headphones.
3. The server's echo checks (grace period, stricter threshold while the agent talks, echo-text match) stay as the backup.

**History:** on 5 Nov the owner first decided not to fix the echo and to use earphones (former ADR 0013). On 12 Nov the owner asked for a proper fix, and the loopback replaced that decision (former ADR 0028).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **WebSocket + PCM transport** | No media server, no STUN/TURN, no codec work; same shape as phone-provider streams | No packet-loss handling (fine on localhost) |
| Server-side WebRTC (aiortc / LiveKit) | Echo cancellation, jitter handling, NAT traversal | A media server and a large dependency for a dev console; phone calls don't use it |
| **WebRTC loopback in the page, for playback only** | Same echo removal as a real WebRTC call; built into every browser; no library, no server; ~46 ms extra buffering | Browser-only benefit; one more part in the audio path |
| Earphones only, no code (the 5 Nov choice) | No work | Speaker demos misfire |
| Server-side echo text filter only | Also protects the phone path | Work for a problem phones don't have; kept only as a backup |
| Speakerphone mode (strict barge-in) | Safety net | Changes behaviour; more code |

## Consequences

- **Easier:** a simple dev setup; telephony plugs into the same engine; demos work on laptop speakers; the console and the `/desk` call share the fix.
- **Harder:** 16 kHz tests overstate phone-line STT accuracy (downsample to 8 kHz before Cisco work); one more moving part in the browser playback path (with a fallback and a warning).
- **Not verified on hardware by us:** the browser pane blocks the microphone. Pass mark: at least 15 dB of the agent's voice removed on a real laptop (the self-test doc that measured it is missing; see `context/pending-work.md` A4).
- **Production:** unaffected. Phone calls come through Cisco.
- **Revisit when:** AU wants a "call from the app or website" channel. Real customers on mobile data need full WebRTC transport.

## Links

`ui/lib/live-call.ts`, `ui/lib/echo-safe-output.ts`, `ui/lib/desk-call.ts`, `brain/app/api/voice.py`, `brain/app/voice/turntaking.py`.
