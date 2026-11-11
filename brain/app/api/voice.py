"""Voice endpoints (mounted only when VOICE_ENABLED=true).

REST (test each piece, or a whole turn from an audio file):
  GET  /v1/voice/providers      which STT / TTS / VAD are active
  POST /v1/voice/stt            audio file -> transcript
  POST /v1/voice/tts            text -> WAV
  POST /v1/voice/turn           audio file -> transcript -> brain -> reply text + WAV (base64) + timings
WebSocket (live call):
  /v1/voice/stream              send {"type":"start","caller_number":...} then binary PCM16 mono frames; receive
                                JSON events + PCM16 audio. {"type":"otp","code":...} carries a keypad code.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

import numpy as np

from app.api.deps import container, require_key
from app.container import Container
from app.metrics import observe_turn
from app.services.audit import mask_number
from app.services.recordings import CallRecorder
from app.voice.audio import decode, duration_ms, to_wav
from app.voice.session import LIVE_SESSIONS, VoiceSession
from app.voice.turntaking import is_noise

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def _voice(c: Container):
    if not c.voice:
        raise HTTPException(404, "voice is disabled (VOICE_ENABLED=false)")
    return c.voice


async def _read_audio(file: UploadFile, rate: int) -> bytes:
    data = await file.read()
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(400, "audio must be between 1 byte and 10 MB")
    try:
        return await asyncio.to_thread(decode, data, rate)
    except Exception as e:
        raise HTTPException(415, f"could not decode audio: {type(e).__name__}") from e


@router.get("/providers")
def providers(c: Container = Depends(container)):
    v = _voice(c)
    s = c.settings
    return {"stt": v.stt.name, "tts": v.tts.name, "vad": "silero", "llm": s.llm_provider,
            "models": {"understand": s.understand_model, "answer": s.answer_model, "stt": s.stt_model},
            "audio": {"format": "pcm_s16le", "channels": 1, "sample_rate": s.audio_rate}}


@router.post("/stt", dependencies=[Depends(require_key)])
async def stt(file: UploadFile = File(...), c: Container = Depends(container)):
    v = _voice(c)
    pcm = await _read_audio(file, c.settings.audio_rate)
    result = await v.stt.transcribe(pcm, c.settings.audio_rate)
    return {"text": result.text, "language": result.language, "no_speech": round(result.no_speech, 3),
            "latency_ms": result.latency_ms, "audio_ms": round(duration_ms(pcm, c.settings.audio_rate))}


class TTSBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    language: str = Field("en", pattern="^(en|hi|hinglish)$")


@router.post("/tts", dependencies=[Depends(require_key)])
async def tts(body: TTSBody, c: Container = Depends(container)):
    v = _voice(c)
    start, first = time.perf_counter(), None
    pcm = bytearray()
    async for chunk in v.tts.stream(body.text, body.language):
        first = first or time.perf_counter()
        pcm += chunk
    return Response(to_wav(bytes(pcm), c.settings.audio_rate), media_type="audio/wav",
                    headers={"x-first-audio-ms": str(round(((first or start) - start) * 1000)),
                             "x-total-ms": str(round((time.perf_counter() - start) * 1000))})


@router.post("/turn", dependencies=[Depends(require_key)])
async def turn(background: BackgroundTasks, file: UploadFile = File(...), session_id: str | None = Form(None),
               c: Container = Depends(container)):
    """One full voice turn from a file. Same brain, sentence streaming and TTS as the live call."""
    v = _voice(c)
    rate = c.settings.audio_rate
    if session_id and not c.traces.session_exists(session_id):
        raise HTTPException(404, "session not found")
    pcm = await _read_audio(file, rate)
    t0 = time.perf_counter()
    started_at = datetime.now().astimezone()
    heard = await v.stt.transcribe(pcm, rate)
    timings = {"input": "voice_note", "stt_ms": heard.latency_ms, "stt_no_speech": round(heard.no_speech, 3)}
    if is_noise(heard.text, heard.no_speech):
        return {"transcript": heard.text, "outcome": "no_speech", "timings": timings, "audio_wav_base64": None}
    audio, sentences, reply, first_audio = bytearray(), [], None, None
    async for event in c.conversation.turn(heard.text, session_id, extra_timings=dict(timings)):
        if event.kind == "sentence":
            sentences.append(event.text)
            async for chunk in v.tts.stream(event.speech, event.language):
                first_audio = first_audio or time.perf_counter()
                audio += chunk
        elif event.kind == "done":
            reply = event.reply
    timings.update(reply.timings)
    if first_audio:
        timings["first_audio_ms"] = round((first_audio - t0) * 1000)  # from receiving the audio to first reply audio
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
    observe_turn(reply.outcome, reply.language, timings)
    await asyncio.to_thread(c.traces.add_voice_timings, reply.trace_id, timings)
    if session_id and c.recordings and c.settings.record_calls:  # after the response is sent: no added wait
        clip = np.frombuffer(pcm + bytes(rate // 2 * 2) + bytes(audio), dtype=np.int16)  # question, 0.5 s, answer
        background.add_task(c.recordings.save, session_id, "voice_note", clip, rate, started_at, reply.trace_id)
    return {"transcript": heard.text, "stt_language": heard.language, "outcome": reply.outcome, "language": reply.language,
            "answer": reply.answer, "sentences": sentences, "citations": reply.citations, "route": reply.route,
            "trace_id": reply.trace_id, "timings": timings, "auth": reply.auth,
            "audio_wav_base64": base64.b64encode(to_wav(bytes(audio), rate)).decode() if audio else None}


@router.websocket("/stream")
async def stream(ws: WebSocket):
    c: Container = ws.app.state.container
    expected = c.settings.api_key
    if expected and (ws.headers.get("x-api-key") or ws.query_params.get("key")) != expected:
        await ws.close(code=4401)
        return
    if not c.voice:
        await ws.close(code=4404)
        return
    await ws.accept()
    lock = asyncio.Lock()
    open_ = True

    async def send_json(event: dict):
        if open_:
            async with lock:
                await ws.send_text(json.dumps(event, ensure_ascii=False))

    async def send_audio(pcm: bytes):
        if open_:
            async with lock:
                await ws.send_bytes(pcm)

    async def save_voice_timings(trace_id: str, timings: dict):
        observe_turn("voice", "-", timings)
        await asyncio.to_thread(c.traces.add_voice_timings, trace_id, timings)

    session: VoiceSession | None = None
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if first.get("type") != "start":
            await ws.close(code=4400)
            return
        session_id = first.get("session_id")
        if session_id and not c.traces.session_exists(session_id):
            session_id = None
        caller = first.get("caller_number")
        caller = str(caller)[:20] if caller and str(caller).replace("+", "").replace(" ", "").isdigit() else None
        session_id = session_id or str(c.traces.create_session(first.get("language"), caller)["id"])
        recorder = CallRecorder(c.settings.audio_rate) if c.settings.record_calls and c.recordings else None
        loop = asyncio.get_running_loop()

        def on_event(kind: str, **detail):  # call timeline -> database, off the event loop
            loop.run_in_executor(None, lambda: c.audit.event(session_id, kind, **detail))
            if kind == "error":
                loop.run_in_executor(None, lambda: c.audit.system_error(detail.get("source", "voice"), detail.get("error", ""),
                                                                         session_id))

        on_event("call_started", caller=mask_number(caller), language=first.get("language"), recorded=bool(recorder))
        session = VoiceSession(brain=c.conversation, stt=c.voice.stt, tts=c.voice.tts, vad=c.voice.vad_factory(),
                               phrases=c.voice.phrases, settings=c.settings, send_json=send_json, send_audio=send_audio,
                               session_id=session_id, on_voice_timings=save_voice_timings,
                               greeting_language=first.get("language") if first.get("language") in ("en", "hi") else "hi",
                               recorder=recorder, on_event=on_event)
        LIVE_SESSIONS[session_id] = session
        await session.start(greet=first.get("greet", True))
        while not session.closed.is_set():
            receive = asyncio.ensure_future(ws.receive())
            closed = asyncio.ensure_future(session.closed.wait())
            done, _ = await asyncio.wait({receive, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                receive.cancel()
                break
            closed.cancel()
            message = receive.result()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                session.feed(message["bytes"])
            elif message.get("text"):
                event = json.loads(message["text"])
                if event.get("type") == "stop":
                    break
                if event.get("type") == "flush":  # caller finished speaking (push-to-talk / end of file)
                    session.flush()
                if event.get("type") == "text" and event.get("text"):
                    await session.text_turn(str(event["text"])[:1000])
                if event.get("type") == "otp" and c.auth:  # keypad code: straight to verification, never to STT/LLM
                    await session.submit_code(str(event.get("code", ""))[:8])
    except (TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        pass
    except Exception:
        logger.exception("voice stream failed")
    finally:
        if session:
            LIVE_SESSIONS.pop(session.session_id, None)
            await session.close("client_closed")
            if session.handoff and c.handoffs:  # hung up while waiting, or the call with the person ended
                h = await asyncio.to_thread(c.handoffs.get, session.handoff["id"])
                if h and h["status"] in ("waiting", "active"):
                    await asyncio.to_thread(c.handoffs.set_status, h["id"],
                                            "abandoned" if h["status"] == "waiting" else "completed",
                                            **({} if h["status"] == "waiting" else {"disposition": "call ended"}))
        open_ = False
        try:
            await ws.close()
        except Exception:
            pass
        if session:  # audit: end of call, then the recording (encoding takes a few seconds, in a thread)
            try:
                reason = {"no_input": "ended: no response from the caller"}.get(session.end_reason, "caller hung up")
                await asyncio.to_thread(c.audit.end_call, session.session_id, reason)
                if c.reviews and c.settings.judge_mode != "off":  # in the background: nothing waits for the review
                    asyncio.create_task(c.reviews.review(session.session_id))
                if session.recorder and session.recorder.duration_ms > 500:
                    await asyncio.to_thread(c.recordings.save, session.session_id, "call", session.recorder.stereo(),
                                            c.settings.audio_rate, session.recorder.started_at)
            except Exception:
                logger.exception("saving the call record failed")

