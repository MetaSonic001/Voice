"""Turn-taking scenarios for a live call, with deterministic fake audio.

'Speech' is a tone whose frequency tells FakeSTT what was said; 'silence' is zeros. The agent's audio is a quiet
tone that is never mistaken for the caller. Timing rules use the audio clock, so results don't depend on CPU speed.
"""

import asyncio

import pytest

from app.config import Settings
from app.services.conversation import Reply, TurnEvent
from app.services.numbers import speech_text
from app.voice.audio import silence, tone
from app.voice.phrases import PhraseCache, phrase_text, variants
from app.voice.session import VoiceSession
from tests.fakes import FakeSTT, FakeTTS, FakeVAD

RATE = 16000
Q = "What is the minimum balance for a savings account?"
LONG_ANSWER = " ".join(["The minimum balance is ten thousand rupees for urban branches."] * 4)
SCRIPT = {
    300: (Q, 0.0),
    500: ("haan", 0.0),
    700: ("ruko, what about senior citizens?", 0.0),
    900: ("", 0.95),  # noise: STT hears nothing real
    1100: ("I want to know about FD rates and", 0.0),  # sounds unfinished
    1300: ("I want to know about FD rates and for senior citizens too", 0.0),
    1500: ("stop", 0.0),
    1700: (LONG_ANSWER.split(".")[0], 0.0),  # the agent's own words (echo)
}


class StubBrain:
    def __init__(self, think_s: float = 0.05, answers: dict | None = None):
        self.think_s, self.answers, self.questions = think_s, answers or {}, []

    async def turn(self, question, session_id, extra_timings=None):
        self.questions.append(question)
        yield TurnEvent("understood", "en", "general")
        await asyncio.sleep(self.think_s)
        sentences = self.answers.get(question, ["Ten thousand rupees."])
        for s in sentences:
            yield TurnEvent("sentence", "en", "general", s, s)
        yield TurnEvent("done", reply=Reply("trace-1", session_id, "answered", "general", "en", " ".join(sentences),
                                            " ".join(sentences)))


class Harness:
    def __init__(self, brain=None, **overrides):
        settings = dict(audio_rate=RATE, playback_lead_ms=100, no_input_s=30, filler_after_ms=5000)
        settings.update(overrides)
        self.settings = Settings(**settings)
        self.events: list[dict] = []
        self.audio = bytearray()
        self.brain = brain or StubBrain()
        self.stt, self.tts = FakeSTT(SCRIPT), FakeTTS(RATE)
        self.session: VoiceSession | None = None

    async def start(self, greet=False):
        async def send_json(e):
            self.events.append(e)

        async def send_audio(b):
            self.audio.extend(b)

        self.session = VoiceSession(brain=self.brain, stt=self.stt, tts=self.tts, vad=FakeVAD(),
                                    phrases=PhraseCache(self.tts), settings=self.settings, send_json=send_json,
                                    send_audio=send_audio, session_id="s1")
        await self.session.start(greet=greet)
        return self

    async def feed(self, pcm: bytes, realtime: bool = False):
        step = 640  # 20 ms
        for i in range(0, len(pcm), step):
            self.session.feed(pcm[i:i + step])
            await asyncio.sleep(0.02 if realtime else 0)

    def types(self):
        return [e["type"] for e in self.events]

    async def wait_for(self, kind: str, timeout: float = 5.0, **match):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            for e in self.events:
                if e["type"] == kind and all(e.get(k) == v for k, v in match.items()):
                    return e
            await asyncio.sleep(0.01)
        raise AssertionError(f"no '{kind}' {match} within {timeout}s; got {self.types()}")

    async def close(self):
        await self.session.close()


def speech(hz: int, ms: int = 800) -> bytes:
    return tone(ms, RATE, freq=hz, amplitude=6000)


def quiet(ms: int) -> bytes:
    return silence(ms, RATE)


async def test_question_gets_spoken_answer_with_metrics():
    h = await Harness().start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    user = await h.wait_for("user")
    assert user["text"] == Q
    await h.wait_for("agent_text", text="Ten thousand rupees.")
    metrics = await h.wait_for("turn_metrics")
    assert metrics["voice_to_voice_ms"] >= 0 and "stt_ms" in metrics
    assert len(h.audio) > 0
    await h.close()


