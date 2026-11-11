"""Replay cache for evaluation runs (PHASE-4-PLAN §10, tier T1): every real LLM response is stored on disk, keyed by a
hash of the exact request (model, system prompt, user text or all messages, output schema, token limit). Any change to
any of them is a cache miss, so a stale answer is never reused; re-running an unchanged item costs no Groq request.

Used only for evals (LLM_REPLAY_DIR). Stored files hold the redacted request preview and the response, nothing else;
the folder can be deleted at any time.
"""

import hashlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from app.services.privacy import redact


class ReplayLLM:
    def __init__(self, inner, cache_dir: Path):
        self.inner, self.dir = inner, Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = self.misses = 0

    @staticmethod
    def key(kind: str, model: str, *request) -> str:
        blob = json.dumps([kind, model, request], sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _load(self, key: str) -> dict | None:
        path = self.dir / f"{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None

    def _store(self, key: str, kind: str, model: str, preview: str, response, meta: dict) -> None:
        entry = {"kind": kind, "model": model, "request_preview": redact(preview)[:400], "response": response,
                 "meta": {k: v for k, v in meta.items() if isinstance(v, (int, float, str, bool, type(None)))}}
        tmp = self.dir / f"{key}.tmp"
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / f"{key}.json")

    async def json(self, model, system, user, schema, max_tokens=400):
        key = self.key("json", model, system, user, schema, max_tokens)
        if (hit := self._load(key)) is not None:
            self.hits += 1
            return hit["response"], hit["meta"] | {"cached": True}
        self.misses += 1
        data, meta = await self.inner.json(model, system, user, schema, max_tokens)
        self._store(key, "json", model, user, data, meta)
        return data, meta

    async def stream(self, model, messages, meta, max_tokens=400) -> AsyncIterator[str]:
        key = self.key("stream", model, messages, max_tokens)
        if (hit := self._load(key)) is not None:
            self.hits += 1
            meta.update(hit["meta"])
            meta["cached"] = True
            for part in re.split(r"(\s+)", hit["response"]):
                if part:
                    yield part
            return
        self.misses += 1
        text = ""
        async for delta in self.inner.stream(model, messages, meta, max_tokens):
            text += delta
            yield delta
        self._store(key, "stream", model, messages[-1]["content"], text, meta)

    async def close(self) -> None:
        if hasattr(self.inner, "close"):
            await self.inner.close()
