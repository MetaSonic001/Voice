"""AWS Bedrock LLM (Converse API) - same interface as OpenAICompatibleLLM.

- `json`   : forced tool call whose input schema is the requested JSON schema (reliable structured output)
- `stream` : ConverseStream, bridged from boto3's blocking iterator to async without blocking the event loop
Credentials come from the standard AWS chain (env vars, profile, instance/task role). For customer data use an India-only
model ID: the India geo inference profile (Mumbai + Hyderabad; allow both ap-south-1 and ap-south-2 in IAM). Never an
`apac.` or global profile: those can process requests outside India. (23 Nov 2026: GPT-5.6 Luna and Terra have India
profiles, `in.openai.gpt-5.6-luna` / `in.openai.gpt-5.6-terra`, Mumbai + Hyderabad only.)
- reasoning effort: per call (`UNDERSTAND_REASONING_EFFORT`, `ANSWER_REASONING_EFFORT`; default low) in
  `additionalModelRequestFields` for OpenAI models. Cached prompt tokens (`cacheReadInputTokens`) are kept in `meta`.
Stateless on purpose: our own Postgres holds the conversation (docs/adr/0026-stateless-llm-calls.md).
"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import AsyncIterator

from app.adapters.llm import LLMError

logger = logging.getLogger(__name__)


def _split(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    system = [{"text": m["content"]} for m in messages if m["role"] == "system"]
    convo = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
    return system, convo


def _rejects(error: Exception, field: str) -> bool:
    """A ValidationException about one request field (e.g. GPT-6 Luna and newer Claude models refuse `temperature`)."""
    return field in str(error).lower() and "ValidationException" in type(error).__name__ + str(error)


class BedrockLLM:
    # Models that refused `temperature` / the reasoning field, learned on the first call; later calls skip the failed attempt.
    _no_temperature: set[str] = set()
    _no_reasoning: set[str] = set()

    def _inference(self, model: str, max_tokens: int | None) -> dict:
        config = {} if model in self._no_temperature else {"temperature": 0}
        if max_tokens:
            config["maxTokens"] = max_tokens
        return config

    def _extra(self, model: str, effort: str | None = None) -> dict:
        """Model-specific fields (Converse `additionalModelRequestFields`): how long an OpenAI model reasons before it
        answers. GPT-6 models (Luna, Sol) take `reasoning.effort` and reject a flat `reasoning_effort`; gpt-oss takes the
        flat field (community example, not in AWS's docs). Other models get nothing."""
        effort = (effort or self.reasoning_effort or "").strip().lower()
        if not effort or model in self._no_reasoning or "openai." not in model:
            return {}
        if "gpt-oss" in model:  # low / medium / high only
            return {"additionalModelRequestFields": {"reasoning_effort": effort if effort in ("low", "medium", "high") else "low"}}
        return {"additionalModelRequestFields": {"reasoning": {"effort": effort}}}

    def _effort_sent(self, model: str, effort: str | None) -> str | None:
        fields = self._extra(model, effort).get("additionalModelRequestFields") or {}
        return fields.get("reasoning_effort") or (fields.get("reasoning") or {}).get("effort")

    def _call(self, fn, model: str, max_tokens: int | None, effort: str | None = None, **kwargs):
        """Call converse/converse_stream. A model that rejects temperature or the reasoning field is remembered and the
        call is retried without it (at most once per field)."""
        for _ in range(3):
            try:
                return fn(modelId=model, inferenceConfig=self._inference(model, max_tokens), **self._extra(model, effort), **kwargs)
            except Exception as e:
                if model not in self._no_temperature and _rejects(e, "temperature"):
                    self._no_temperature.add(model)
                elif model not in self._no_reasoning and self._extra(model, effort) and _rejects(e, "reasoning"):
                    self._no_reasoning.add(model)
                    logger.warning("bedrock model rejected the reasoning effort; sending none",
                                   extra={"data": {"model": model, "effort": self.reasoning_effort}})
                else:
                    raise
        return fn(modelId=model, inferenceConfig=self._inference(model, max_tokens), **self._extra(model, effort), **kwargs)

    def __init__(self, region: str, timeout_s: float = 20.0, client=None, api_key: str = "", reasoning_effort: str = "low"):
        self.reasoning_effort = (reasoning_effort or "").strip().lower()
        if client is None:
            import boto3

            if api_key:  # a Bedrock API key: boto3 reads it only from this environment variable
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=timeout_s, connect_timeout=5, retries={"max_attempts": 3, "mode": "adaptive"}))
        self._client = client

    async def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int | None = None,
                   effort: str | None = None) -> tuple[dict, dict]:
        start = time.perf_counter()
        response = await asyncio.to_thread(
            self._call,
            self._client.converse,
            model,
            max_tokens,
            effort,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            toolConfig={"tools": [{"toolSpec": {"name": "output", "description": "Return the result",
                                                "inputSchema": {"json": schema}}}],
                        "toolChoice": {"tool": {"name": "output"}}},
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                usage = response.get("usage", {})
                return block["toolUse"]["input"], {
                    "model": model, "latency_ms": round((time.perf_counter() - start) * 1000),
                    "prompt_tokens": usage.get("inputTokens", 0), "completion_tokens": usage.get("outputTokens", 0),
                    "cache_read_tokens": usage.get("cacheReadInputTokens", 0), "effort": self._effort_sent(model, effort),
                    "retries": 0}
        raise LLMError(f"{model} returned no structured output")

    async def stream(self, model: str, messages: list[dict], meta: dict, max_tokens: int | None = None,
                     effort: str | None = None) -> AsyncIterator[str]:
        system, convo = _split(messages)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        start = time.perf_counter()
        meta.update(model=model, ttft_ms=None, prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, retries=0,
                    effort=self._effort_sent(model, effort))

        def worker():
            try:
                response = self._call(self._client.converse_stream, model, max_tokens, effort, system=system, messages=convo)
                for event in response["stream"]:
                    if stop.is_set():
                        break
                    if "contentBlockDelta" in event:
                        text = event["contentBlockDelta"]["delta"].get("text")
                        if text:
                            loop.call_soon_threadsafe(queue.put_nowait, ("text", text))
                    elif "metadata" in event:
                        loop.call_soon_threadsafe(queue.put_nowait, ("usage", event["metadata"].get("usage", {})))
            except Exception as e:  # surfaced to the consumer below
                loop.call_soon_threadsafe(queue.put_nowait, ("error", e))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

        threading.Thread(target=worker, daemon=True, name="bedrock-stream").start()
        try:
            while True:
                kind, value = await queue.get()
                if kind == "text":
                    if meta["ttft_ms"] is None:
                        meta["ttft_ms"] = round((time.perf_counter() - start) * 1000)
                    yield value
                elif kind == "usage":
                    meta["prompt_tokens"] = value.get("inputTokens", 0)
                    meta["completion_tokens"] = value.get("outputTokens", 0)
                    meta["cache_read_tokens"] = value.get("cacheReadInputTokens", 0)
                elif kind == "error":
                    raise LLMError(f"Bedrock stream failed: {value}") from value
                else:
                    break
        finally:
            stop.set()  # consumer stopped early (e.g. caller interrupted): end the background read
            meta["latency_ms"] = round((time.perf_counter() - start) * 1000)

    async def close(self) -> None:
        pass
