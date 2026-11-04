import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.container import VoiceRuntime, build
from app.main import create_app
from app.voice.phrases import PhraseCache
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker, FakeSTT, FakeTTS, FakeVAD

SAMPLE_DIR = ROOT.parent / "Sample Questions"
KB_DIR = SAMPLE_DIR / "Knowledge Base"
WS_SCRIPT = {300: ("What is the minimum balance requirement for the savings account?", 0.0)}


@pytest.fixture(scope="session")
def container(tmp_path_factory):
    """Full app container with fake models, fake speech providers and a throwaway Postgres (no network)."""
    tmp = tmp_path_factory.mktemp("brain")
    golden = tmp / "golden"
    golden.mkdir()
    settings = Settings(data_dir=tmp / "data", log_dir=tmp / "logs", api_key="", voice_enabled=True,
                        min_rerank_score=0.2, rerank_candidates=8, context_chunks=3, rate_limit_per_min=0,
                        playback_lead_ms=100)
    tts = FakeTTS(settings.audio_rate)
    voice = VoiceRuntime(FakeSTT(WS_SCRIPT), tts, FakeVAD, PhraseCache(tts))
    c = build(settings, start_worker=False, golden_dir=golden, reports_dir=tmp / "reports",
              overrides={"embedder": FakeEmbedder(), "reranker": FakeReranker(), "llm": FakeLLM(), "voice": voice})
    yield c
    c.db.close()


@pytest.fixture(scope="session")
def client(container):
    with TestClient(create_app(container)) as tc:
        yield tc
