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
    doc_category: str | None = None
    doc_tags: dict = field(default_factory=dict)  # tags a person set on the KB page (see services/kbmeta.py)
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


# What one request can be. "fraud" is never chosen by the LLM: code turns a request into a fraud request when a risk
# flag is present (the LLM's `risk` list or the code screens in services/guard.py).
REQUEST_KINDS = ("general", "personal", "action", "issue", "human", "new_customer", "smalltalk", "out_of_scope")
INTENTS = REQUEST_KINDS + ("fraud", "manipulation")
RISKS = ("unrecognised_debit", "lost_card", "otp_shared", "fraud_other")
PENDING_REPLIES = ("none", "accept", "decline", "question", "answer")
PRODUCTS = ("", "savings_account", "current_account", "fd", "rd", "credit_card", "debit_card", "loan", "other")
SCOPES = ("specific", "overview", "full", "compare", "choose")  # how much the caller wants (the router's view)
LANGUAGES = ("en", "hi", "hinglish", "other")


@dataclass
class Request:
    """One thing the caller asked for. A sentence can hold up to three; each is tracked on its own."""

    kind: str
    question: str  # self-contained, in the caller's language (redacted)
    personal: dict = field(default_factory=dict)  # topic + filters, for the caller's own products
    action: dict = field(default_factory=dict)  # block / unblock / controls / other, which card
    issue: str = "none"
    status: str = "open"  # open | done | declined | pending | failed
    goal: str = "info"  # info (wants a fact or a rule) | choose (wants help picking a product)
    product: str = ""  # the product type it is about (PRODUCTS)
    own_check: bool = False  # a general answer that could differ for the caller's own product: offer to check theirs
    scope: str = ""  # specific | overview | full | compare | choose ("" = not given: code patterns decide)
    named: list = field(default_factory=list)  # product names the caller said ("Zenith+", "Altura")
    label: str = ""  # a short spoken name for it ("your balance in both accounts"), for "Shall I go on to ...?"
    ref: int = 0  # the call agenda item this request belongs to (services/agenda.py); 0 = none
    risk: list = field(default_factory=list)  # a risk that belongs to this request alone (an agenda follow-up)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "question": self.question, "personal": self.personal, "action": self.action,
                "issue": self.issue, "status": self.status, "goal": self.goal, "product": self.product,
                "own_check": self.own_check, "scope": self.scope, "named": self.named, "label": self.label,
                "ref": self.ref, "risk": self.risk}

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        return cls(d["kind"], d["question"], d.get("personal") or {}, d.get("action") or {}, d.get("issue") or "none",
                   d.get("status") or "open", d.get("goal") or "info", d.get("product") or "", bool(d.get("own_check")),
                   d.get("scope") or "", list(d.get("named") or []), d.get("label") or "", int(d.get("ref") or 0),
                   list(d.get("risk") or []))


@dataclass
class Understanding:
    """What the understanding step made of a turn. The flat fields (intent, personal, ...) describe the primary
    request, so code that handles one request at a time reads them as before; `for_request` re-targets them."""

    language: str
    intent: str
    standalone_question: str
    search_query_en: str
    personal: dict = field(default_factory=dict)  # topic + filters, for questions about the caller's own products
    facts: dict = field(default_factory=dict)  # caller facts for the whole call (senior citizen, product, tenure...)
    action: dict = field(default_factory=dict)  # a requested change: block / unblock / controls / other, which card
    issue: str = "none"  # the kind of problem reported (intent "issue")
    department: str = "general"  # the team that would handle this if a person were needed
    requests: list[Request] = field(default_factory=list)  # 1..3, in the caller's order
    risk: list[str] = field(default_factory=list)  # unrecognised_debit | lost_card | otp_shared | fraud_other
    reply_to_pending: str = "none"  # accept | decline | question | none: how the turn answers the question we asked
    clarify: str = ""  # a question to ask back when something needed is missing
    resume_text: str = ""  # what to re-ask after verification: this request and the ones still waiting behind it
    recent: str = ""  # the last exchange of the call, for the answer step (follow-ups such as "and the fees?")
    goal: str = "info"  # of the primary request
    product: str = ""
    own_check: bool = False
    scope: str = ""
    named: list = field(default_factory=list)
    # Several questions in one turn (services/agenda.py). `asks` lists every separate thing the caller said, as their own
    # words ("quote"), "question" or "statement", with a short spoken "label"; requests beyond the three that were fully
    # understood stay in `extra_asks` and are understood when their turn comes.
    asks: list = field(default_factory=list)
    extra_asks: list = field(default_factory=list)
    agenda_reply: str = "none"  # the reply to "Shall I go on to ...?": none | continue | stop | skip | choose
    agenda_pick: int = 0  # with "choose": which of the listed questions (1-based)
    followup: str = ""  # a question code adds after the answers ("unrecognised_debit": "money got deducted" + a balance)
    opener: str = ""  # the LLM's short "let me check" line; said only when code allows it (conversation.opener_for)

    def for_request(self, r: Request) -> "Understanding":
        # The English search keywords were written for the first general request only; others search on their own question.
        first_general = next((x for x in self.requests if x.kind in ("general", "new_customer")), None)
        search = self.search_query_en if r is first_general else ""
        return Understanding(self.language, r.kind, r.question, search, r.personal, self.facts, r.action,
                             r.issue, self.department, [r], r.risk or self.risk, self.reply_to_pending, self.clarify,
                             self.resume_text, self.recent, r.goal, r.product, r.own_check, r.scope, r.named)


