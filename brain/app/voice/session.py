"""One live voice call: turn-taking, barge-in, streaming replies.

Audio in  -> VAD per 32 ms frame (audio clock, deterministic) -> capture utterances
          -> speculative STT once the caller pauses -> end-of-turn decision -> brain.turn() streams checked
             sentences -> TTS per sentence -> Player paces audio out.

Turn-taking rules
- Speech must last `vad_start_ms` to count (clicks, coughs, door slams are ignored); Silero VAD scores
  steady noise (fans, traffic, tones) as non-speech.
- End of turn: `end_silence_ms` of silence when the transcript sounds complete; up to `max_end_silence_ms` when
  it sounds unfinished ("...and", "aur", "matlab"). Callers who pause a lot get a longer window automatically.
- STT starts speculatively after a short pause, so the transcript is usually ready the moment the turn ends.
- While the agent is talking, caller sound must last `bargein_min_ms` to count as a possible interruption. Before
  that, three cheap filters drop most background sound: it must score higher on the speech detector than normal
  speech needs to, it must not be much quieter than the caller's own voice (people and TVs in the background
  are), and it must not fall in the first moments of the agent's reply (echo before the browser's echo
  cancellation settles). A possible interruption lowers the agent's volume (never a silent gap) while what was
  said is transcribed: noise / echo of the agent / "haan", "hmm", "ok" -> back to full volume;
  "ruko", "stop", "wait" or a real question -> the agent stops, drops queued audio and listens.
- Talking while the agent is still thinking = correction/addition: the pending answer is cancelled and the new
  words are appended to the previous question.
- Silence after the agent finishes: re-prompt after `no_input_s`, then say goodbye and end the call.
- A filler ("one moment") plays if the first sentence isn't ready after `filler_after_ms`.
- Transfer to a person (after the caller said yes): the agent stops listening, hold music plays with a "thank
  you for waiting" every 30 s, and when someone accepts at the desk their audio is bridged to the caller (and
  recorded). Nobody within `handoff_wait_s` -> a callback is booked and the call ends politely.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.domain import Transcript
from app.ports import STT, TTS, VAD
from app.services.numbers import speech_text
from app.voice.audio import BYTES_PER_SAMPLE, duration_ms, rms
from app.voice.hold import hold_music
from app.voice.phrases import PhraseCache, phrase_text
from app.voice.player import Player
from app.voice.turntaking import classify_barge, incomplete, is_noise, unclear_speech, words

logger = logging.getLogger(__name__)

SPECULATIVE_MS = 300  # start transcribing after this much silence
BARGE_DECIDE_SILENCE_MS = 300  # how long the caller pauses before we judge an interruption
BARGE_DECIDE_MAX_MS = 1500  # or judge after this much speech
PREROLL_MS = 300  # audio kept from before speech was confirmed, so first syllables aren't lost
BARGE_VAD_START = 0.65  # while the agent talks, a frame must score this high to start a possible interruption (else 0.5)
BARGE_QUIET_RATIO = 0.25  # ... and be at least this loud relative to the caller's own speech (-12 dB)
BARGE_GRACE_S = 0.3  # ... and not in the first 0.3 s of agent audio
DUCK_GAIN = 0.3  # agent volume while a possible interruption is checked


class Brain(Protocol):
    def turn(self, question: str, session_id: str | None, extra_timings: dict | None = None) -> AsyncIterator: ...

    async def verify(self, session_id: str, code: str) -> tuple[dict, str, str]: ...

    async def handoff_timeout(self, session_id: str, handoff_id: str, language: str) -> str: ...

# Live calls by session id, so the agent desk can join one (filled by the voice API).
LIVE_SESSIONS: dict[str, "VoiceSession"] = {}
HOLD_MESSAGE_S = 30


@dataclass
class Capture:
    kind: str  # "turn" (caller speaking normally) | "barge" (caller sound while the agent talks)
    audio: bytearray
    started_clock_ms: float
    speech_ms: float = 0.0
    silence_ms: float = 0.0
    version: int = 0  # bumps whenever speech resumes after a pause (invalidates pending decisions)
    last_speech_wall: float = field(default_factory=time.perf_counter)
    stt: tuple[int, asyncio.Task] | None = None  # (version, task) of the speculative transcription
    deciding: bool = False
    merge_with: str = ""  # previous question, when this utterance corrects/extends it before the agent spoke


class VoiceSession:
    def __init__(self, *, brain: Brain, stt: STT, tts: TTS, vad: VAD, phrases: PhraseCache, settings,
                 send_json: Callable[[dict], Awaitable[None]], send_audio: Callable[[bytes], Awaitable[None]],
                 session_id: str | None, on_voice_timings: Callable[[str, dict], Awaitable[None]] | None = None,
                 greeting_language: str = "hi", recorder=None, on_event: Callable[..., None] | None = None):
        self.brain, self.stt, self.tts, self.vad, self.phrases, self.s = brain, stt, tts, vad, phrases, settings
        self.send_json, self.session_id, self.on_voice_timings = send_json, session_id, on_voice_timings
        self.rate = settings.audio_rate
        self.frame_bytes = vad.frame_samples * BYTES_PER_SAMPLE
        self.frame_ms = vad.frame_samples / self.rate * 1000
        self.recorder = recorder
        self.player = Player(send_audio, self.rate, settings.playback_lead_ms,
                             on_frame=recorder.agent if recorder else None)
        self.language = greeting_language
        self.greeting_language = greeting_language

        self._inbuf = bytearray()
        self._clock_ms = 0.0
        self._speech_run_ms = 0.0
        self._in_speech = False
        self._preroll: deque[bytes] = deque(maxlen=max(1, int(PREROLL_MS / self.frame_ms)))
        self._caller_levels: deque[float] = deque(maxlen=300)  # loudness of the caller's own speech frames (~10 s)
        self._on_event = on_event  # call timeline for the audit: disclosure played, interruptions, errors
        self._capture: Capture | None = None
        self._longest_resumed_pause_ms = 0.0

        self._agent_task: asyncio.Task | None = None  # the current reply (generation + TTS)
        self._agent_audio_started = False  # has any audio of the current reply been sent?
        self._agent_text = ""  # what the agent is saying now (for echo detection)
        self._last_question = ""  # for merging a correction made while the agent was thinking
        self._no_input_task: asyncio.Task | None = None
        self._reprompts = 0
        self._unclear = 0  # consecutive turns the recogniser was unsure of
        self._tasks: set[asyncio.Task] = set()
        self.closed = asyncio.Event()
        self.end_reason = ""
        self.mode = "ai"  # ai | hold (waiting for a person) | human (a person is talking to the caller)
        self.handoff: dict | None = None
        self._agent_sink: Callable[[bytes], Awaitable[None]] | None = None

    # ───────── lifecycle ─────────

    async def start(self, greet: bool = True) -> None:
        await self.send_json({"type": "ready", "session_id": self.session_id, "sample_rate": self.rate,
                              "frame_ms": round(self.frame_ms)})
        if greet:
            self._agent_task = self._spawn(self._say_phrase("greeting", self.greeting_language))
        else:
            self._arm_no_input()

    async def close(self, reason: str = "closed") -> None:
        if self.closed.is_set():
            return
        self.end_reason = reason
        self.closed.set()
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        await self.player.close()
        try:
            await self.send_json({"type": "end", "reason": reason})
        except Exception:  # the client may already be gone
            pass

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("voice task failed", exc_info=task.exception())

    @property
    def agent_active(self) -> bool:
        return (self._agent_task is not None and not self._agent_task.done()) or self.player.playing

    # ───────── audio in (synchronous, per frame) ─────────

    def feed(self, pcm: bytes) -> None:
        if self.closed.is_set():
            return
        if self.recorder:
            self.recorder.caller(pcm)
        if self.mode != "ai":
            if self.mode == "human" and self._agent_sink:
                self._spawn(self._agent_sink(pcm))
            return
        self._inbuf += pcm
        while len(self._inbuf) >= self.frame_bytes:
            frame = bytes(self._inbuf[: self.frame_bytes])
            del self._inbuf[: self.frame_bytes]
            self._process_frame(frame)

    def _process_frame(self, frame: bytes) -> None:
        self._clock_ms += self.frame_ms
        p = self.vad.prob(frame)
        cap = self._capture
        agent = cap is None and self.agent_active
        start = BARGE_VAD_START if agent else 0.5
        self._in_speech = p >= (0.35 if self._in_speech else start)  # hysteresis: harder to start than to continue

        if cap is None:
            self._preroll.append(frame)
            if agent and time.monotonic() - self.player.speaking_since < BARGE_GRACE_S:
                self._speech_run_ms = 0.0  # the agent just started talking: likely its own echo
                return
            self._speech_run_ms = self._speech_run_ms + self.frame_ms if self._in_speech else 0.0
            needed = self.s.bargein_min_ms if agent else self.s.vad_start_ms
            if self._speech_run_ms >= needed:
                if agent and self._quieter_than_caller():
                    self._speech_run_ms = 0.0  # background voice or TV: much quieter than the caller
                    return
                kind = "barge" if agent else "turn"
                self._capture = Capture(kind, bytearray(b"".join(self._preroll)), self._clock_ms, speech_ms=self._speech_run_ms)
                self._preroll.clear()
                self._speech_run_ms = 0.0
                self._cancel_no_input()
                if kind == "barge":
                    self.player.duck(DUCK_GAIN)  # keep talking, quieter, while we check what this was
                self._emit({"type": "speech_started", "barge_in": kind == "barge"})
            return

        cap.audio += frame
        if self._in_speech and cap.kind == "turn":
            self._caller_levels.append(rms(frame))
        if self._in_speech:
            if cap.silence_ms >= 250:
                self._longest_resumed_pause_ms = max(self._longest_resumed_pause_ms, cap.silence_ms)
                cap.version += 1
            cap.silence_ms = 0.0
            cap.speech_ms += self.frame_ms
            cap.last_speech_wall = time.perf_counter()
        else:
            cap.silence_ms += self.frame_ms

        self._check_capture(cap)

    def _quieter_than_caller(self) -> bool:
        """The last few frames are much quieter than this caller's own speech (needs ~1 s of their speech first)."""
        if len(self._caller_levels) < 30:
            return False
        recent = list(self._preroll)[-max(1, int(self.s.bargein_min_ms / self.frame_ms)):]
        level = sorted(rms(f) for f in recent)[len(recent) // 2]
        caller = sorted(self._caller_levels)[len(self._caller_levels) // 2]
        return level < caller * BARGE_QUIET_RATIO

    def _check_capture(self, cap: Capture) -> None:
        """Start speculative STT / a decision when the pause or length thresholds are reached."""
        if cap.kind == "barge":
            if not cap.deciding and (cap.silence_ms >= BARGE_DECIDE_SILENCE_MS or cap.speech_ms >= BARGE_DECIDE_MAX_MS):
                cap.deciding = True
                self._spawn(self._decide_barge(cap))
            return
        length_ms = self._clock_ms - cap.started_clock_ms
        if cap.silence_ms >= SPECULATIVE_MS and (cap.stt is None or cap.stt[0] != cap.version):
            cap.stt = (cap.version, self._spawn(self._transcribe(bytes(cap.audio))))
        if not cap.deciding and (cap.silence_ms >= self._end_silence_ms() or length_ms >= self.s.max_utterance_s * 1000):
            cap.deciding = True
            self._spawn(self._decide_turn(cap, forced=length_ms >= self.s.max_utterance_s * 1000))

    def _end_silence_ms(self) -> float:
        """Base end-of-turn silence, stretched for callers who pause mid-sentence."""
        adaptive = self._longest_resumed_pause_ms * 1.3
        return min(self.s.max_end_silence_ms, max(self.s.end_silence_ms, adaptive))

    # ───────── decisions (async) ─────────

    async def _transcribe(self, pcm: bytes) -> Transcript:
        try:
            return await self.stt.transcribe(pcm, self.rate)
        except Exception as e:
            logger.exception("stt failed")
            self._audit("error", source="speech-to-text", error=f"{type(e).__name__}: {e}")
            return Transcript("", None, 1.0, 0)

    async def _decide_turn(self, cap: Capture, forced: bool = False) -> None:
        version = cap.version
        task = cap.stt[1] if cap.stt and cap.stt[0] == version else self._spawn(self._transcribe(bytes(cap.audio)))
        result = await task
        if self._capture is not cap:
            return
        if cap.version != version:  # caller started talking again while we were transcribing
            cap.deciding = False
            return
        if is_noise(result.text, result.no_speech):
            self._capture = None
            self._emit({"type": "speech_ignored", "reason": "noise"})
            if not self.agent_active:
                self._arm_no_input()
            return
        if unclear_speech(result.text, result.no_speech, result.confidence, result.compression):
            self._capture = None
            self._unclear += 1  # asked to repeat once; the second time in a row, offer another way
            self._emit({"type": "speech_ignored", "reason": "unclear", "in_a_row": self._unclear})
            self._audit("unclear_speech", in_a_row=self._unclear)
            self._agent_task = self._spawn(self._say_phrase("unclear_twice" if self._unclear >= 2 else "unclear", self.language))
            return
        self._unclear = 0
        if not forced and incomplete(result.text) and cap.silence_ms < self.s.max_end_silence_ms:
            # sounds unfinished ("...and", "aur"): give the caller up to max_end_silence_ms
            await asyncio.sleep(max(0.0, (self.s.max_end_silence_ms - cap.silence_ms) / 1000))
            if self._capture is not cap or cap.version != version:
                if self._capture is cap:
                    cap.deciding = False
                return
        self._capture = None
        timings = {"input": "speech", "stt_ms": result.latency_ms, "stt_no_speech": round(result.no_speech, 3),
                   "endpoint_ms": round((time.perf_counter() - cap.last_speech_wall) * 1000),
                   "utterance_ms": round(duration_ms(bytes(cap.audio), self.rate))}
        self._start_reply(result, cap.last_speech_wall, timings, merge_with=cap.merge_with)

    async def _decide_barge(self, cap: Capture) -> None:
        result = await self._transcribe(bytes(cap.audio))
        if self._capture is not cap:
            return
        verdict = classify_barge(result.text, result.no_speech, self._agent_text)
        self._emit({"type": "barge_in_check", "verdict": verdict, "text": result.text})
        if verdict in ("noise", "echo", "backchannel"):
            self._capture = None
            self.player.duck(1.0)  # false alarm: back to normal volume (the agent never stopped)
            return
        thinking_only = not self._agent_audio_started  # agent hadn't started speaking this reply yet
        await self._interrupt()
        content = [w for w in words(result.text) if w not in {"stop", "wait", "ruko", "rukiye", "bas", "sorry", "no", "nahi"}]
        if not content:  # just "stop"/"ruko": stay quiet and listen
            self._capture = None
            self._arm_no_input()
            return
        # a real question or correction: keep listening to the rest of it as a normal turn
        cap.kind, cap.deciding = "turn", False
        cap.merge_with = self._last_question if thinking_only else ""
        self._check_capture(cap)  # the caller may already have finished while we were checking

    async def _interrupt(self) -> None:
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        self.player.clear()
        self._audit("interrupted", while_saying=self._agent_text)
        self._agent_text = ""
        await self.send_json({"type": "clear"})  # client must drop any audio it has buffered
        self._emit({"type": "interrupted"})

    # ───────── replies ─────────

    def _start_reply(self, transcript: Transcript, user_end_wall: float, timings: dict, merge_with: str = "") -> None:
        text = f"{merge_with} {transcript.text.strip()}".strip()
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        self._reprompts = 0
        self._last_question = text
        self._agent_task = self._spawn(self._reply(text, transcript.language, user_end_wall, timings))

    async def _reply(self, question: str, stt_language: str | None, user_end_wall: float, timings: dict) -> None:
        self._agent_audio_started = False
        await self.send_json({"type": "user", "text": question, "stt_language": stt_language})
        first_audio = {}

        def mark_first_audio():
            if "wall" not in first_audio:
                first_audio["wall"] = time.perf_counter()
                first_audio.setdefault("any", first_audio["wall"])
                self._agent_audio_started = True

        def mark_filler():
            first_audio.setdefault("any", time.perf_counter())

        filler = self._spawn(self._filler_after(self.s.filler_after_ms / 1000, mark_filler))
        sentences: asyncio.Queue = asyncio.Queue()
        trace_id = None

        async def produce():
            nonlocal trace_id
            try:
                async for event in self.brain.turn(question, self.session_id, extra_timings=timings):
                    if event.kind == "understood":
                        self.language = event.language
                    elif event.kind == "auth":  # show the keypad / simulated SMS on the client
                        await self.send_json({"type": "auth", **event.auth})
                    elif event.kind == "sentence":
                        await sentences.put(event)
                    elif event.kind == "handoff":
                        self.handoff = event.handoff
                    elif event.kind == "done":
                        trace_id = event.reply.trace_id
                        await self.send_json({"type": "reply_done", "outcome": event.reply.outcome,
                                              "language": event.reply.language, "trace_id": trace_id,
                                              "citations": [c.get("section") or c.get("source", "") for c in event.reply.citations]})
            finally:
                await sentences.put(None)

        async def speak():
            while (event := await sentences.get()) is not None:
                filler.cancel()
                self._agent_text = event.text
                await self.send_json({"type": "agent_text", "text": event.text})
                first_chunk = True
                async for pcm in self.tts.stream(event.speech, event.language):
                    self.player.enqueue(pcm, on_start=mark_first_audio if first_chunk else None)
                    first_chunk = False

        try:
            await asyncio.gather(produce(), speak())
            await self.player.wait_done()
        except asyncio.CancelledError:
            filler.cancel()
            raise
        except Exception as e:
            logger.exception("reply failed")
            self._audit("error", source="reply", error=f"{type(e).__name__}: {e}")
            filler.cancel()
            self.player.enqueue(await self.phrases.get("unclear", self.language))
            await self.player.wait_done()
        finally:
            filler.cancel()
        voice = {**timings}
        if "wall" in first_audio:  # caller stopped speaking -> first audio of the answer
            voice["voice_to_voice_ms"] = round((first_audio["wall"] - user_end_wall) * 1000)
        if "any" in first_audio:  # ... -> first thing the caller hears (the filler, if one played)
            voice["first_audio_ms"] = round((first_audio["any"] - user_end_wall) * 1000)
        await self.send_json({"type": "turn_metrics", **voice})
        if trace_id and self.on_voice_timings:
            await self.on_voice_timings(trace_id, voice)
        self._agent_text = ""
        if self.handoff and self.mode == "ai":
            self._start_hold()
            return
        self._arm_no_input()

    async def _filler_after(self, delay_s: float, on_start: Callable[[], None] | None = None) -> None:
        await asyncio.sleep(delay_s)
        if not self.player.playing:  # the next line of the pool each time: the caller never hears the same one twice running
            variant = getattr(self, "_filler_turn", 0)
            self._filler_turn = variant + 1
            self.player.enqueue(await self.phrases.get("filler", self.language, variant), on_start=on_start)

    async def _say_phrase(self, kind: str, language: str) -> None:
        pcm = await self.phrases.get(kind, language)
        self._agent_text = phrase_text(kind, language)  # lets echo detection recognise our own voice
        await self.send_json({"type": "agent_text", "text": self._agent_text, "phrase": kind})
        self._audit("disclosure" if kind == "greeting" else "phrase", phrase=kind, text=self._agent_text)
        self.player.enqueue(pcm)
        self._agent_audio_started = True
        await self.player.wait_done()
        self._arm_no_input()

    # ───────── silence handling ─────────

    # ───────── transfer to a person ─────────

    def _start_hold(self) -> None:
        self.mode = "hold"
        self._cancel_no_input()
        self._capture = None
        self._audit("on_hold", handoff=self.handoff["id"], department=self.handoff["department"])
        self._emit({"type": "handoff", "status": "waiting", **self.handoff})
        self._agent_task = self._spawn(self._hold())

    async def _hold(self) -> None:
        music = hold_music(self.rate)
        started, next_message = time.monotonic(), time.monotonic() + HOLD_MESSAGE_S
        while self.mode == "hold":
            if time.monotonic() - started > self.s.handoff_wait_s:
                text = await self.brain.handoff_timeout(self.session_id, self.handoff["id"], self.language)
                self.mode = "ai"
                self.player.clear()
                await self._say_text(text)
                await self.close("nobody available: callback booked")
                return
            if time.monotonic() >= next_message:
                next_message = time.monotonic() + HOLD_MESSAGE_S
                self.player.enqueue(await self.phrases.get("hold", self.language))
            if self.player.remaining_s() < 1.0:
                self.player.enqueue(music)
            await asyncio.sleep(0.25)

    async def connect_agent(self, name: str, sink: Callable[[bytes], Awaitable[None]]) -> None:
        """A person accepted at the desk: stop the music, and from now on the caller and the person talk directly."""
        self.mode = "human"
        self._agent_sink = sink
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        self.player.clear()
        await self.send_json({"type": "clear"})
        await self.send_json({"type": "handoff", "status": "active", "agent": name, **(self.handoff or {})})
        self._audit("agent_joined", agent=name)

    def agent_audio(self, pcm: bytes) -> None:
        if self.mode == "human":
            self.player.enqueue(pcm)

    async def _say_text(self, text: str) -> None:
        async for pcm in self.tts.stream(speech_text(text, self.language), self.language):
            self.player.enqueue(pcm)
        await self.send_json({"type": "agent_text", "text": text})
        await self.player.wait_done()

    def _arm_no_input(self) -> None:
        self._cancel_no_input()
        if not self.closed.is_set() and self.mode == "ai":
            self._no_input_task = self._spawn(self._no_input())

    def _cancel_no_input(self) -> None:
        if self._no_input_task and not self._no_input_task.done():
            self._no_input_task.cancel()

    async def _no_input(self) -> None:
        await asyncio.sleep(self.s.no_input_s)
        if self._capture is not None or self.agent_active or self.mode != "ai":
            return
        self._reprompts += 1
        if self._reprompts >= 2:
            await self._say_once("goodbye")
            await self.close("no_input")
            return
        self._agent_task = self._spawn(self._say_phrase("reprompt", self.language))

    async def _say_once(self, kind: str) -> None:
        self.player.enqueue(await self.phrases.get(kind, self.language))
        await self.player.wait_done()

    def flush(self) -> None:
        """Client says the caller has finished (push-to-talk released, end of an audio file): decide now."""
        cap = self._capture
        if cap is not None and cap.kind == "turn" and not cap.deciding:
            cap.deciding = True
            self._spawn(self._decide_turn(cap, forced=True))

    # ───────── keypad code (caller verification) ─────────

    async def submit_code(self, code: str) -> None:
        """A verification code typed on the keypad (DTMF on a phone line). It never passes through STT or the LLM.
        If it's right, the question that was waiting for verification is answered straight away."""
        if self.mode != "ai":
            return
        self._cancel_no_input()
        if self.agent_active:
            await self._interrupt()
        auth, message, language = await self.brain.verify(self.session_id, code)
        pending = auth.pop("pending_question", None)
        await self.send_json({"type": "auth", **auth})
        self.language = language
        self._agent_task = self._spawn(self._say_then_resume(message, language,
                                                             pending if auth["status"] == "verified" else None))

    async def _say_then_resume(self, text: str, language: str, question: str | None) -> None:
        self._agent_text = text
        await self.send_json({"type": "agent_text", "text": text})
        async for pcm in self.tts.stream(speech_text(text, language), language):
            self.player.enqueue(pcm)
        self._agent_audio_started = True
        await self.player.wait_done()
        if question:
            self._start_reply(Transcript(question, None, 0.0, 0), time.perf_counter(), {"input": "after_verification"})
        else:
            self._arm_no_input()

    # ───────── text turn (testing without audio) ─────────

    async def text_turn(self, text: str) -> None:
        if self.mode != "ai":
            return
        self._cancel_no_input()
        if self.agent_active:
            await self._interrupt()
        self._start_reply(Transcript(text, None, 0.0, 0), time.perf_counter(), {"input": "text"})

    def _emit(self, event: dict) -> None:
        self._spawn(self.send_json(event))

    def _audit(self, kind: str, **detail) -> None:
        if self._on_event:
            try:
                self._on_event(kind, **detail)
            except Exception:  # the audit trail must never break the call
                logger.exception("call event failed")
