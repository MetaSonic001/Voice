"""Checks against the real Groq API (run with: uv run pytest -m live)."""

import pytest

from app.adapters.llm import OpenAICompatibleLLM
from app.config import Settings
from app.services.conversation import UNDERSTAND_SCHEMA, UNDERSTAND_SYSTEM

pytestmark = pytest.mark.live


@pytest.fixture
async def llm():
    s = Settings()
    if not s.groq_api_key.get_secret_value():
        pytest.skip("GROQ_API_KEY not set")
    client = OpenAICompatibleLLM(s.llm_base_url, s.groq_api_key.get_secret_value())
    yield client, s
    await client.close()


async def test_understanding_returns_strict_json(llm):
    client, s = llm
    data, meta = await client.json(s.understand_model, UNDERSTAND_SYSTEM, "Caller: senior citizen ke liye FD rate kya hai?",
                                   UNDERSTAND_SCHEMA)
    assert data["intent"] == "general" and data["language"] == "hinglish"
    assert "fixed deposit" in data["search_query_en"].lower()
    assert meta["latency_ms"] > 0


async def test_answer_stream_yields_text(llm):
    client, s = llm
    meta: dict = {}
    text = "".join([t async for t in client.stream(s.answer_model, [{"role": "user", "content": "Reply with exactly: OK"}], meta)])
    assert "OK" in text and meta["ttft_ms"] is not None
