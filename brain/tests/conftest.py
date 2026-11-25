import importlib.util
import os
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ROOT, Settings
from app.container import VoiceRuntime, build
from app.main import create_app
from app.voice.phrases import PhraseCache
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker, FakeSTT, FakeTTS, FakeVAD

SAMPLE_DIR = ROOT.parent / "Sample Questions"
KB_DIR = SAMPLE_DIR / "Knowledge Base"
WS_SCRIPT = {300: ("What is the minimum balance requirement for the savings account?", 0.0)}


class _TestEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    test_database_url: str = ""


def _test_database_url() -> str:
    """The tests' own database, never the one in DATABASE_URL (they upload documents, delete calls, re-seed the bank).
    TEST_DATABASE_URL (environment or brain/.env), a database whose name ends in _test: dropped and created empty at
    the start of every run. Not set: a throwaway pgserver Postgres in a temp folder ("" = pgserver)."""
    url = (os.environ.get("TEST_DATABASE_URL") or _TestEnv().test_database_url).strip()
    if not url:
        if importlib.util.find_spec("pgserver") is None:
            pytest.exit("No test database. Set TEST_DATABASE_URL in brain/.env, e.g. "
                        "postgresql://au:<password>@127.0.0.1:5432/au_voice_test (the Docker Postgres), "
                        "or install pgserver (Python 3.12 / 3.13 only).", returncode=2)
        return ""
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not name.endswith("_test"):
        pytest.exit(f"TEST_DATABASE_URL must name a database ending in _test (the tests empty it), not '{name}'.",
                    returncode=2)
    import psycopg

    with psycopg.connect(urlunsplit(parts._replace(path="/postgres")), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return url


@pytest.fixture(scope="session")
def container(tmp_path_factory):
    """Full app container with fake models, fake speech providers and a throwaway Postgres (no network)."""
    tmp = tmp_path_factory.mktemp("brain")
    golden = tmp / "golden"
    golden.mkdir()
    settings = Settings(database_url=_test_database_url(), data_dir=tmp / "data", log_dir=tmp / "logs", api_key="", voice_enabled=True,
                        min_rerank_score=0.2, rerank_candidates=8, context_chunks=3, rate_limit_per_min=0,
                        playback_lead_ms=100, mock_bank=True, rerank=True)  # tests run the reranker path (fakes)
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
