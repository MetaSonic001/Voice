// The agent desk joining a live call the AI handed over (/v1/desk/stream): the person's microphone goes to the
// caller, the caller's audio comes back. Same audio worklets as the test console's live call.

import { wsUrl } from "./api";
import { CAPTURE, moduleUrl, PLAYBACK } from "./worklets";

const RATE = 16000;

export class DeskCall {
  private ws?: WebSocket;
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private player?: AudioWorkletNode;
  muted = false;

  constructor(private onEnd: () => void) {}

  async join(handoffId: string, agent: string) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext({ sampleRate: RATE });
    for (const source of [CAPTURE, PLAYBACK]) {
      const url = moduleUrl(source);
      await this.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
    }
    this.player = new AudioWorkletNode(this.ctx, "playback");
    this.player.connect(this.ctx.destination);
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
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    this.stream = undefined;
    this.ctx = undefined;
  }
}
