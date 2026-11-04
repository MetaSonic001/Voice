"""Plain data types shared by services and adapters. No I/O here."""

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Literal

BlockKind = Literal["heading", "para", "list", "table"]


@dataclass
class Block:
    """One structural unit from a parsed document."""

    kind: BlockKind
    text: str = ""
    level: int = 2  # headings: 1 = product/title level, 2 = section
    rows: list[list[str]] = field(default_factory=list)  # tables: first row is the header


@dataclass
class ParsedDoc:
    blocks: list[Block]
    report: dict


@dataclass
class ChunkDraft:
    kind: Literal["text", "table"]
    section_path: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def embed_text(self) -> str:
        return f"{self.section_path}\n{self.text}"


@dataclass
class Hit:
    """A candidate chunk during retrieval, with its scores from each stage."""

    chunk_id: str
    text: str
    section_path: str
    document_id: str
    document_title: str
    version_id: str
    version_no: int
    effective_from: str | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    rrf: float = 0.0
    rerank_score: float | None = None

    def brief(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document": self.document_title,
            "version": self.version_no,
            "section": self.section_path,
            "dense_rank": self.dense_rank,
            "keyword_rank": self.keyword_rank,
            "rrf": round(self.rrf, 5),
            "rerank_score": None if self.rerank_score is None else round(self.rerank_score, 4),
            "snippet": self.text[:160],
        }


INTENTS = ("general", "personal", "action", "issue", "human", "smalltalk", "out_of_scope", "manipulation")
LANGUAGES = ("en", "hi", "hinglish", "other")


@dataclass
class Understanding:
    language: str
    intent: str
    standalone_question: str
    search_query_en: str
    personal: dict = field(default_factory=dict)  # topic + filters, for questions about the caller's own products
    facts: dict = field(default_factory=dict)  # caller facts for the whole call (senior citizen, product, tenure...)


@dataclass
class Transcript:
    text: str
    language: str | None = None  # en | hi | other | None (unknown)
    no_speech: float = 0.0  # 0..1: how likely the audio held no real speech (noise, breath, music)
    latency_ms: int = 0


# ───────── Bank data (MVP 2: read-only, the caller's own products) ─────────


@dataclass
class Transaction:
    date: date_type
    description: str
    kind: str  # Credit | Debit
    amount: float


@dataclass
class Account:
    number: str
    type: str  # BSBDA | Digital | Vikaas | Value | Platinum | Royale
    balance: float
    transactions: list[Transaction] = field(default_factory=list)  # newest first

    @property
    def last4(self) -> str:
        return self.number[-4:]

    @property
    def is_salary(self) -> bool:
        return any(t.description.lower().startswith("salary") for t in self.transactions)


@dataclass
class Customer:
    cif: str
    name: str
    mobile: str
    accounts: list[Account]
    fd: dict | None = None  # balance, rate, maturity
    loan: dict | None = None  # outstanding, emi, paid, left, next_due
    debit_card: dict | None = None  # last4, variant, ecom, pos, atm
    credit_card: dict | None = None  # last4, type, outstanding, limit, due, bill_date
