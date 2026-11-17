// Play call audio so the browser's echo canceller can remove it from the microphone.
//
// Why: Chrome's echo canceller only subtracts audio that arrives over a WebRTC peer connection. Audio played
// through Web Audio (our playback worklet -> ctx.destination) is invisible to it, so the speakers' sound leaks
// back into the microphone (the agent hears itself and "interrupts" itself). Firefox and Safari already cancel
// all audio the browser plays; the loopback below is harmless there.
//
// Fix: send the playback through two peer connections inside this page (a loopback, no server, no STUN) and
// play the received stream from an <audio> element. The browser now treats it as a remote participant's voice
// and cancels it. Built into every browser: no new library.
//
// If the loopback cannot connect, we fall back to plain Web Audio output (the old behaviour); the server's
// barge-in checks (grace period, higher threshold, echo-text match) still apply in both cases.

export type OutputMode = "loopback" | "direct";

export type EchoSafeOutput = { mode: OutputMode; close: () => void };

const CONNECT_TIMEOUT_MS = 3000;

function connected(pc: RTCPeerConnection): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("loopback timeout")), CONNECT_TIMEOUT_MS);
    const check = () => {
      if (pc.connectionState === "connected") {
        clearTimeout(timer);
        resolve();
      } else if (pc.connectionState === "failed" || pc.connectionState === "closed") {
        clearTimeout(timer);
        reject(new Error(`loopback ${pc.connectionState}`));
      }
    };
    pc.addEventListener("connectionstatechange", check);
    check();
  });
}

/** Route `node` (the playback worklet) to the speakers in a way the echo canceller can see. */
export async function echoSafeOutput(ctx: AudioContext, node: AudioNode): Promise<EchoSafeOutput> {
  const send = new RTCPeerConnection();
  const recv = new RTCPeerConnection();
  const audio = new Audio();
  const close = () => {
    audio.pause();
    audio.srcObject = null;
    send.close();
    recv.close();
  };
  try {
    const dest = ctx.createMediaStreamDestination();
    node.connect(dest);
    send.onicecandidate = (e) => e.candidate && recv.addIceCandidate(e.candidate).catch(() => {});
    recv.onicecandidate = (e) => e.candidate && send.addIceCandidate(e.candidate).catch(() => {});
    const remote = new Promise<MediaStream>((resolve) => {
      recv.ontrack = (e) => {
        try {
          (e.receiver as RTCRtpReceiver & { jitterBufferTarget?: number }).jitterBufferTarget = 0; // keep barge-in fast
        } catch {
          /* older browsers: default buffer */
        }
        resolve(e.streams[0] ?? new MediaStream([e.track]));
      };
    });
    for (const track of dest.stream.getAudioTracks()) send.addTrack(track, dest.stream);
    const offer = await send.createOffer();
    await send.setLocalDescription(offer);
    await recv.setRemoteDescription(offer);
    const answer = await recv.createAnswer();
    await recv.setLocalDescription(answer);
    await send.setRemoteDescription(answer);
    audio.srcObject = await remote;
    await connected(send);
    await audio.play();
    return { mode: "loopback", close };
  } catch (err) {
    console.warn("Echo-safe audio output unavailable, playing directly:", err);
    close();
    node.disconnect();
    node.connect(ctx.destination);
    return { mode: "direct", close: () => {} };
  }
}