async def test_short_clicks_are_not_speech():
    h = await Harness().start()
    await h.feed(quiet(200) + speech(300, 100) + quiet(500) + speech(300, 120) + quiet(1000))
    await asyncio.sleep(0.3)
    assert "speech_started" not in h.types() and not h.brain.questions
    await h.close()


async def test_noise_that_passes_vad_is_ignored_after_stt():
    h = await Harness().start()
    await h.feed(quiet(200) + speech(900, 500) + quiet(1000))
    await h.wait_for("speech_ignored")
    await asyncio.sleep(0.2)
    assert not h.brain.questions
    await h.close()


async def test_slow_speaker_pause_after_and_is_one_turn():
    h = await Harness().start()
    # "I want to know about FD rates and ..." (pause 900 ms, longer than the normal 700 ms end) "... for senior citizens too"
    await h.feed(quiet(200) + speech(1100, 700) + quiet(900), realtime=True)
    await h.feed(speech(1300, 1200) + quiet(1600), realtime=True)  # this caller pauses a lot: they get a longer window
    await h.wait_for("user")
    await asyncio.sleep(0.3)
    assert h.brain.questions == ["I want to know about FD rates and for senior citizens too"]
    await h.close()


async def _agent_talking(h: Harness):
    """Ask the question that has a long answer and wait until the agent is audibly speaking."""
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("agent_text")
    for _ in range(200):
        if len(h.audio) > 12800:  # 0.4 s: past the start-of-reply grace window (echo)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("agent never started speaking")


async def test_real_barge_in_stops_agent_and_takes_new_question():
    brain = StubBrain(answers={Q: [LONG_ANSWER]})
    h = await Harness(brain).start()
    await _agent_talking(h)
    await h.feed(speech(700, 600) + quiet(1000))
    check = await h.wait_for("barge_in_check")
    assert check["verdict"] == "interrupt"
    await h.wait_for("clear")
    second = [e for e in h.events if e["type"] == "user"]
    for _ in range(300):
        second = [e for e in h.events if e["type"] == "user"]
        if len(second) == 2:
            break
        await asyncio.sleep(0.01)
    assert second[-1]["text"] == "ruko, what about senior citizens?"
    await h.close()


@pytest.mark.parametrize("hz,verdict", [(500, "backchannel"), (900, "noise"), (1700, "echo")])
async def test_false_barge_ins_resume_the_agent(hz, verdict):
    brain = StubBrain(answers={Q: [LONG_ANSWER]})
    h = await Harness(brain).start()
    await _agent_talking(h)
    await h.feed(speech(hz, 500) + quiet(400))
    check = await h.wait_for("barge_in_check")
    assert check["verdict"] == verdict
    await h.wait_for("turn_metrics", timeout=10)  # the full answer still plays to the end
    assert "clear" not in h.types()
    expected_ms = len(LONG_ANSWER.split()) * 100
    assert len(h.audio) / 2 / RATE * 1000 >= expected_ms * 0.95
    await h.close()


async def test_stop_word_alone_silences_agent_and_listens():
    brain = StubBrain(answers={Q: [LONG_ANSWER]})
    h = await Harness(brain).start()
    await _agent_talking(h)
    await h.feed(speech(1500, 500) + quiet(800))
    await h.wait_for("clear")
    await asyncio.sleep(0.3)
    assert brain.questions == [Q]  # nothing new was asked
    await h.close()


async def test_correction_while_thinking_merges_with_previous_question():
    brain = StubBrain(think_s=1.5)
    h = await Harness(brain).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("user")
    await h.feed(speech(700, 600) + quiet(1000))  # caller adds to it before the agent has spoken
    for _ in range(300):
        if len(brain.questions) == 2:
            break
        await asyncio.sleep(0.01)
    assert brain.questions[1] == f"{Q} ruko, what about senior citizens?"
    await h.close()


