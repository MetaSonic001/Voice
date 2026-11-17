"""Sarvam speech adapters, the fallback to the free providers, and the phrase cache on disk.

Offline: Sarvam's HTTP API is faked with httpx.MockTransport, following docs.sarvam.ai (Nov 2026). No key, no credits.
"""

import json
import struct

import httpx
import pytest

from app.voice.fallback import FallbackSTT, FallbackTTS
from app.voice.phrases import PhraseCache
from app.voice.providers import SARVAM_URL, SarvamSTT, SarvamTTS, STTError, TTSError, pcm_stream

PCM = struct.pack("<8h", *range(8))  # 16 bytes, 8 samples


def _mock(client_owner, handler):
    """Point an adapter's HTTP client at a fake Sarvam (same headers, no network)."""
    old = client_owner._client
    client_owner._client = httpx.AsyncClient(base_url=SARVAM_URL, headers=old.headers,
                                             transport=httpx.MockTransport(handler))


def _error(status: int, code: str):
    return httpx.Response(status, json={"error": {"message": "nope", "code": code}})


async def _chunks(*parts: bytes):
    for p in parts:
        yield p


async def _collect(gen) -> bytes:
    return b"".join([c async for c in gen])


# ── speech to text ──

async def test_stt_sends_key_model_and_codemix_and_maps_language():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"], seen["key"] = request.url.path, request.headers["api-subscription-key"]
        seen["body"] = request.content
        return httpx.Response(200, json={"request_id": "r1", "transcript": " mera card block karo ",
                                         "language_code": "hi-IN", "language_probability": 0.9})

    stt = SarvamSTT("test-key")
    _mock(stt, handler)
    t = await stt.transcribe(PCM * 100, 16000)
    assert seen["path"] == "/speech-to-text" and seen["key"] == "test-key"
    for field in (b'name="model"', b"saaras:v4", b'name="mode"', b"codemix", b'name="language_code"', b"unknown",
                  b'filename="audio.wav"'):
        assert field in seen["body"]
    assert t.text == "mera card block karo" and t.language == "hi"


async def test_stt_v4_sends_key_terms_and_v3_does_not():
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(request.content)
        return httpx.Response(200, json={"transcript": "x", "language_code": "en-IN"})

    from app.voice.vocab import terms_for

    terms = terms_for("extended")
    assert 10 < len(terms) <= 50 and "Zenith+" in terms and "AU Small Finance Bank" in terms
    v4 = SarvamSTT("k", keyterms=terms)
    _mock(v4, handler)
    await v4.transcribe(PCM, 16000)
    assert v4.model == "saaras:v4" and b'name="keyterms"' in bodies[0] and "Zenith+".encode() in bodies[0]
    v3 = SarvamSTT("k", model="saaras:v3", keyterms=terms)
    _mock(v3, handler)
    await v3.transcribe(PCM, 16000)
    assert b"keyterms" not in bodies[1]  # v3 does not take key terms


