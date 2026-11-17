// A live, two-way call over the brain's WebSocket (/v1/voice/stream).
//
// Microphone -> browser echo cancellation + noise suppression -> capture worklet (16 kHz PCM16, 20 ms frames,
// optional "focus on my voice" gate) -> server. Server audio (PCM16) -> playback worklet -> echo-safe output
// (a WebRTC loopback, so the echo canceller removes the agent's voice from the mic) -> speakers.
// The server decides turn-taking and barge-in; on {"type":"clear"} we drop any agent audio not yet played.

import { wsUrl } from "./api";
import { echoSafeOutput, type EchoSafeOutput } from "./echo-safe-output";
import { CAPTURE, moduleUrl, PLAYBACK } from "./worklets";

export type CallEvent = { type: string; [key: string]: unknown };

const RATE = 16000;

export class LiveCall {
  private ws?: WebSocket;
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private player?: AudioWorkletNode;
  private output?: EchoSafeOutput;

  constructor(private onEvent: (event: CallEvent) => void) {}

  async start(sessionId: string, language: string, focus: boolean) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext({ sampleRate: RATE }); // the browser resamples the mic to 16 kHz for us
    for (const source of [CAPTURE, PLAYBACK]) {
      const url = moduleUrl(source);
      await this.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
    }
    this.player = new AudioWorkletNode(this.ctx, "playback");
    this.output = await echoSafeOutput(this.ctx, this.player); // lets the echo canceller hear what we play
    const aec = this.stream.getAudioTracks()[0]?.getSettings().echoCancellation;
    if (aec === false || this.output.mode === "direct") {
      this.onEvent({ type: "echo_warning", reason: aec === false ? "echo cancellation is off" : "echo-safe playback unavailable" });
    }
    this.capture = new AudioWorkletNode(this.ctx, "capture", { processorOptions: { focus } });
    this.ctx.createMediaStreamSource(this.stream).connect(this.capture); // not routed to the speakers

    const ws = new WebSocket(wsUrl("/v1/voice/stream"));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "start", session_id: sessionId, language, greet: true }));
    ws.onmessage = (msg) => {
      if (typeof msg.data !== "string") {
        this.player?.port.postMessage(msg.data, [msg.data as ArrayBuffer]);
        return;
      }
      const event = JSON.parse(msg.data) as CallEvent;
      if (event.type === "clear") this.player?.port.postMessage({ clear: true }); // caller interrupted
      this.onEvent(event);
    };
    ws.onclose = () => {
      this.onEvent({ type: "closed" });
      this.release();
    };
    this.capture.port.onmessage = (msg) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(msg.data as ArrayBuffer);
    };
  }

  /** Keypad code for verification: sent as data, never as speech. */
  sendCode(code: string) {
    this.ws?.send(JSON.stringify({ type: "otp", code }));
  }

  sendText(text: string) {
    this.ws?.send(JSON.stringify({ type: "text", text }));
  }

  setFocus(on: boolean) {
    this.capture?.port.postMessage({ focus: on });
  }

  stop() {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" }));
    this.ws?.close();
    this.release();
  }

  private release() {
    this.output?.close();
    this.output = undefined;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    this.stream = undefined;
    this.ctx = undefined;
  }
}
