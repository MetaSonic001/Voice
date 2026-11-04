// Audio worklets (they run on the browser's audio thread). Kept as source strings and loaded from Blob URLs,
// so loading never depends on a network fetch or on where the app is hosted.

export const CAPTURE = `// Microphone -> 20 ms frames of 16-bit PCM for the server (runs on the browser's audio thread).
//
// "Focus on my voice": the caller is closest to the microphone, so their speech is much louder than people
// talking in the background. We learn the caller's speech level and the room's noise floor, and send silence
// for frames that are well below the caller's level (distant voices, TV). A short hangover keeps soft word
// endings. The server's VAD and turn-taking rules then never see the background voices.
const FRAME = 320; // 20 ms at 16 kHz

class Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buf = new Float32Array(FRAME);
    this.n = 0;
    this.focus = !!options.processorOptions?.focus;
    this.floor = 0.002; // room noise level (RMS)
    this.voice = 0; // the caller's speech level (RMS), learned
    this.hangover = 0; // frames to keep open after loud speech
    this.port.onmessage = (e) => {
      if ("focus" in e.data) this.focus = !!e.data.focus;
    };
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === FRAME) {
        this.send();
        this.n = 0;
      }
    }
    return true;
  }

  send() {
    let sum = 0;
    for (let i = 0; i < FRAME; i++) sum += this.buf[i] * this.buf[i];
    const rms = Math.sqrt(sum / FRAME);
    if (rms < this.floor * 3) this.floor = 0.95 * this.floor + 0.05 * rms; // adapt to the room when quiet
    else if (rms > this.voice * 0.5) this.voice = this.voice ? 0.9 * this.voice + 0.1 * rms : rms; // caller speech
    this.voice *= 0.9995; // relax slowly (~2.5%/s), so a caller who starts speaking softer isn't cut off
    const threshold = Math.max(this.floor * 3, this.voice * 0.25); // ~12 dB below the caller
    if (rms >= threshold) this.hangover = 10; // 200 ms
    const pass = !this.focus || this.hangover-- > 0;

    const pcm = new Int16Array(FRAME);
    if (pass) {
      for (let i = 0; i < FRAME; i++) {
        const s = Math.max(-1, Math.min(1, this.buf[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
  }
}

registerProcessor("capture", Capture);
`;

export const PLAYBACK = `// Agent audio (16-bit PCM from the server) -> speakers, gapless. {clear: true} drops what hasn't played yet.
class Playback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.port.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        const pcm = new Int16Array(e.data);
        const f = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
        this.queue.push(f);
      } else if (e.data?.clear) {
        this.queue = [];
        this.offset = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    let i = 0;
    while (i < out.length && this.queue.length) {
      const head = this.queue[0];
      const take = Math.min(out.length - i, head.length - this.offset);
      out.set(head.subarray(this.offset, this.offset + take), i);
      i += take;
      this.offset += take;
      if (this.offset === head.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    out.fill(0, i);
    return true;
  }
}

registerProcessor("playback", Playback);
`;

export const moduleUrl = (source: string) => URL.createObjectURL(new Blob([source], { type: "application/javascript" }));
