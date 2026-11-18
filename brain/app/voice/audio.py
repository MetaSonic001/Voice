"""Audio helpers. Internally everything is mono 16-bit little-endian PCM at one rate (default 16 kHz)."""

import io
import math
import wave

import numpy as np

BYTES_PER_SAMPLE = 2


def duration_ms(pcm: bytes, rate: int) -> float:
    return len(pcm) / BYTES_PER_SAMPLE / rate * 1000


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(math.sqrt(float(np.mean(samples * samples))))


def to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(BYTES_PER_SAMPLE)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def decode(data: bytes, rate: int) -> bytes:
    """Any audio file (wav, mp3, ogg, webm, m4a, flac...) -> mono PCM16 at `rate`."""
    import av

    out = bytearray()
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
        for resampled in resampler.resample(None):
            out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
    return bytes(out)


class StreamingDecoder:
    """Incremental decoder for compressed audio streams (e.g. MP3 from a TTS) -> PCM16 mono at `rate`."""

    def __init__(self, codec: str, rate: int):
        import av

        self._ctx = av.CodecContext.create(codec, "r")
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for packet in self._ctx.parse(data):
            for frame in self._ctx.decode(packet):
                for resampled in self._resampler.resample(frame):
                    out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
        return bytes(out)

    def flush(self) -> bytes:
        out = bytearray()
        for packet in self._ctx.parse(b""):
            for frame in self._ctx.decode(packet):
                for resampled in self._resampler.resample(frame):
                    out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
        try:
            for frame in self._ctx.decode(None):
                for resampled in self._resampler.resample(frame):
                    out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
        except Exception:  # some codecs raise on a second flush; nothing left to decode
            pass
        for resampled in self._resampler.resample(None):
            out += bytes(resampled.planes[0])[: resampled.samples * BYTES_PER_SAMPLE]
        return bytes(out)


def tone(ms: float, rate: int, freq: float = 220.0, amplitude: int = 6000) -> bytes:
    """Synthetic test signal (used by tests and diagnostics)."""
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()


def silence(ms: float, rate: int) -> bytes:
    return b"\x00\x00" * int(rate * ms / 1000)