async def test_filler_plays_when_answer_is_slow():
    h = await Harness(StubBrain(think_s=0.8), filler_after_ms=200).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("turn_metrics", timeout=8)
    assert h.tts.spoken[0] == speech_text(phrase_text("filler", "en"), "en")
    await h.close()


async def test_silence_reprompts_then_ends_call():
    h = await Harness(no_input_s=0.2).start()
    await h.wait_for("agent_text", phrase="reprompt", timeout=5)
    end = await h.wait_for("end", timeout=10)
    assert end["reason"] == "no_input"
    spoken = {speech_text(phrase_text("goodbye", lang), lang) for lang in ("hi", "en")}  # as sent to TTS
    assert spoken & set(h.tts.spoken)


class CodeBrain(StubBrain):
    """Asks for a keypad code (as a personal question does); `verify` accepts it."""

    async def turn(self, question, session_id, extra_timings=None):
        self.questions.append(question)
        yield TurnEvent("understood", "en", "personal")
        yield TurnEvent("auth", "en", "personal", auth={"status": "otp_sent"})
        text = "Please type the code sent to your mobile."
        yield TurnEvent("sentence", "en", "personal", text, text)
        yield TurnEvent("done", reply=Reply("trace-1", session_id, "auth_required", "personal", "en", text, text))

    async def verify(self, session_id, code):
        return {"status": "verified"}, "Thank you, you are verified.", "en"


async def test_waiting_for_the_code_reminds_about_the_code_not_the_menu():
    h = await Harness(brain=CodeBrain(), no_input_s=0.2, code_wait_s=0.8).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("auth", status="otp_sent")
    await h.wait_for("agent_text", phrase="code_reminder", timeout=5)
    assert not any(e.get("phrase") == "reprompt" for e in h.events)  # not "are you still there? ask about loans..."
    end = await h.wait_for("end", timeout=10)
    assert end["reason"] == "no_input"


async def test_after_the_code_the_normal_silence_prompt_returns():
    h = await Harness(brain=CodeBrain(), no_input_s=0.3, code_wait_s=30).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("auth", status="otp_sent")
    await h.session.submit_code("123456")
    await h.wait_for("auth", status="verified")
    await h.wait_for("agent_text", phrase="reprompt", timeout=5)  # 0.3 s, not the 30 s code wait
    assert not any(e.get("phrase") == "code_reminder" for e in h.events)
    await h.close()


async def test_greeting_plays_and_can_be_interrupted():
    h = await Harness().start(greet=True)
    await h.wait_for("agent_text", phrase="greeting")
    for _ in range(200):
        if len(h.audio) > 12800:  # 0.4 s: past the start-of-reply grace window (echo)
            break
        await asyncio.sleep(0.01)
    await h.feed(speech(300, 600) + quiet(1000))
    await h.wait_for("clear")
    await h.wait_for("user")
    await h.close()


async def test_text_turn_without_audio():
    h = await Harness().start()
    await h.session.text_turn(Q)
    await h.wait_for("agent_text", text="Ten thousand rupees.")
    await h.close()


class HandoffBrain(StubBrain):
    """Says "connecting you" and hands the call to a person."""

    async def turn(self, question, session_id, extra_timings=None):
        self.questions.append(question)
        yield TurnEvent("understood", "en", "human")
        yield TurnEvent("sentence", "en", "human", "Connecting you now.", "Connecting you now.")
        yield TurnEvent("handoff", "en", "human", handoff={"id": "h1", "department": "general", "name": "Customer Care"})
        yield TurnEvent("done", reply=Reply("trace-1", session_id, "handoff", "human", "en", "Connecting you now.",
                                            "Connecting you now."))


async def test_transfer_holds_then_bridges_a_person():
    h = await Harness(HandoffBrain()).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    waiting = await h.wait_for("handoff", status="waiting")
    assert waiting["department"] == "general" and h.session.mode == "hold"
    asked = len(h.brain.questions)
    await h.feed(speech(700, 600) + quiet(1000))  # the caller talks while on hold: nothing goes to the AI
    await asyncio.sleep(0.3)
    assert len(h.brain.questions) == asked
    heard = bytearray()

    async def person(pcm):
        heard.extend(pcm)

    await h.session.connect_agent("Sana", person)
    await h.wait_for("handoff", status="active", agent="Sana")
    await h.feed(speech(700, 200))  # the caller's audio now goes to the person
    await asyncio.sleep(0.05)
    assert len(heard) > 0 and h.session.mode == "human"
    await h.close()