async def test_warm_opens_a_connection_and_ignores_failures():
    seen = []
    stt, tts = SarvamSTT("k"), SarvamTTS("k", 16000)
    _mock(stt, lambda r: seen.append(r.url.path) or httpx.Response(404))
    _mock(tts, lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    await stt.warm()
    await tts.warm()  # no exception
    assert seen == ["/"]


async def test_stt_cuts_audio_to_the_30_second_limit():
    sizes = []

    def handler(request: httpx.Request):
        sizes.append(len(request.content))
        return httpx.Response(200, json={"transcript": "x", "language_code": "en-IN"})

    stt = SarvamSTT("k")
    _mock(stt, handler)
    await stt.transcribe(b"\0\0" * 16000 * 40, 16000)  # 40 s
    assert sizes[0] < 2 * 16000 * 30  # sent at most 29.5 s


async def test_stt_out_of_credits_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return _error(402, "insufficient_quota_error")

    stt = SarvamSTT("k")
    _mock(stt, handler)
    with pytest.raises(STTError) as e:
        await stt.transcribe(PCM, 16000)
    assert e.value.out_of_credit and not e.value.retryable and len(calls) == 1
    assert "insufficient_quota_error" in str(e.value)


async def test_stt_retries_a_rate_limit_but_not_an_exhausted_budget():
    replies = [_error(429, "rate_limit_exceeded_error"),
               httpx.Response(200, json={"transcript": "hello", "language_code": "en-IN"})]
    stt = SarvamSTT("k")
    _mock(stt, lambda r: replies.pop(0))
    assert (await stt.transcribe(PCM, 16000)).text == "hello"

    calls = []
    _mock(stt, lambda r: calls.append(1) or _error(429, "seat_budget_exhausted_error"))
    with pytest.raises(STTError) as e:
        await stt.transcribe(PCM, 16000)
    assert e.value.out_of_credit and len(calls) == 1


def test_missing_key_fails_at_startup():
    with pytest.raises(STTError):
        SarvamSTT("")
    with pytest.raises(TTSError):
        SarvamTTS("", 16000)


# ── text to speech ──

async def test_tts_request_body_follows_the_v3_docs():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, content=PCM, headers={"content-type": "application/octet-stream"})

    tts = SarvamTTS("k", 16000, pace=1.1)  # default: bulbul:v4-flash with the Ishita personas
    _mock(tts, handler)
    assert await _collect(tts.stream("Namaste ji", "hinglish")) == PCM
    body = seen["body"]
    assert seen["path"] == "/text-to-speech/stream"
    assert body["language_code"] == "hi-IN" and body["speaker"] == "ishita_enhi_customer"
    assert body["model"] == "bulbul:v4-flash"
    assert body["speech_sample_rate"] == 16000 and body["output_audio_codec"] == "linear16" and body["pace"] == 1.1
    assert "enable_preprocessing" not in body and "temperature" not in body
    assert tts.body("x", "en")["speaker"] == "ishita_en_customer" and tts.body("x", "other")["speaker"] == "ishita_en_customer"
    v3 = SarvamTTS("k", 16000, model="bulbul:v3", speakers={"en": "priya"})
    assert v3.body("x", "hi")["speaker"] == "priya" and "enable_preprocessing" not in v3.body("x", "en")
    assert SarvamTTS("k", 16000, model="bulbul:v2", speakers={"en": "anushka"}).body("x", "en")["enable_preprocessing"]
    assert SarvamTTS("k", 16000, pace=5).pace == 2.0


def test_speaker_names_must_fit_the_model():
    # v3 names on v4-flash (and v4 personas on v3) make every request fail with HTTP 400: refuse at startup instead
    with pytest.raises(TTSError, match="persona"):
        SarvamTTS("k", 16000, model="bulbul:v4-flash", speakers={"en": "priya"})
    with pytest.raises(TTSError, match="bare voice name"):
        SarvamTTS("k", 16000, model="bulbul:v3", speakers={"en": "ishita_en_customer"})


def test_voice_tag_changes_with_voice_model_and_pace():
    a = SarvamTTS("k", 16000).voice_tag
    assert a != SarvamTTS("k", 16000, pace=1.1).voice_tag
    assert a != SarvamTTS("k", 16000, speakers={"en": "neha_en_customer"}).voice_tag
    assert a != SarvamTTS("k", 16000, model="bulbul:v3", speakers={"en": "priya"}).voice_tag


async def test_tts_bad_key_raises_out_of_credit():
    tts = SarvamTTS("k", 16000)
    _mock(tts, lambda r: _error(403, "invalid_api_key_error"))
    with pytest.raises(TTSError) as e:
        await _collect(tts.stream("hi", "en"))
    assert e.value.out_of_credit


async def test_pcm_stream_keeps_samples_whole_across_odd_chunks():
    out = await _collect(pcm_stream(_chunks(PCM[:3], PCM[3:12], PCM[12:]), "", 16000))
    assert out == PCM


async def test_pcm_stream_strips_a_streaming_wav_header_split_over_chunks():
    # a streaming WAV header: size fields 0, an extra LIST chunk before 'data'
    header = (b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
              + b"LIST" + struct.pack("<I", 4) + b"INFO" + b"data" + struct.pack("<I", 0))
    raw = header + PCM
    out = await _collect(pcm_stream(_chunks(raw[:5], raw[5:30], raw[30:50], raw[50:]), "audio/wav", 16000))
    assert out == PCM


