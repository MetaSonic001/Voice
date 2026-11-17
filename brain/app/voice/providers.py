"""Speech providers behind the STT / TTS / VAD ports.

Local testing : GroqSTT (Whisper API, fast, uses the same Groq key) + EdgeTTS (free, unofficial - dev only)
Sarvam        : SarvamSTT + SarvamTTS (Indian languages, India-hosted; paid, the production choice)
Every adapter keeps one warm HTTP connection pool for low latency. Another vendor = one more class with the same
`transcribe` / `stream` method, picked in container.build_voice.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx
import numpy as np

from app.domain import Transcript
from app.voice.audio import StreamingDecoder, to_wav

# Words Whisper should spell correctly (it uses this as context, not as an instruction). See app/voice/vocab.py.
from app.voice.vocab import hint_for  # noqa: E402
_WHISPER_LANG = {"english": "en", "hindi": "hi", "en": "en", "hi": "hi"}
_RETRY_STATUS = (429, 500, 502, 503)
# Keep idle connections for 5 minutes (httpx's default is 5 s, so most turns of a call paid a fresh TLS handshake:
# 100-250 ms). Same as the LLM client (adapters/llm.py).
KEEP_ALIVE = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=300)


async def _open_connection(client: httpx.AsyncClient, path: str) -> None:
    """Open (or refresh) the pooled TLS connection with a tiny free request, so the caller's first turn doesn't pay
    for the handshake. Any reply will do; failures are ignored (the real request will report them)."""
    try:
        await client.get(path, timeout=3.0)
    except Exception:
        pass


class SpeechError(RuntimeError):
    """A speech provider failed. `out_of_credit` marks failures that retrying will not fix (no credits left,
    wrong or disabled key); `retryable` marks the brief ones (rate limit, provider busy)."""

    # Sarvam error codes that mean "top up or fix the key", even when sent with HTTP 429 (docs: errors-troubleshooting)
    NO_CREDIT_CODES = {"insufficient_quota_error", "seat_budget_exhausted_error", "invalid_api_key_error"}

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code

    @property
    def out_of_credit(self) -> bool:
        return self.status in (401, 402, 403) or self.code in self.NO_CREDIT_CODES

    @property
    def retryable(self) -> bool:
        return self.status in _RETRY_STATUS and not self.out_of_credit


class STTError(SpeechError):
    pass


class TTSError(SpeechError):
    pass


class GroqSTT:
    name = "groq-whisper"

    def __init__(self, api_key: str, base_url: str = "https://api.groq.com/openai/v1",
                 model: str = "whisper-large-v3-turbo", timeout_s: float = 15.0, vocabulary: str = "base"):
        if not api_key:
            raise STTError("GROQ_API_KEY is required for STT_PROVIDER=groq")
        self.model, self.hint = model, hint_for(vocabulary)
        self._client = httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0), limits=KEEP_ALIVE)

    async def warm(self) -> None:
        await _open_connection(self._client, "/models")

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


# -- Sarvam (https://docs.sarvam.ai) --
# One API key, sent as the `api-subscription-key` header. Errors come back as {"error": {"message", "code"}}.
# 402 = no credits, 403 = bad key: never retried. 429/5xx: retried briefly. Prices (Nov 2026, pay as you go): STT
# Rs 0.49 a minute (v3 and v4 alike); TTS per 1,000 characters Rs 2 for bulbul:v4-flash, Rs 3 for bulbul:v3.
SARVAM_URL = "https://api.sarvam.ai"


def _sarvam_error(kind: type[SpeechError], what: str, status: int, body: bytes | str) -> SpeechError:
    """Turn a Sarvam error response into a SpeechError. Only the provider's own error text is kept (Sarvam's
    error bodies do not echo the audio or the text that was sent)."""
    code, message = None, body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        err = json.loads(message).get("error") or {}
        code, message = err.get("code"), err.get("message") or message
    except (ValueError, AttributeError):
        pass
    return kind(f"{what} HTTP {status}{f' {code}' if code else ''}: {message[:200]}", status, code)


class SarvamSTT:
    """Sarvam Saaras, REST: one request per finished utterance (the live WebSocket stream is a production item,
    pending-work B2.6). `codemix` mode keeps Hinglish as Hinglish (English words in Latin script)."""

    name = "sarvam-stt"
    max_seconds = 29.5  # the REST endpoint takes up to 30 s of audio per request

    def __init__(self, api_key: str, model: str = "saaras:v4", mode: str = "codemix", timeout_s: float = 15.0,
                 keyterms: list[str] | None = None):
        if not api_key:
            raise STTError("SARVAM_API_KEY is required for STT_PROVIDER=sarvam")
        self.model, self.mode = model, mode
        # words to spell right (product names, banking terms); saaras:v4 only, at most 50 of up to 64 characters
        self.keyterms = [t[:64] for t in (keyterms or [])][:50] if model.startswith("saaras:v4") else []
        self._client = httpx.AsyncClient(base_url=SARVAM_URL, headers={"api-subscription-key": api_key},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0), limits=KEEP_ALIVE)

    async def warm(self) -> None:
        await _open_connection(self._client, "/")

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        start = time.perf_counter()
        wav = to_wav(pcm16[: int(self.max_seconds * rate) * 2], rate)
        data = {"model": self.model, "language_code": "unknown", **({"mode": self.mode} if self.mode else {}),
                **({"keyterms": json.dumps(self.keyterms, ensure_ascii=False)} if self.keyterms else {})}
        for attempt in range(3):
            r = await self._client.post("/speech-to-text", files={"file": ("audio.wav", wav, "audio/wav")}, data=data)
            if r.status_code < 400:
                break
            err = _sarvam_error(STTError, "Sarvam STT", r.status_code, r.text)
            if not err.retryable or attempt == 2:
                raise err
            await asyncio.sleep(0.3 * (attempt + 1))
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


# Our default bulbul:v4-flash voices: the same woman (Ishita) in each reply language, customer-care / banking style.
V4_SPEAKERS = {"en": "ishita_en_customer", "hi": "ishita_hi_banking", "hinglish": "ishita_enhi_customer"}


class SarvamTTS:
    """Sarvam Bulbul over HTTP streaming (POST /text-to-speech/stream): audio arrives while it is being made.
    We ask for raw 16-bit PCM; the reply is accepted as raw PCM, WAV or MP3 (the docs don't fix which).

    `speakers` maps our reply language (en / hi / hinglish) to a voice. bulbul:v4-flash voices are personas tied to a
    language and style (ishita_en_customer); bulbul:v3 / v2 voices are bare names (priya). Mixing them up makes every
    request fail with HTTP 400, so it is refused at startup."""

    name = "sarvam-tts"
    _LANG = {"en": "en-IN", "hi": "hi-IN", "hinglish": "hi-IN"}  # the script drives pronunciation; Hinglish is Latin script

    def __init__(self, api_key: str, rate: int, model: str = "bulbul:v4-flash", speakers: dict[str, str] | None = None,
                 timeout_s: float = 15.0, pace: float = 1.0):
        if not api_key:
            raise TTSError("SARVAM_API_KEY is required for TTS_PROVIDER=sarvam")
        self.rate, self.model = rate, model
        persona = model.startswith("bulbul:v4")
        self.speakers = speakers or (dict(V4_SPEAKERS) if persona else {"en": "priya"})
        for language, speaker in self.speakers.items():
            if persona != ("_" in speaker):
                need = "a persona ID such as ishita_en_customer (SARVAM_V4_SPEAKERS)" if persona else \
                    "a bare voice name such as priya (SARVAM_SPEAKER)"
                raise TTSError(f"{model} needs {need}; got '{speaker}' for {language}")
        self.pace = min(max(pace, 0.5), 2.0)  # v3 and v4-flash accept 0.5-2.0
        self._client = httpx.AsyncClient(base_url=SARVAM_URL,
                                         headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                                         timeout=httpx.Timeout(timeout_s, connect=5.0), limits=KEEP_ALIVE)

    @property
    def voice_tag(self) -> str:
        """Everything that changes how a phrase sounds; the phrase cache on disk is keyed by it."""
        voices = ",".join(f"{k}:{v}" for k, v in sorted(self.speakers.items()))
        return f"{self.name}|{self.model}|{voices}|{self.pace}|{self.rate}"

    def speaker(self, language: str) -> str:
        return self.speakers.get(language) or self.speakers.get("en") or next(iter(self.speakers.values()))

    async def warm(self) -> None:
        await _open_connection(self._client, "/")

    def body(self, text: str, language: str) -> dict:
        body = {"text": text, "language_code": self._LANG.get(language, "en-IN"), "speaker": self.speaker(language),
                "model": self.model, "pace": self.pace, "speech_sample_rate": self.rate, "output_audio_codec": "linear16"}
        if self.model.startswith("bulbul:v2"):  # v3 and later always normalise numbers/dates and reject this flag
            body["enable_preprocessing"] = True
        return body

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        body = self.body(text, language)
        for attempt in range(3):  # retried only before any audio was passed on
            async with self._client.stream("POST", "/text-to-speech/stream", json=body) as r:
                if r.status_code < 400:
                    async for pcm in pcm_stream(r.aiter_bytes(), r.headers.get("content-type", ""), self.rate):
                        yield pcm
                    return
                err = _sarvam_error(TTSError, "Sarvam TTS", r.status_code, await r.aread())
            if not err.retryable or attempt == 2:
                raise err
            await asyncio.sleep(0.3 * (attempt + 1))

    async def close(self):
        await self._client.aclose()


async def pcm_stream(chunks: AsyncIterator[bytes], content_type: str, rate: int) -> AsyncIterator[bytes]:
    """A streamed TTS reply -> PCM16 mono pieces. Handles raw PCM, a WAV header in front (its size fields may be 0
    while streaming, so only the 'data' marker is looked for) and MP3. Samples are kept whole across pieces."""
    head, decoder, carry = b"", None, b""
    async for chunk in chunks:
        if head is not None:  # still deciding what the stream is
            head += chunk
            if len(head) < 12:
                continue
            if "mpeg" in content_type or "mp3" in content_type or head[:3] == b"ID3":
                decoder = StreamingDecoder("mp3", rate)
                chunk = head
            elif head[:4] == b"RIFF":
                at = head.find(b"data", 12)
                if at < 0 or len(head) < at + 8:
                    continue  # the header isn't complete yet
                chunk = head[at + 8:]
            else:
                chunk = head
            head = None
        if decoder:
            pcm = decoder.feed(chunk)
        else:
            chunk = carry + chunk
            usable = len(chunk) - (len(chunk) % 2)
            pcm, carry = chunk[:usable], chunk[usable:]
        if pcm:
            yield pcm
    if head:  # a very short reply (< 12 bytes): treat it as raw PCM
        usable = len(head) - (len(head) % 2)
        if usable:
            yield head[:usable]
    if decoder:
        tail = decoder.flush()
        if tail:
            yield tail


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