# ───────── Phase 4 step 9: unclear speech ─────────

from app.voice.turntaking import unclear_speech  # noqa: E402


@pytest.mark.parametrize("text,no_speech,confidence,compression,expected", [
    ("main apna balance", 0.05, -0.4, 1.2, False),  # a normal accent: confident enough
    ("main apna balance", 0.05, -1.5, 1.2, False),  # low confidence alone is not enough (soft voice, unfamiliar name)
    ("ok ok ok ok ok ok ok ok", 0.1, None, 1.0, False),  # nonsense alone is not enough when the recogniser is unsure of nothing
    ("ok ok ok ok ok ok ok ok", 0.1, -1.6, 1.0, True),  # unsure AND repetitive: two signals agree
    ("chatna", 0.4, -1.3, 1.1, True),  # unsure of the words AND unsure it was speech
    ("", 0.9, None, 0.0, False),
])
def test_unclear_speech_needs_two_signals(text, no_speech, confidence, compression, expected):
    assert unclear_speech(text, no_speech, confidence, compression) is expected


async def test_unclear_speech_asks_once_then_offers_another_way():
    class Unsure(FakeSTT):
        async def transcribe(self, pcm16, rate):
            t = await super().transcribe(pcm16, rate)
            t.confidence, t.compression = -1.8, 3.0
            return t

    h = await Harness().start()
    h.stt = h.session.stt = Unsure(SCRIPT)
    h.session.language = "en"
    for n in (1, 2):
        while h.session.agent_active:  # let the first "say that again" finish before the caller tries again
            await asyncio.sleep(0.05)
        await h.feed(tone(800, RATE, freq=300) + silence(1200, RATE))
        await h.wait_for("speech_ignored", reason="unclear", in_a_row=n)
    assert h.brain.questions == []  # an unclear turn never reaches the brain
    for _ in range(100):  # the second phrase is synthesised after the event
        if len(h.tts.spoken) >= 2:
            break
        await asyncio.sleep(0.05)
    assert any("Could you say it again" in s for s in h.tts.spoken), h.tts.spoken
    assert any("still having trouble" in s for s in h.tts.spoken) 
    await h.close()


class ResumeBrain(CodeBrain):
    """After the code, the question that waited for it is answered (slowly, like a real bank read)."""

    async def turn(self, question, session_id, extra_timings=None):
        if question != "What is my balance?":
            async for event in super().turn(question, session_id, extra_timings):
                yield event
            return
        self.questions.append(question)
        yield TurnEvent("understood", "en", "personal")
        await asyncio.sleep(0.5)
        yield TurnEvent("sentence", "en", "personal", "You have ten rupees.", "You have ten rupees.")
        yield TurnEvent("done", reply=Reply("trace-2", session_id, "answered", "personal", "en", "You have ten rupees.",
                                            "You have ten rupees."))

    async def verify(self, session_id, code):
        return {"status": "verified", "pending_question": "What is my balance?"}, "Thank you, you are verified.", "en"


async def test_after_verification_the_answer_follows_with_no_filler():
    # live call 16 Nov: "...you are verified." then "Okay, one second." then the answer - three lines where two do
    h = await Harness(brain=ResumeBrain(), filler_after_ms=100).start()
    await h.feed(quiet(200) + speech(300) + quiet(1000))
    await h.wait_for("auth", status="otp_sent")
    await h.session.submit_code("123456")
    await h.wait_for("agent_text", text="You have ten rupees.", timeout=5)
    await h.wait_for("turn_metrics", timeout=5)
    filler = {speech_text(t, "en") for t in variants("filler", "en")}
    assert not filler & set(h.tts.spoken)
    assert h.tts.spoken.index("Thank you, you are verified.") < h.tts.spoken.index("You have ten rupees.")
    await h.close()
