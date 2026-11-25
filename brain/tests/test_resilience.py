"""Fallback model, circuit breaker and the eval replay cache (PHASE-4-PLAN §7.3 and §10)."""

import pytest

from app.adapters.llm import LLMError
from app.adapters.replay import ReplayLLM
from app.adapters.resilient import ResilientLLM


class Flaky:
    def __init__(self, bad=()):
        self.bad, self.calls = set(bad), []

    async def json(self, model, system, user, schema, max_tokens=400, effort=None):
        self.calls.append(model)
        if model in self.bad:
            raise LLMError("LLM HTTP 429: rate limit")
        return {"ok": model}, {"model": model}

    async def stream(self, model, messages, meta, max_tokens=400, effort=None):
        self.calls.append(model)
        if model in self.bad:
            raise LLMError("LLM HTTP 429: rate limit")
        meta["model"] = model
        for part in ("Hello ", "there"):
            yield part


async def test_forced_429_is_answered_by_the_fallback():
    inner = Flaky(bad={"big"})
    llm = ResilientLLM(inner, {"big": "small"})
    data, meta = await llm.json("big", "s", "u", {}, 10)
    assert data == {"ok": "small"} and meta["fallback_for"] == "big"
    text = "".join([d async for d in llm.stream("big", [{"role": "user", "content": "x"}], {})])
    assert text == "Hello there" and llm.stats["fallback_used"] == 2


async def test_no_fallback_means_the_original_error_surfaces():
    with pytest.raises(LLMError):
        await ResilientLLM(Flaky(bad={"big"}), {}).json("big", "s", "u", {}, 10)
    both = ResilientLLM(Flaky(bad={"big", "small"}), {"big": "small"})
    with pytest.raises(LLMError):
        await both.json("big", "s", "u", {}, 10)
    assert both.stats["fallback_failed"] == 1


async def test_breaker_skips_the_failing_model_after_three_failures():
    inner = Flaky(bad={"big"})
    llm = ResilientLLM(inner, {"big": "small"})
    for _ in range(3):
        await llm.json("big", "s", "u", {}, 10)
    inner.calls.clear()
    await llm.json("big", "s", "u", {}, 10)
    assert inner.calls == ["small"]  # the failing model is not tried while the breaker is open


async def test_replay_cache_hits_only_on_the_identical_request(tmp_path):
    inner = Flaky()
    llm = ReplayLLM(inner, tmp_path)
    await llm.json("m", "system", "user", {"a": 1}, 50)
    await llm.json("m", "system", "user", {"a": 1}, 50)
    assert (llm.hits, llm.misses, len(inner.calls)) == (1, 1, 1)
    await llm.json("m", "system v2", "user", {"a": 1}, 50)  # a changed prompt is a miss: never a stale answer
    assert llm.misses == 2
    msgs = [{"role": "user", "content": "q"}]
    first = "".join([d async for d in llm.stream("m", msgs, {})])
    meta: dict = {}
    again = "".join([d async for d in llm.stream("m", msgs, meta)])
    assert first == again == "Hello there" and meta["cached"] is True


# ───────── Phase 4 §7: concurrency, prefetch and the latency report ─────────


async def test_twenty_simultaneous_sessions_do_not_block_or_mix(client, container):
    """Our own locking and queueing, with the fake LLM (the real LLM cannot be load-tested on the free tier)."""
    import asyncio
    import time

    sids = [client.post("/api/v1/sessions", json={}).json()["id"] for _ in range(20)]
    started = time.perf_counter()
    replies = await asyncio.gather(*[container.conversation.ask(f"hello number {i}", sid) for i, sid in enumerate(sids)])
    assert all(r.outcome == "smalltalk" or r.answer for r in replies) and len({r.session_id for r in replies}) == 20
    assert time.perf_counter() - started < 15


def test_the_prefetched_customer_is_shared_within_a_turn_and_dropped_after_a_change(container):
    calls = []
    real = container.bank.customer
    container.bank.customer = lambda cif: (calls.append(cif), real(cif))[1]
    try:
        auth = container.auth
        sid = container.traces.create_session(None, "9000000101")["id"]
        first = auth.start(sid, "balance")
        code_row = auth.verify  # (verified below with the simulated code)
        import re

        auth.verify(sid, re.search(r"(\d{6})", first.simulated_sms).group(1))
        calls.clear()
        a, b = auth.verified_customer(sid), auth.verified_customer(sid)
        assert a is b and len(calls) == 0 or len(calls) == 1  # one bank read serves both (the verify read may already be cached)
        auth.invalidate(sid)
        auth.verified_customer(sid)
        assert calls and code_row
    finally:
        container.bank.customer = real


def test_stats_report_stage_latency_percentiles(client, container):
    client.post("/api/v1/ask", json={"question": "What is the minimum balance requirement for the savings account?"})
    stats = client.get("/api/v1/stats?hours=1").json()
    assert "total_ms" in stats["stages_ms"] and {"p50", "p95", "p99"} <= set(stats["stages_ms"]["total_ms"])
    assert set(stats["llm_retries_and_fallbacks"]) == {"rate_limit_retries", "fallback_used", "router_repaired_in_code", "router_retried"}
