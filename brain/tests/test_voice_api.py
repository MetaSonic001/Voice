"""Voice endpoints end to end (fake speech providers, real brain pipeline with fake models)."""

import base64
import json
import time

import pytest

from app.voice.audio import silence, to_wav, tone
from tests.test_api_flow import KB_V1

RATE = 16000


@pytest.fixture(scope="module")
def kb(client, container):
    r = client.post("/v1/documents", files={"file": ("voice-kb.md", KB_V1.replace(b"AU Test", b"AU Voice Test").replace(b"Savings Account", b"Voice Account"))})
    assert r.status_code == 201
    container.worker.run_pending()
    return r.json()


def _question_wav() -> bytes:
    return to_wav(silence(200, RATE) + tone(800, RATE, freq=300, amplitude=6000) + silence(300, RATE), RATE)


def test_stt_and_tts_endpoints(client):
    stt = client.post("/v1/voice/stt", files={"file": ("q.wav", _question_wav(), "audio/wav")}).json()
    assert stt["text"].startswith("What is the minimum balance")
    r = client.post("/v1/voice/tts", json={"text": "Hello there", "language": "en"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert client.post("/v1/voice/tts", json={"text": "x", "language": "fr"}).status_code == 422


def test_voice_turn_from_file(client, kb):
    r = client.post("/v1/voice/turn", files={"file": ("q.wav", _question_wav(), "audio/wav")}).json()
    assert r["outcome"] == "answered" and "10,000" in r["answer"]
    assert base64.b64decode(r["audio_wav_base64"])[:4] == b"RIFF"
    assert {"stt_ms", "understand_ms", "first_audio_ms", "total_ms"} <= set(r["timings"])
    trace = client.get(f"/v1/traces/{r['trace_id']}").json()
    assert "voice" in trace["data"]


def test_live_call_over_websocket(client, kb):
    events, audio = [], bytearray()
    with client.websocket_connect("/v1/voice/stream") as ws:
        ws.send_text(json.dumps({"type": "start", "greet": False, "language": "en"}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        pcm = silence(200, RATE) + tone(800, RATE, freq=300, amplitude=6000) + silence(1200, RATE)
        for i in range(0, len(pcm), 640):
            ws.send_bytes(pcm[i:i + 640])
        while True:
            message = ws.receive()
            if message.get("bytes"):
                audio.extend(message["bytes"])
                continue
            event = json.loads(message["text"])
            events.append(event)
            if event["type"] == "turn_metrics":
                break
        ws.send_text(json.dumps({"type": "stop"}))
    kinds = [e["type"] for e in events]
    assert "user" in kinds and "agent_text" in kinds and "reply_done" in kinds
    done = next(e for e in events if e["type"] == "reply_done")
    assert done["outcome"] == "answered"
    assert len(audio) > 0
    metrics = events[-1]
    assert metrics["voice_to_voice_ms"] >= 0


def test_live_calls_over_the_limit_are_refused(client, container):
    """Admission control: one process serves `max_live_calls` calls; the next gets "busy" and close code 1013."""
    from starlette.websockets import WebSocketDisconnect

    from app.api.voice import BUSY_CLOSE, live_calls

    def wait_free():  # a call holds its line until its clean-up (recording, audit) has finished
        for _ in range(100):
            if live_calls() == 0:
                return
            time.sleep(0.05)
        raise AssertionError("a line was never freed")

    wait_free()
    old = container.settings.max_live_calls
    container.settings.max_live_calls = 1
    try:
        with client.websocket_connect("/v1/voice/stream") as first:
            first.send_text(json.dumps({"type": "start", "greet": False, "language": "en"}))
            assert json.loads(first.receive_text())["type"] == "ready"
            assert live_calls() == 1
            assert client.get("/health/ready").json()["checks"]["live_calls"] == "1/1"
            with client.websocket_connect("/v1/voice/stream") as second:
                busy = json.loads(second.receive_text())
                assert busy["type"] == "busy" and busy["limit"] == 1
                with pytest.raises(WebSocketDisconnect) as closed:
                    second.receive_text()
                assert closed.value.code == BUSY_CLOSE
            first.send_text(json.dumps({"type": "stop"}))
        wait_free()
        with client.websocket_connect("/v1/voice/stream") as third:  # a line is free again
            third.send_text(json.dumps({"type": "start", "greet": False, "language": "en"}))
            assert json.loads(third.receive_text())["type"] == "ready"
            third.send_text(json.dumps({"type": "stop"}))
    finally:
        container.settings.max_live_calls = old