# ── fallback to the free providers ──

class _Speaker:
    def __init__(self, name, audio=b"", fail_with=None, fail_after_first=False):
        self.name, self.audio, self.fail_with, self.fail_after_first, self.calls = name, audio, fail_with, fail_after_first, 0

    async def stream(self, text, language):
        self.calls += 1
        if self.fail_with and not self.fail_after_first:
            raise self.fail_with
        yield self.audio
        if self.fail_with:
            raise self.fail_with


class _Listener:
    def __init__(self, name, fail_with=None):
        self.name, self.fail_with, self.calls = name, fail_with, 0

    async def transcribe(self, pcm, rate):
        from app.domain import Transcript

        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        return Transcript(self.name, "en", 0.0, 1)


async def test_tts_out_of_credit_switches_to_backup_until_restart():
    sarvam = _Speaker("sarvam-tts", fail_with=TTSError("no credits", 402, "insufficient_quota_error"))
    edge = _Speaker("edge-tts", audio=b"EDGE")
    tts = FallbackTTS(sarvam, edge)
    assert await _collect(tts.stream("a", "en")) == b"EDGE"
    assert tts.switched and tts.name == "edge-tts"
    assert await _collect(tts.stream("b", "en")) == b"EDGE"
    assert sarvam.calls == 1  # not asked again


async def test_tts_brief_failure_uses_backup_once():
    sarvam = _Speaker("sarvam-tts", fail_with=TTSError("busy", 503))
    tts = FallbackTTS(sarvam, _Speaker("edge-tts", audio=b"EDGE"))
    assert await _collect(tts.stream("a", "en")) == b"EDGE"
    assert not tts.switched
    await _collect(tts.stream("b", "en"))
    assert sarvam.calls == 2


async def test_tts_failure_mid_sentence_is_not_repeated_in_another_voice():
    sarvam = _Speaker("sarvam-tts", audio=b"HALF", fail_with=TTSError("dropped", 500), fail_after_first=True)
    edge = _Speaker("edge-tts", audio=b"EDGE")
    with pytest.raises(TTSError):
        await _collect(FallbackTTS(sarvam, edge).stream("a", "en"))
    assert edge.calls == 0


async def test_stt_falls_back_and_switches_on_bad_key():
    sarvam = _Listener("sarvam-stt", fail_with=STTError("bad key", 403, "invalid_api_key_error"))
    groq = _Listener("groq-whisper")
    stt = FallbackSTT(sarvam, groq)
    assert (await stt.transcribe(PCM, 16000)).text == "groq-whisper"
    await stt.transcribe(PCM, 16000)
    assert stt.switched and sarvam.calls == 1 and groq.calls == 2


# ── phrase cache on disk (paid voices only) ──

class _TaggedTTS:
    name = "paid"

    def __init__(self, tag="paid|v1"):
        self.voice_tag, self.calls = tag, 0

    async def stream(self, text, language):
        self.calls += 1
        yield b"\x01\x00" * 10


async def test_paid_phrases_are_kept_on_disk_across_restarts(tmp_path):
    first = _TaggedTTS()
    await PhraseCache(first, tmp_path).get("greeting", "en")
    assert first.calls == 1 and len(list(tmp_path.glob("*.pcm"))) == 1

    after_restart = _TaggedTTS()
    assert await PhraseCache(after_restart, tmp_path).get("greeting", "en") == b"\x01\x00" * 10
    assert after_restart.calls == 0

    other_voice = _TaggedTTS("paid|v2")  # another speaker or pace: made again, not reused
    await PhraseCache(other_voice, tmp_path).get("greeting", "en")
    assert other_voice.calls == 1


async def test_free_voices_and_switched_fallback_skip_the_disk(tmp_path):
    class Free:
        name = "edge-tts"

        async def stream(self, text, language):
            yield b"\0\0"

    await PhraseCache(Free(), tmp_path).get("greeting", "en")
    switched = FallbackTTS(_TaggedTTS(), Free())
    switched.switched = True
    await PhraseCache(switched, tmp_path).get("greeting", "en")
    assert not list(tmp_path.glob("*.pcm"))
