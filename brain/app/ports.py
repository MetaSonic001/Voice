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
    """The bank's own systems: customer products (core banking, cards, deposits, loans) and card controls.

    Today: adapters/mock_corebank.py (a mock in its own database schema). Later: the bank's APIs. If different
    departments expose different APIs (cards, loans...), one adapter can call each of them behind this same
    interface; nothing above it changes.
    """

    def find_by_mobile(self, mobile: str) -> Customer | None: ...

    def customer(self, cif: str) -> Customer | None: ...

    def update_card(self, cif: str, kind: str, card_id: str, changes: dict, action: str, key: str | None = None) -> dict:
        """Apply `changes` (status / channel flags) to one of this customer's cards. Returns
        {"reference", "before", "after"} with `after` read back from the system, or raises BankError."""
        ...


class ServiceDesk(Protocol):
    """Where complaints, disputes, callbacks and new-customer leads are raised (the bank's CRM in production)."""

    def create_ticket(self, *, session_id: str | None, cif: str | None, kind: str, category: str, department: str,
                      summary: str, detail: dict, tat_days: int) -> dict: ...


class BankError(RuntimeError):
    """The bank system did not complete a request (unavailable, rejected). Never report success after this."""


class BankUnconfirmed(BankError):
    """A write was sent but its outcome could not be confirmed (timeout, lost reply). Never report success OR failure."""
