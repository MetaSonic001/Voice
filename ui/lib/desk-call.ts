// The agent desk joining a live call the AI handed over (/v1/desk/stream): the person's microphone goes to the
// caller, the caller's audio comes back. Same audio worklets as the test console's live call.

import { wsUrl } from "./api";
import { echoSafeOutput, type EchoSafeOutput } from "./echo-safe-output";
import { openMic } from "./mic";
import { CAPTURE, moduleUrl, PLAYBACK } from "./worklets";

const RATE = 16000;

export class DeskCall {
  private ws?: WebSocket;
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private player?: AudioWorkletNode;
  private output?: EchoSafeOutput;
  muted = false;

  constructor(private onEnd: () => void) {}

  async join(handoffId: string, agent: string) {
    try {
      await this.open(handoffId, agent);
    } catch (err) {
      this.ws?.close();
      this.release(); // setup failed after the mic opened: don't leave it on
      throw err;
    }
  }

  private async open(handoffId: string, agent: string) {
    this.stream = (await openMic()).stream; // same processing as before; see mic.ts
    this.ctx = new AudioContext({ sampleRate: RATE });
    for (const source of [CAPTURE, PLAYBACK]) {
      const url = moduleUrl(source);
      await this.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
    }
    this.player = new AudioWorkletNode(this.ctx, "playback");
    this.output = await echoSafeOutput(this.ctx, this.player); // lets the echo canceller hear what we play
    this.capture = new AudioWorkletNode(this.ctx, "capture", { processorOptions: { focus: false } });
    this.ctx.createMediaStreamSource(this.stream).connect(this.capture);

    const base = wsUrl("/v1/desk/stream");
    const ws = new WebSocket(`${base}${base.includes("?") ? "&" : "?"}handoff_id=${handoffId}&agent=${encodeURIComponent(agent)}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (msg) => {
      if (typeof msg.data !== "string") this.player?.port.postMessage(msg.data, [msg.data as ArrayBuffer]);
    };
    ws.onclose = () => {
      this.release();
      this.onEnd();
    };
    this.capture.port.onmessage = (msg) => {
      if (ws.readyState === WebSocket.OPEN && !this.muted) ws.send(msg.data as ArrayBuffer);
    };
  }

  hangUp() {
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
