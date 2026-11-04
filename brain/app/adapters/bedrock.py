"""AWS Bedrock LLM (Converse API) - same interface as OpenAICompatibleLLM.

- `json`   : forced tool call whose input schema is the requested JSON schema (reliable structured output)
- `stream` : ConverseStream, bridged from boto3's blocking iterator to async without blocking the event loop
Credentials come from the standard AWS chain (env vars, profile, instance/task role). Use a model available in
your region (e.g. an `apac.` inference profile in ap-south-1).
"""

import asyncio
import threading
import time
from collections.abc import AsyncIterator

from app.adapters.llm import LLMError


def _split(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    system = [{"text": m["content"]} for m in messages if m["role"] == "system"]
    convo = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
    return system, convo


class BedrockLLM:
    def __init__(self, region: str, timeout_s: float = 20.0, client=None):
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=timeout_s, connect_timeout=5, retries={"max_attempts": 3, "mode": "adaptive"}))
        self._client = client

    async def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int = 400) -> tuple[dict, dict]:
        start = time.perf_counter()
        response = await asyncio.to_thread(
            self._client.converse,
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
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
                    "retries": 0}
        raise LLMError(f"{model} returned no structured output")

    async def stream(self, model: str, messages: list[dict], meta: dict, max_tokens: int = 400) -> AsyncIterator[str]:
        system, convo = _split(messages)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        start = time.perf_counter()
        meta.update(model=model, ttft_ms=None, prompt_tokens=0, completion_tokens=0, retries=0)

        def worker():
            try:
                response = self._client.converse_stream(modelId=model, system=system, messages=convo,
                                                        inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
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
                elif kind == "error":
                    raise LLMError(f"Bedrock stream failed: {value}") from value
                else:
                    break
        finally:
            stop.set()  # consumer stopped early (e.g. caller interrupted): end the background read
            meta["latency_ms"] = round((time.perf_counter() - start) * 1000)

    async def close(self) -> None:
        pass