@dataclass
class Transcript:
    text: str
    language: str | None = None  # en | hi | other | None (unknown)
    no_speech: float = 0.0  # 0..1: how likely the audio held no real speech (noise, breath, music)
    latency_ms: int = 0
    confidence: float | None = None  # mean log-probability of the words (Whisper); lower = less sure. None = not reported
    compression: float = 0.0  # Whisper's compression ratio: very high means repetitive nonsense


# ───────── Bank data (read from the core-banking port; the agent never writes it except via card actions) ─────────


@dataclass
class Transaction:
    date: date_type
    description: str
    kind: str  # Credit | Debit
    amount: float
    channel: str = ""  # UPI | IMPS | NEFT | POS | ECOM | ATM | NACH | CASH | INTEREST | CHARGE
    counterparty: str = ""  # person or merchant
    status: str = "success"  # success | failed | pending | reversed
    reference: str = ""
    note: str = ""
    time: str = ""  # HH:MM


@dataclass
class Charge:
    date: date_type
    type: str  # amb_shortfall | debit_card_annual_fee | sms_alerts | emi_bounce
    amount: float
    reversed: bool = False


@dataclass
class DebitCard:
    id: str
    last4: str
    variant: str
    status: str  # active | temp_blocked | blocked
    atm: bool
    pos: bool
    ecom: bool
    contactless: bool
    international: bool
    account_last4: str = ""


@dataclass
class CardTxn:
    date: date_type
    merchant: str
    amount: float
    status: str = "success"
    international: bool = False


@dataclass
class CreditCard:
    id: str
    last4: str
    variant: str
    status: str
    limit: float
    outstanding: float
    total_due: float
    minimum_due: float
    statement_date: date_type | None = None
    due_date: date_type | None = None
    last_payment_on: date_type | None = None
    last_payment_amount: float | None = None
    last_payment_status: str | None = None  # posted | processing
    previous_bill_paid: bool | None = None
    annual_fee: float | None = None
    annual_fee_charged_on: date_type | None = None
    annual_fee_waiver_spend: float | None = None
    spend_last_year: float | None = None
    lounge_per_quarter: int = 0
    lounge_used_quarter: int = 0
    international: bool = False
    transactions: list[CardTxn] = field(default_factory=list)  # newest first

    @property
    def available(self) -> float:
        return max(self.limit - self.outstanding, 0)


@dataclass
class Account:
    number: str
    type: str  # the savings variant: BSBDA | Digital | Vikaas | Value | Platinum | Royale | ...
    balance: float  # ledger balance
    transactions: list[Transaction] = field(default_factory=list)  # newest first
    available: float | None = None
    hold_amount: float = 0
    hold_reason: str | None = None
    status: str = "active"
    is_salary: bool = False
    branch_type: str = ""  # urban | semi_urban | rural
    amb_required: float = 0
    amb_this_month: float | None = None
    amb_last_month: float | None = None
    charges: list[Charge] = field(default_factory=list)
    debit_card: DebitCard | None = None

    @property
    def last4(self) -> str:
        return self.number[-4:]


@dataclass
class FixedDeposit:
    number: str
    principal: float
    rate: float
    opened_on: date_type
    tenure_months: int
    maturity_date: date_type
    maturity_amount: float
    payout: str  # cumulative | quarterly
    auto_renew: bool
    status: str  # active | matured | closed

    @property
    def last4(self) -> str:
        return self.number[-4:]


@dataclass
class Emi:
    due_date: date_type
    amount: float
    status: str  # paid | bounced
    reason: str | None = None
    charge: float = 0
    paid_on: date_type | None = None  # the day it was actually debited (None: not paid)


@dataclass
class RateChange:
    date: date_type
    old_rate: float
    new_rate: float
    old_emi: float
    new_emi: float


@dataclass
class Loan:
    number: str
    type: str  # home | personal | vehicle | gold | business
    rate_type: str  # fixed | floating
    rate: float
    sanctioned: float
    outstanding: float
    emi: float
    next_due: date_type | None
    emis_paid: int
    emis_left: int
    status: str = "active"
    debit_account: str | None = None
    emis: list[Emi] = field(default_factory=list)  # newest first
    rate_changes: list[RateChange] = field(default_factory=list)  # newest first

    @property
    def last4(self) -> str:
        return self.number[-4:]


@dataclass
class Customer:
    cif: str
    name: str
    mobile: str
    accounts: list[Account]
    fds: list[FixedDeposit] = field(default_factory=list)
    loans: list[Loan] = field(default_factory=list)
    credit_cards: list[CreditCard] = field(default_factory=list)
    segment: str = "regular"  # regular | senior | priority
    rm_name: str | None = None
    language: str | None = None

    @property
    def debit_cards(self) -> list[DebitCard]:
        return [a.debit_card for a in self.accounts if a.debit_card]
