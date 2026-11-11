"""Fallback model on rate limits and outages (PHASE-4-PLAN §7.3), as a wrapper around any LLM adapter.

The inner client already makes one bounded retry. If the call still fails, the same request goes to the configured
fallback model, but only for models that passed the same smoke set for that role (LLM_FALLBACK maps primary -> fallback).
A simple breaker, a counter and not a library: 3 failures in 60 s send the next 60 s straight to the fallback.
If there is no fallback, or it fails too, the original error is raised and the conversation says so honestly.
"""

import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)
BREAKER_FAILURES, BREAKER_WINDOW_S, BREAKER_OPEN_S = 3, 60.0, 60.0


class ResilientLLM:
    def __init__(self, inner, fallbacks: dict[str, str]):
        self.inner, self.fallbacks = inner, fallbacks
        self._failures: dict[str, deque] = defaultdict(deque)
        self._open_until: dict[str, float] = {}
        self.stats = {"failures": 0, "fallback_used": 0, "fallback_failed": 0}

    def _use_fallback_first(self, model: str) -> bool:
        return model in self.fallbacks and time.monotonic() < self._open_until.get(model, 0.0)

    def _record_failure(self, model: str) -> None:
        now = time.monotonic()
        window = self._failures[model]
        window.append(now)
        while window and now - window[0] > BREAKER_WINDOW_S:
            window.popleft()
        self.stats["failures"] += 1
        if len(window) >= BREAKER_FAILURES:
            self._open_until[model] = now + BREAKER_OPEN_S
            window.clear()
            logger.warning("llm breaker open", extra={"data": {"model": model, "seconds": BREAKER_OPEN_S}})

    async def json(self, model, system, user, schema, max_tokens=400):
        if self._use_fallback_first(model):
            return await self._json_fallback(model, system, user, schema, max_tokens, None)
        try:
            return await self.inner.json(model, system, user, schema, max_tokens)
        except Exception as e:
            self._record_failure(model)
            if model not in self.fallbacks:
                raise
            return await self._json_fallback(model, system, user, schema, max_tokens, e)

    async def _json_fallback(self, model, system, user, schema, max_tokens, original):
        try:
            data, meta = await self.inner.json(self.fallbacks[model], system, user, schema, max_tokens)
        except Exception:
            self.stats["fallback_failed"] += 1
            if original is not None:
                raise original
            raise
        self.stats["fallback_used"] += 1
        return data, meta | {"fallback_for": model}

    async def stream(self, model, messages, meta, max_tokens=400):
        models = [self.fallbacks[model]] if self._use_fallback_first(model) else [model] + (
            [self.fallbacks[model]] if model in self.fallbacks else [])
        for i, name in enumerate(models):
            started, last = False, i == len(models) - 1
            try:
                async for delta in self.inner.stream(name, messages, meta, max_tokens):
                    started = True
                    yield delta
                if name != model:
                    self.stats["fallback_used"] += 1
                    meta["fallback_for"] = model
                return
            except Exception:
                if started:  # part of the answer was already spoken: switching models mid-sentence is not safe
                    raise
                if name == model:
                    self._record_failure(model)
                if last:
                    if name != model:
                        self.stats["fallback_failed"] += 1
                    raise

    async def close(self) -> None:
        if hasattr(self.inner, "close"):
            await self.inner.close()
