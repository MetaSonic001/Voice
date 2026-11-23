"""OpenAI-compatible chat client (Groq by default). Strict-JSON calls and streamed text, with retries.

One persistent HTTP/2-capable client keeps connections warm, which matters for latency.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


def _cap(max_tokens: int | None) -> dict:
    # No cap by default: a capped call that runs out mid-answer fails the turn (gpt-oss counts its reasoning too).
    return {"max_completion_tokens": max_tokens} if max_tokens else {}


class LLMError(RuntimeError):
    def __init__(self, message: str, body: str = ""):
        super().__init__(message)
        self.body = body  # the provider's full error body (e.g. Groq's failed_generation); not logged


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 20.0, max_retries: int = 3,
                 retry_budget_s: float = 4.0):
        if not api_key:
            raise LLMError("GROQ_API_KEY is not set (put it in brain/.env)")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=300),
        )
        self._max_retries = max_retries
        self._retry_budget_s = retry_budget_s  # total time a call may spend waiting to retry: a caller is on the line

    @staticmethod
    def _model_params(model: str, effort: str | None = None) -> dict:
        # gpt-oss models reason before answering; keep that short for latency. They take low / medium / high only, so
        # "none" (and no effort given) is sent as low.
        if "gpt-oss" not in model:
            return {}
        return {"reasoning_effort": effort if effort in ("low", "medium", "high") else "low", "include_reasoning": False}

    async def _post(self, payload: dict, stream: bool = False) -> httpx.Response:
        retries, waited = 0, 0.0
        while True:
            request = self._client.build_request("POST", "/chat/completions", json=payload)
            response = await self._client.send(request, stream=stream)
            if response.status_code < 400:
                response.retries = retries  # type: ignore[attr-defined]
                return response
            full = (await response.aread()).decode(errors="replace")[:20000]
            body = full[:500]
            await response.aclose()
            wait = float(response.headers.get("retry-after", 0) or 0) or 0.5 * 2**retries
            if response.status_code in RETRY_STATUS and retries < self._max_retries and waited + wait <= self._retry_budget_s:
                logger.warning("llm retry", extra={"data": {"status": response.status_code, "wait_s": wait}})
                await asyncio.sleep(wait)
                retries, waited = retries + 1, waited + wait
                continue
            raise LLMError(f"LLM HTTP {response.status_code}: {body}", body=full)

    async def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int | None = None,
                   effort: str | None = None) -> tuple[dict, dict]:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            **_cap(max_tokens),
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}},
            **self._model_params(model, effort),
        }
        start = time.perf_counter()
        response = await self._post(payload)
        body = response.json()
        content = body["choices"][0]["message"].get("content") or "{}"
        usage = body.get("usage") or {}
        meta = {
            "model": model,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cache_read_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "effort": self._model_params(model, effort).get("reasoning_effort"),
            "retries": response.retries,
        }
        try:
            return json.loads(content), meta
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON from {model}: {content[:200]}") from e

    async def stream(self, model: str, messages: list[dict], meta: dict, max_tokens: int | None = None,
                     effort: str | None = None) -> AsyncIterator[str]:
        """Yields text deltas; fills `meta` (ttft, latency, tokens, retries) as it goes."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            **_cap(max_tokens),
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._model_params(model, effort),
        }
        start = time.perf_counter()
        meta.update(model=model, ttft_ms=None, prompt_tokens=0, completion_tokens=0, cache_read_tokens=0,
                    effort=self._model_params(model, effort).get("reasoning_effort"))
        response = await self._post(payload, stream=True)
        meta["retries"] = response.retries
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                usage = event.get("usage") or (event.get("x_groq") or {}).get("usage")
                if usage:
                    meta["prompt_tokens"] = usage.get("prompt_tokens", 0)
                    meta["completion_tokens"] = usage.get("completion_tokens", 0)
                    meta["cache_read_tokens"] = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                for choice in event.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        if meta["ttft_ms"] is None:
                            meta["ttft_ms"] = round((time.perf_counter() - start) * 1000)
                        yield delta
        finally:
            await response.aclose()
            meta["latency_ms"] = round((time.perf_counter() - start) * 1000)

    async def close(self) -> None:
        await self._client.aclose()
