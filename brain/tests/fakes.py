"""Deterministic stand-ins for tests: no downloads, no network, fast."""

import hashlib
import math
import re


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

    async def json(self, model, system, user, schema, max_tokens=400, effort=None):
        """Understanding v2 by keyword rules. " and then " (or "; ") splits a turn into separate requests."""
        question = user.rsplit("Caller:", 1)[-1].strip()
        if "Conversation so far" in user and False:  # (history is not used by the fake)
            pass
        pending = "The assistant just asked:" in user
        parts = [p.strip() for p in re.split(r"\band then\b|;", question) if p.strip()] or [question]
        found = re.search(r"^Already found: (.*)$", user, re.M)  # the agenda's second look: only what was not found
        if found:
            seen = found.group(1).lower()
            parts = [p for p in parts if p.lower() not in seen] or ["thanks"]
        else:  # a part with "FAKE_MISS" is missed by the first reading (tests the coverage check and the second look)
            parts = [p for p in parts if "FAKE_MISS" not in p] or parts
        requests = [self._request(p) for p in parts[:3]]
        # every part is an ask in the caller's own words (labels left to code)
        asks = [] if found and parts == ["thanks"] else [{"quote": p, "type": "question", "label": ""} for p in parts[:8]]
        agenda_reply, agenda_pick = "none", 0
        if "Still to answer:" in user:
            low = question.lower()
            pick = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\b", low)
            if re.search(r"that'?s all|no more|leave (the rest|them)|stop", low):
                agenda_reply = "stop"
            elif re.search(r"\bskip\b", low):
                agenda_reply = "skip"
            elif pick:
                agenda_reply = "choose"
                agenda_pick = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}[pick.group(1)]
            elif re.search(r"\b(go on|continue|carry on)\b", low):
                agenda_reply = "continue"
        first = parts[0].lower()
        risk = []
        if re.search(r"don't recogni[sz]e|didn't make|did not make", first):
            risk.append("unrecognised_debit")
        if re.search(r"stolen|lost my card|card is lost", first):
            risk.append("lost_card")
        reply = "none"
        if pending:
            reply = "decline" if re.match(r"\s*(no|nahi)\b", first) else "accept" if re.match(r"\s*(yes|haan)\b", first) else "none"
            if reply == "none" and requests[0]["kind"] == "general" and "?" not in question:
                reply = "answer"  # a statement after our question answers it ("about 30,000, mostly shopping")
        lang = "hi" if re.search(r"[ऀ-ॿ]", question) else "en"
        data = {"language": lang, "asks": asks, "agenda_reply": agenda_reply, "agenda_pick": agenda_pick,
                "requests": requests, "risk": risk, "reply_to_pending": reply,
                "search_query_en": question, "facts": {"senior_citizen": "unknown", "product": "", "tenure": "", "amount": "",
                                                       "account": ""},
                "department": "credit_cards" if "credit" in first else "payments" if requests[0]["kind"] == "issue" else "general"}
        return data, {"model": model, "latency_ms": 1, "prompt_tokens": 0, "completion_tokens": 0, "retries": 0}

    @staticmethod
    def _request(question: str) -> dict:
        q = question.lower()
        kind = "general"
        if re.search(r"connect me|fraud team", q) and not re.search(r"stolen|don't recognise", q):
            kind = "human"
        elif re.search(r"don't recognise|didn't make|did not make|stolen|fraud", q):
            kind = "issue"
        elif re.search(r"\b(unblock|block|enable|disable|switch on|switch off)\b", q):
            kind = "action"
        elif re.search(r"atm did not|charged twice|debited but|money got deducted", q):
            kind = "issue"
        elif re.search(r"\b(my|mera|meri|mere)\b", q) and re.search(r"balance|transaction|emi|outstanding|statement", q):
            kind = "personal"
        elif re.search(r"\b(human|executive|agent)\b|connect me|fraud team", q):
            kind = "human"
        elif q.strip(" !.?") in ("hi", "hello", "thanks", "thank you", "namaste"):
            kind = "smalltalk"
        topic = next((t for k, t in (("balance", "balance"), ("transaction", "transactions"), ("emi", "loan"),
                                      ("outstanding", "credit_card")) if k in q), "other")
        account = "all" if re.search(r"\b(both|all)\b", q) else (re.search(r"\b(\d{4})\b", q) or [None, ""])[1]
        amount = next((float(x.replace(",", "")) for x in re.findall(r"\b\d[\d,]{2,}\b", q) if len(x.replace(",", "")) != 4
                       or x.startswith(("8", "1"))), 0)
        last4 = (re.search(r"\bending (\d{4})\b", q) or [None, ""])[1]
        card = "credit" if "credit" in q else "debit" if "debit" in q else ""
        count = int((re.search(r"\blast (\d{1,2})\b", q) or [None, 0])[1])
        if count:
            amount = 0
        personal = {"topic": topic, "detail": "", "account": account, "item": "", "card": card, "direction": "any",
                    "period": "", "count": count, "min_amount": 0, "amount": amount, "keyword": "", "total": False}
        action = {"type": "unblock" if "unblock" in q else "block" if "block" in q else
                  "controls" if re.search(r"enable|disable|switch", q) else "none",
                  "card": card, "card_last4": last4,
                  "block": "permanent" if "permanent" in q else "temporary" if "temporar" in q else "",
                  "atm": "", "pos": "", "ecom": "on" if "enable online" in q else "off" if "disable online" in q else "",
                  "contactless": "", "international": ""}
        issue = "atm_cash_not_dispensed" if "atm" in q else "double_charge" if "twice" in q else             "payment_failed_debited" if re.search(r"debited but|money got deducted", q) else "none"
        product = ("fd" if re.search(r"\bfd\b|fixed deposit", q) else "credit_card" if "credit card" in q
                   else "savings_account" if "savings" in q else "")
        goal = "choose" if re.search(r"interested in|which .* suits?|help me (choose|pick)", q) else "info"
        own_check = product == "fd" and bool(re.search(r"before maturity|break", q))
        return {"kind": kind, "question": question, "personal": personal if kind in ("personal", "action", "issue") else None,
                "action": action if kind in ("action", "issue") else None, "issue": issue if kind == "issue" else "none",
                "goal": goal, "product": product, "own_check": own_check}

    async def stream(self, model, messages, meta, max_tokens=400, effort=None):
        user = messages[-1]["content"]
        question = user.rsplit("Question:", 1)[-1].strip()
        sources = re.findall(r"\[(\d+)\] .*?\n(.*?)(?=\n\[\d+\] |\n\n(?:Caller details|Last exchange|What to do|Question):)", user, re.S)
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
