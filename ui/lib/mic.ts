// Open the microphone with the browser's standard call processing, and report what the browser actually applied
// (asking for a setting does not mean we got it).
//
// Settings: echo cancellation, noise suppression and auto gain on, mono. These are the browser defaults that web
// calling apps run with. In Chrome, echo cancellation removes audio from WebRTC peer connections, which is why the
// agent's voice plays through a WebRTC loopback (echo-safe-output.ts).
//
// Checked and not used (17 Nov 2026):
// - echoCancellation "all" (Chrome 141+): also removes audio from other apps. It brings nothing here, because the
//   agent's voice is the only thing we play and it is already covered. Which canceller Chrome uses for it on Windows
//   is not documented, and Chrome's own tests of the Windows system canceller were weak.
// - autoGainControl off: no source shows it reduces echo, and on a laptop mic it can leave the caller too quiet for the
//   speech detector.

export type MicReport = {
  device: string;
  echoCancellation: string; // what the browser applied: "true" | "false" | "remote-only" | "all" | "unknown"
  offered: string; // echo-cancellation values the browser offers for this mic
  autoGainControl: string;
  noiseSuppression: string;
};

const shown = (v: unknown) => (v === undefined ? "unknown" : String(v));

const REQUESTED: MediaTrackConstraints = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };

export async function openMic(): Promise<{ stream: MediaStream; report: MicReport }> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: REQUESTED });
  const track = stream.getAudioTracks()[0];
  const s = (track?.getSettings() ?? {}) as Record<string, unknown>;
  const caps = (track?.getCapabilities?.() ?? {}) as Record<string, unknown>;
  const report: MicReport = {
    device: track?.label || "microphone",
    echoCancellation: shown(s.echoCancellation),
    offered: Array.isArray(caps.echoCancellation) ? (caps.echoCancellation as unknown[]).map(String).join(", ") : "not listed",
    autoGainControl: shown(s.autoGainControl),
    noiseSuppression: shown(s.noiseSuppression),
  };
  // What we asked for, what this mic supports, and what the browser applied. "echoCancellation: true" only says the
  // canceller is switched on; how much echo it removes is measured from call recordings.
  console.info("Microphone", { requested: REQUESTED, capabilities: caps, settings: s });
  return { stream, report };
}
