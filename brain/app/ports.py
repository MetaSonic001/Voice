"""Interfaces for everything swappable. Services depend on these, never on a concrete adapter."""

from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from app.domain import Customer, ParsedDoc, Transcript


class Parser(Protocol):
    def parse(self, path: Path, options: dict) -> ParsedDoc: ...


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance in [0, 1] for each passage."""
        ...


class LLM(Protocol):
    async def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int = 400) -> tuple[dict, dict]:
        """Returns (parsed JSON, meta: tokens, latency, retries)."""
        ...

    def stream(self, model: str, messages: list[dict], meta: dict, max_tokens: int = 400) -> AsyncIterator[str]:
        """Yields text deltas and fills `meta` with ttft, latency and token counts."""
        ...


class FileStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def local_path(self, key: str) -> AbstractContextManager[Path]:
        """A local file for the stored object while the context is open (downloads it if remote)."""
        ...

    def delete_prefix(self, prefix: str) -> None: ...


# ───────── Voice ─────────


class STT(Protocol):
    name: str

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        """Mono 16-bit PCM in, transcript out."""
        ...


class TTS(Protocol):
    name: str

    def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        """Yields mono 16-bit PCM at the configured output rate as soon as audio is produced."""
        ...


class VAD(Protocol):
    frame_samples: int  # samples per frame the model expects (e.g. 512 at 16 kHz)

    def prob(self, frame: bytes) -> float:
        """Speech probability for one frame of mono 16-bit PCM. Stateful: one instance per call."""
        ...

    def reset(self) -> None: ...


# ───────── Bank (MVP 2) ─────────


class Bank(Protocol):
    """Read-only customer data. The Excel adapter stands in for the core-banking API."""

    def find_by_mobile(self, mobile: str) -> Customer | None: ...

    def customer(self, cif: str) -> Customer | None: ...
