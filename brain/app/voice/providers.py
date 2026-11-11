"""Speech providers behind the STT / TTS / VAD ports.

Local testing : GroqSTT (Whisper API, fast, uses the same Groq key) + EdgeTTS (free, unofficial - dev only)
Production    : SarvamSTT + SarvamTTS (Indian languages, India-hosted)
Every adapter keeps one warm HTTP connection pool for low latency.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
import numpy as np

from app.domain import Transcript
from app.voice.audio import StreamingDecoder, strip_wav_header, to_wav

# Words Whisper should spell correctly (it uses this as context, not as an instruction). See app/voice/vocab.py.
from app.voice.vocab import hint_for  # noqa: E402
_WHISPER_LANG = {"english": "en", "hindi": "hi", "en": "en", "hi": "hi"}


class STTError(RuntimeError):
    pass


class GroqSTT:
    name = "groq-whisper"

    def __init__(self, api_key: str, base_url: str = "https://api.groq.com/openai/v1",
                 model: str = "whisper-large-v3-turbo", timeout_s: float = 15.0, vocabulary: str = "base"):
        if not api_key:
            raise STTError("GROQ_API_KEY is required for STT_PROVIDER=groq")
        self.model, self.hint = model, hint_for(vocabulary)
        self._client = httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0))

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        start = time.perf_counter()
        for attempt in range(3):
            r = await self._client.post("/audio/transcriptions",
                                        files={"file": ("audio.wav", to_wav(pcm16, rate), "audio/wav")},
                                        data={"model": self.model, "response_format": "verbose_json",
                                              "temperature": "0", **({"prompt": self.hint} if self.hint else {})})
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise STTError(f"Groq STT HTTP {r.status_code}: {r.text[:200]}")
            break
        body = r.json()
        segments = body.get("segments") or []
        no_speech = max((s.get("no_speech_prob", 0.0) for s in segments), default=0.0) if segments else 0.0
        if segments:  # weight by duration: one silent segment shouldn't flag a long real utterance
            total = sum(max(s["end"] - s["start"], 0.01) for s in segments)
            no_speech = sum(s.get("no_speech_prob", 0.0) * max(s["end"] - s["start"], 0.01) for s in segments) / total
        confidence = (sum(s.get("avg_logprob", 0.0) * max(s["end"] - s["start"], 0.01) for s in segments)
                      / sum(max(s["end"] - s["start"], 0.01) for s in segments)) if segments else None
        return Transcript(text=(body.get("text") or "").strip(),
                          language=_WHISPER_LANG.get((body.get("language") or "").lower(), "other"),
                          no_speech=float(no_speech), latency_ms=round((time.perf_counter() - start) * 1000),
                          confidence=confidence, compression=max((s.get("compression_ratio", 0.0) for s in segments), default=0.0))

    async def close(self):
        await self._client.aclose()


class SarvamSTT:
    name = "sarvam-stt"

    def __init__(self, api_key: str, model: str = "saaras:v3", timeout_s: float = 15.0):
        if not api_key:
            raise STTError("SARVAM_API_KEY is required for STT_PROVIDER=sarvam")
        self.model = model
        self._client = httpx.AsyncClient(base_url="https://api.sarvam.ai", headers={"api-subscription-key": api_key},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0))

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        start = time.perf_counter()
        data = {"model": self.model, "language_code": "unknown"}
        if self.model.startswith("saaras:v3"):
            data["mode"] = "codemix"  # Hinglish stays Hinglish
        r = await self._client.post("/speech-to-text", files={"file": ("audio.wav", to_wav(pcm16, rate), "audio/wav")},
                                    data=data)
        if r.status_code >= 400:
            raise STTError(f"Sarvam STT HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        code = (body.get("language_code") or "")[:2].lower() or None
        return Transcript(text=(body.get("transcript") or "").strip(),
                          language={"en": "en", "hi": "hi"}.get(code, "other" if code else None),
                          no_speech=0.0, latency_ms=round((time.perf_counter() - start) * 1000))

    async def close(self):
        await self._client.aclose()


class EdgeTTS:
    """Microsoft Edge read-aloud voices: free and good for development, but unofficial - not for production."""

    name = "edge-tts"

    def __init__(self, voices: dict[str, str], rate: int, speed_pct: int = 0):
        self.voices, self.rate = voices, rate
        self.speed = f"{speed_pct:+d}%"

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        import edge_tts

        decoder = StreamingDecoder("mp3", self.rate)
        communicate = edge_tts.Communicate(text, self.voices.get(language, self.voices["en"]), rate=self.speed)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                pcm = decoder.feed(chunk["data"])
                if pcm:
                    yield pcm
        tail = decoder.flush()
        if tail:
            yield tail


class SarvamTTS:
    """Sarvam Bulbul over HTTP streaming: raw PCM arrives while it is being synthesised."""

    name = "sarvam-tts"
    _LANG = {"en": "en-IN", "hi": "hi-IN", "hinglish": "hi-IN"}

    def __init__(self, api_key: str, rate: int, model: str = "bulbul:v3", speaker: str = "priya", timeout_s: float = 15.0,
                 pace: float = 1.0):
        if not api_key:
            raise ValueError("SARVAM_API_KEY is required for TTS_PROVIDER=sarvam")
        self.rate, self.model, self.speaker, self.pace = rate, model, speaker, pace
        self._client = httpx.AsyncClient(base_url="https://api.sarvam.ai",
                                         headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0))

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        body = {"text": text, "language_code": self._LANG.get(language, "en-IN"), "speaker": self.speaker,
                "model": self.model, "output_audio_codec": "linear16", "speech_sample_rate": self.rate, "pace": self.pace,
                "enable_preprocessing": True}
        async with self._client.stream("POST", "/text-to-speech/stream", json=body) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"Sarvam TTS HTTP {r.status_code}: {(await r.aread())[:200]!r}")
            first, carry = True, b""
            async for chunk in r.aiter_bytes():
                if first:
                    chunk, first = strip_wav_header(chunk) if chunk[:4] == b"RIFF" else chunk, False
                chunk = carry + chunk
                usable = len(chunk) - (len(chunk) % 2)  # keep 16-bit samples whole across chunks
                carry = chunk[usable:]
                if usable:
                    yield chunk[:usable]

    async def close(self):
        await self._client.aclose()


class SileroVAD:
    """Silero VAD (bundled model, no download). ~1 ms per 32 ms frame on CPU. One instance per call."""

    frame_samples = 512  # at 16 kHz

    def __init__(self, rate: int = 16000):
        if rate != 16000:
            raise ValueError("SileroVAD expects 16 kHz audio")
        import torch
        from silero_vad import load_silero_vad

        # Note: never call torch.set_num_threads() here - it is process-wide and would throttle the
        # embedder and reranker. The VAD model is tiny (~3 ms per 32 ms frame) either way.
        self._torch = torch
        self._model = load_silero_vad()
        self.rate = rate

    def prob(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        with self._torch.no_grad():
            return float(self._model(self._torch.from_numpy(samples), self.rate).item())

    def reset(self) -> None:
        self._model.reset_states()
