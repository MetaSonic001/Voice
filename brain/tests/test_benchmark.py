"""The /bench page's numbers: turns grouped by setup, a frozen benchmark, deltas with the right direction."""

from types import SimpleNamespace

from app.services.benchmark import _row, setup_of


def _settings(**kw):
    base = dict(llm_provider="bedrock", understand_model="in.openai.gpt-5.6-luna", answer_model="in.openai.gpt-5.6-luna",
                understand_reasoning_effort="low", answer_reasoning_effort="none", stt_provider="sarvam",
                sarvam_stt_model="saaras:v4", tts_provider="sarvam", sarvam_tts_model="bulbul:v4-flash",
                search_provider="local", search_runtime="torch", device="auto", vector_store="pgvector", rerank=True)
    return SimpleNamespace(**(base | kw))


def test_a_setup_change_is_a_new_group_and_groq_effort_is_written_as_sent():
    a, setup = setup_of(_settings(), "v1")
    assert setup["answer_effort"] == "none" and len(a) == 10
    assert setup_of(_settings(), "v1")[0] == a  # same setup, same id
    assert setup_of(_settings(answer_reasoning_effort="low"), "v1")[0] != a  # effort change = new setup
    assert setup_of(_settings(), "v2")[0] != a  # prompt change = new setup
    _, groq = setup_of(_settings(llm_provider="openai", answer_reasoning_effort="none"), "v1")
    assert groq["answer_effort"] == "low"  # gpt-oss on Groq has no "none": it is sent as low


def test_delta_direction():
    faster = _row("latency", "llm_ttft_ms", "x", True, {"p50": 300, "p95": 500}, {"p50": 500, "p95": 900})
    assert faster["delta"] == -200 and faster["delta_pct"] == -40.0 and faster["better"] is True
    worse = _row("quality", "smoke_pass_pct", "x", False, 70.0, 80.0)
    assert worse["better"] is False
    neutral = _row("quality", "refused_pct", "x", None, 5.0, 3.0)
    assert neutral["better"] is None
    assert _row("latency", "k", "x", True, None, {"p50": 1})["delta"] is None  # nothing to compare yet


def test_bench_api_groups_real_turns_and_pins_a_frozen_benchmark(client, container):
    client.post("/api/v1/ask", json={"question": "What is the minimum balance requirement for the savings account?"})
    r = client.get("/api/v1/bench").json()
    assert r["current_setup_id"] == container.conversation.setup_id
    assert r["current"]["turns"] >= 1
    assert "first_reply_ms" in r["current"]["latency"]  # timed on every path
    assert {row["key"] for row in r["rows"]} >= {"voice_to_voice_ms", "understand_ms", "llm_ttft_ms", "smoke_pass_pct"}
    pinned = client.post("/api/v1/bench/baseline", json={"label": "fake LLM"}).json()
    assert pinned["setup_id"] == container.conversation.setup_id and pinned["label"] == "fake LLM"
    turns_then = pinned["snapshot"]["turns"]
    client.post("/api/v1/ask", json={"question": "What is the minimum balance requirement for the savings account?"})
    r = client.get("/api/v1/bench").json()
    assert r["baseline_from"] == "pinned" and r["baseline_is_current"]
    assert r["baseline"]["turns"] == turns_then  # frozen: more turns don't move the benchmark
    assert r["current"]["turns"] == turns_then + 1
    assert client.delete("/api/v1/bench/baseline").json() == {"baseline_from": "auto"}
    assert client.post("/api/v1/bench/baseline", json={"setup_id": "nosuchsetup"}).status_code == 404
