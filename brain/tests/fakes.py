"""Deterministic stand-ins for tests: no downloads, no network, fast."""

import hashlib
import math
import re

from app.domain import Understanding

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class FakeEmbedder:
    """Bag-of-words hashed into a fixed-size unit vector: similar wording -> similar vectors."""

    name, dim = "fake-embedder", 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in _tokens(text):
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class FakeReranker:
    name = "fake-reranker"

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = set(_tokens(query))
        return [len(q & set(_tokens(p))) / (len(q) or 1) for p in passages]


class FakeLLM:
    """Understanding: keyword rules. Answer: first line of context source [1] that shares words with the question."""

    async def json(self, model, system, user, schema, max_tokens=400):
        question = user.rsplit("Caller:", 1)[-1].strip()
        q = question.lower()
        intent = "general"
        if re.search(r"\b(my|mera|meri|mere)\b", q) and re.search(r"balance|transaction|emi|outstanding|statement", q):
            intent = "personal"
        elif re.search(r"\bblock\b", q):
            intent = "action"
        elif re.search(r"\b(human|executive|agent)\b", q):
            intent = "human"
        elif q.strip(" !.?") in ("hi", "hello", "thanks", "thank you", "namaste"):
            intent = "smalltalk"
        lang = "hi" if re.search(r"[ऀ-ॿ]", question) else "en"
        topic = next((t for k, t in (("balance", "balance"), ("transaction", "transactions"), ("emi", "loan"),
                                      ("outstanding", "credit_card")) if k in q), "other")
        account = "all" if re.search(r"\b(both|all)\b", q) else (re.search(r"\b(\d{4})\b", q) or [None, ""])[1]
        personal = {"topic": topic, "account": account, "direction": "any", "period": "", "count": 0, "min_amount": 0,
                    "keyword": "", "total": False}
        data = Understanding(lang, intent, question, question, personal).__dict__
        return data, {"model": model, "latency_ms": 1, "prompt_tokens": 0, "completion_tokens": 0, "retries": 0}

    async def stream(self, model, messages, meta, max_tokens=400):
        user = messages[-1]["content"]
        question = user.rsplit("Question:", 1)[-1].strip()
        sources = re.findall(r"\[(\d+)\] .*?\n(.*?)(?=\n\[\d+\] |\n\nQuestion:)", user, re.S)
        q = set(_tokens(question))
        answer = "NO_ANSWER"
        for n, body in sources:
            for line in body.splitlines():
                line = line.strip("-# ").strip()
                if line and len(q & set(_tokens(line))) >= 2:
                    answer = f"{line} [{n}]"
                    break
            if answer != "NO_ANSWER":
                break
        meta.update(model=model, ttft_ms=1, latency_ms=1, prompt_tokens=0, completion_tokens=0, retries=0)
        for part in re.split(r"(\s+)", answer):
            yield part



# ───────── voice fakes ─────────

import asyncio  # noqa: E402

import numpy as np  # noqa: E402

from app.domain import Transcript  # noqa: E402
from app.voice.audio import rms, tone  # noqa: E402


class FakeVAD:
    """Speech = loud frame. Tests build 'speech' from tones and 'silence' from zeros."""

    frame_samples = 512

    def prob(self, frame: bytes) -> float:
        return 1.0 if rms(frame) > 1000 else 0.0

    def reset(self) -> None:
        pass


def dominant_hz(pcm: bytes, rate: int = 16000) -> int:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if np.max(np.abs(samples), initial=0) < 1000:
        return 0
    spectrum = np.abs(np.fft.rfft(samples))
    return int(round(np.fft.rfftfreq(samples.size, 1 / rate)[int(np.argmax(spectrum))] / 100) * 100)


class FakeSTT:
    """Recognises which scripted utterance it heard from the tone frequency (100 Hz buckets)."""

    name = "fake-stt"

    def __init__(self, script: dict[int, tuple[str, float]], delay_s: float = 0.05):
        self.script, self.delay_s, self.calls = script, delay_s, []

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        await asyncio.sleep(self.delay_s)
        hz = dominant_hz(pcm16, rate)
        text, no_speech = self.script.get(hz, ("", 1.0))
        self.calls.append(hz)
        return Transcript(text, "en", no_speech, int(self.delay_s * 1000))


class FakeTTS:
    """Quiet tone (never mistaken for caller speech): 100 ms per word, delivered in two chunks."""

    name = "fake-tts"

    def __init__(self, rate: int = 16000, ms_per_word: int = 100):
        self.rate, self.ms_per_word, self.spoken = rate, ms_per_word, []

    async def stream(self, text: str, language: str):
        self.spoken.append(text)
        pcm = tone(max(1, len(text.split())) * self.ms_per_word, self.rate, freq=440, amplitude=300)
        half = len(pcm) // 2 // 2 * 2
        await asyncio.sleep(0.01)
        yield pcm[:half]
        await asyncio.sleep(0.01)
        yield pcm[half:]
