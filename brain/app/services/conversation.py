"""The conversation core: one caller turn in, a checked reply out. Channel-agnostic: REST and voice both use it.

Pipeline per turn:
  0. redact      remove account/card/phone numbers, OTPs, IDs before anything else sees the text
  1. understand  small LLM, strict JSON: language, request type, standalone question, English search keywords
  2. route       general -> knowledge answer; personal/action/issue -> needs verification (Phase B);
                 human -> handoff; smalltalk / out_of_scope / manipulation -> fixed replies
  3. retrieve    hybrid search + rerank; weak evidence -> refuse without calling the answer model
  4. answer      grounded, cited, short, in the caller's language - STREAMED sentence by sentence
  5. check       each sentence before release: numbers must exist in its cited sources; else stop and refuse
  6. trace       everything needed to explain the reply

`turn()` yields events as soon as they are ready (voice speaks each checked sentence immediately);
`ask()` collects the same events into one Reply (REST).
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field, replace

from app.domain import LANGUAGES, Hit, Request, Understanding
from app.logs import log
from app.logs import trace_id as trace_ctx
from app.adapters.http_bank import BANK_CALLS
from app.adapters.llm import LLMError
from app.ports import LLM, BankError
from app.services.audit import AuditStore, grounding, log_turn
from app.services.auth import AuthService
from app.services.benchmark import setup_of
from app.services.numbers import allowed_numbers, clean_markup, speech_text, spelled_figures, unsupported_numbers
from app.services.guard import injection, unsafe_output
from app.services.kbmeta import (mention_note, missing_products, named_products, product_of, provenance, select_sources,
                                 trim_context, variant_gate)
from app.services.confirm import read_reply
from app.services.says import new_request, topics_named
from app.services.content import SEED, Table, current, for_session, phrase, release_of, use
from app.services.dialogue import (SAFETY_LIMIT, TARGET_WORDS, can_offer_own, describe_facts, merge_facts, next_question, own_offer,
                                    recommend_request)
from app.services.dialogue import strategy as pick_strategy
from app.services.journeys import _REPEAT_ASKED, Journeys, Step, expired
from app.services.journeys import pending as make_pending
from app.services.personal import CODE_DETAILS, DETAILS, PERIODS, TOPICS, PersonalQuery, account_choice, loan_in_focus
from app.services.personal import _accounts as resolve_accounts
from app.services.personal import answer as personal_answer
from app.services.personal import fill
from app.services.privacy import is_code_entry, redact
from app.services.registry import task_for
from app.services.retrieval import Retriever, _ms, family_variants
from app.services.sentences import SentenceSplitter
from app.services import agenda
from app.services.smalltalk import REPLIES as SMALLTALK, is_courtesy, is_fragment, is_goodbye, is_greeting, is_repeat, reply_for
from app.services.state import CallState, StateStore
from app.services.store import TraceStore
from app.services.understanding import repair
from app.services.understanding import MAX_REQUESTS, UNDERSTAND_SCHEMA, UNDERSTAND_SCHEMA_LENIENT, UNDERSTAND_SYSTEM, asked_only, both_cards, broad_charges, build_input, card_failing, correct, coverage_gap, issue_screened, own_data, parse, pick_department, wants_own_charges, with_issue, with_risk

logger = logging.getLogger(__name__)
PROMPT_VERSION = "2026-11-22.1"  # opener (understanding writes it, code decides, the answer continues from it)

# The answer model's instructions: brain/content/prompts/answer_style.md (a release may change it) followed by
# answer_rules.md (locked). Catalog.answer_system() joins them.
LANGUAGE_INSTRUCTIONS = SEED.prompts["language"]  # locked: brain/content/catalog.json

TEMPLATES = Table("core")  # fixed sentences: brain/content/catalog.json, "core.*"
AUTH_TEMPLATE = {"otp_sent": "otp_sent", "need_mobile": "need_mobile", "not_registered": "not_registered",
                 "locked": "locked", "too_many_codes": "locked", "verified": "verified", "invalid": "otp_invalid",
                 "expired": "otp_expired", "no_code": "no_code", "already_verified": "already_verified"}
_MOBILE = re.compile(r"(?:\+?91[\s-]?|0)?([6-9](?:[\s-]?\d){9})(?!\d)")
ROUTE_FOR_INTENT = {"personal": "auth_required", "action": "auth_required", "issue": "auth_required",
                    "fraud": "auth_required", "human": "handoff"}
OUTCOME_FOR_INTENT = {"personal": "needs_verification", "action": "needs_verification", "issue": "needs_verification",
                      "fraud": "needs_verification", "human": "handoff", "smalltalk": "smalltalk",
                      "out_of_scope": "out_of_scope", "manipulation": "out_of_scope", "new_customer": "answered"}
_MARKER = re.compile(r"\[(\d+)\]")
# Some models cite as 【1】 or ［1］ and use narrow/no-break spaces and hyphens (U+202F, U+00A0, U+2011):
# normalise to plain ASCII so citations parse and TTS, number checks and logs see ordinary text.
_BRACKETS = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]", "〔": "[", "〕": "]",
                           "\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2007": " ", "\u2011": "-", "\u2010": "-"})




def _words(sentences: list[str]) -> int:
    return len(_MARKER.sub("", " ".join(sentences)).split())


def fraud_opening(risk: list[str]) -> str:
    """The template for the first line of a fraud report: it fits what happened."""
    for r in ("otp_shared", "lost_card", "unrecognised_debit"):
        if r in (risk or []):
            return f"fraud_open_{r}"
    return "fraud_first"


# Outcomes that end in a question to the caller or a hand-over: requests waiting behind them stay on the agenda.
HOLDING = {"clarify", "confirm", "auth_required", "handoff", "refused", "error", "action_failed"}
DONE = {"answered", "action_done", "ticket", "callback"}


def describe_pending(p: dict) -> str:
    """The question the agent last asked, in words, for the understanding step to read the caller's reply against."""
    kind = p.get("kind")
    if kind == "action":
        return redact(str(p.get("question", "")))[:200]
    if kind == "agenda_next":  # "Shall I go on to X? Still to answer: 1) X 2) Y": the router reads agenda_reply from it
        return redact(agenda.describe(p))[:400]
    return {"handoff": "Shall I connect you to our team?", "ticket": "Shall I raise a complaint or request for this?",
            "fraud_followup": "Shall I raise a dispute and connect you to the fraud team?",
            "lead": "Would you like someone from our team to call you to help open an account?",
            "action_target": "Which card is it?", "block_type": "Block the card temporarily or permanently?", "check_own": p.get("text", ""),
            "charges_which": p.get("text", "")}.get(kind, "a yes or no question")


def _corrected(u: Understanding, raw: str) -> Understanding:
    """The code corrections on the router's reading, in their order (the request list last)."""
    u = with_issue(with_risk(own_data(card_failing(correct(u, raw), raw), raw), raw), raw)
    return pick_department(both_cards(asked_only(u, raw), raw), raw)


# ───────── the LLM's reading, checked against the caller's own words ─────────
# Calls a6545287, 5884ab26, d42aa7ed (20-21 Nov): the understanding step copied the bank-data question answered in the
# turn before into the new turn ("no cash came out of the ATM" became the balance again; "does that include money people
# sent me?" became the UPI count again), so the caller heard the last answer again. Code now compares each bank-data
# request with what was answered last turn: the same data, with no word in the new sentence naming that topic, is a copy.
_ECHO_FIELDS = ("topic", "detail", "account", "item", "card", "period", "keyword", "direction", "count")
# "that", "those", "it", "isme", "उसमें": the new sentence refers back to the last answer
_REFERS_BACK = re.compile(r"\b(that|those|these|this|it|them|they|isme|usme|inme|unme|ismein|usmein|inmein|unmein|wo|woh|ye|yeh)\b|"
                          r"इसमें|उसमें|इनमें|उनमें|वो|ये", re.I)


def answer_key(p: dict | None) -> dict:
    """The data a bank-data request asked for (wording, totals and amounts aside), kept for the next turn's copy check."""
    p = p or {}
    return {k: str(p.get(k) or "").lower() for k in _ECHO_FIELDS}


def copied(r: Request, raw: str, previous: list[dict]) -> bool:
    """This request asks for exactly what was answered last turn and the caller's words don't name its topic again."""
    if r.kind != "personal" or not previous:
        return False
    topic = (r.personal or {}).get("topic") or ""
    return answer_key(r.personal) in previous and topic not in topics_named(raw)


_SPECIFICS = ("detail", "keyword", "direction", "count", "item", "card")


def rider(r: Request, raw: str, previous: list[dict]) -> bool:
    """A bank-data request that only repeats the topic and period answered last turn and adds nothing of its own (no
    detail, name, direction, count): call 5884ab26, "How much did Rajesh send me?" also brought back the whole list.
    Dropped only while another request remains (`_without_copies`)."""
    if r.kind != "personal" or not previous:
        return False
    key = answer_key(r.personal)
    if key["topic"] in topics_named(raw) or any(key[k] not in ("", "any", "0") for k in _SPECIFICS):
        return False
    return any(p["topic"] == key["topic"] and p["period"] == key["period"] and p["account"] == key["account"] for p in previous)


def without_duplicates(requests: list[Request]) -> list[Request]:
    """Bank-data requests asking for the same data are one request (after the code corrections, two differently worded
    requests can become the same one: calls 5884ab26 and d42aa7ed answered "No, that total is only money you paid out" twice)."""
    out, seen = [], []
    for r in requests:
        if r.kind == "personal" and (r.personal or {}).get("topic"):
            key = answer_key(r.personal)
            if key["detail"] in CODE_DETAILS:  # a detail code sets has one fixed answer ("No, that total is only...")
                key = {"topic": key["topic"], "detail": key["detail"]}
            if key in seen:
                continue
            seen.append(key)
        out.append(r)
    return out


def carry_period(u: Understanding, raw: str, previous: list[dict]) -> None:
    """A follow-up on transactions just listed ("and those, were they all UPI?") keeps the period of that list, so nothing
    from outside it is listed (call d42aa7ed: a 1 Nov salary under "the last 7 days")."""
    if not _REFERS_BACK.search(raw):
        return
    periods = {p["period"] for p in previous if p.get("topic") == "transactions" and p.get("period")}
    if len(periods) != 1:
        return
    for r in u.requests:
        if r.kind == "personal" and (r.personal or {}).get("topic") == "transactions" and not (r.personal or {}).get("period"):
            r.personal = {**r.personal, "period": next(iter(periods))}


def _merge(replies: list) -> "Reply":
    """One reply for the turn: every request's words in order, the last request's outcome (it says what is waiting)."""
    if len(replies) == 1:
        return replies[0]
    last = replies[-1]
    last.answer = " ".join(r.answer for r in replies if r.answer)
    last.speech_text = speech_text(last.answer, last.language)
    last.intent = replies[0].intent
    last.citations = [c for r in replies for c in r.citations]
    return last


def _note(state: CallState, task, req: Request, reply: "Reply") -> None:
    """Call state bookkeeping: what was done or declined, and what 'this account' / 'that card' now means."""
    if reply.outcome in DONE:
        state.mark(task.name)
    elif reply.outcome == "cancelled":
        state.mark(task.name, declined=True)
    if reply.outcome == "answered" and req.kind == "personal" and (req.personal or {}).get("topic"):
        state.answered.append(answer_key(req.personal))  # the next turn's copy check (`copied`)
    account = str((req.personal or {}).get("account") or "")
    if reply.outcome == "answered" and account and account not in ("all", "both"):
        state.focus["account"] = account
    detail = str((req.personal or {}).get("detail") or "")
    if reply.outcome == "answered" and (req.personal or {}).get("topic") in ("balance", "transactions"):
        state.focus["about"] = "hold" if detail in ("hold_check", "hold_why", "available_only") else "transactions"
    loan = str((req.personal or {}).get("item") or "")
    if reply.outcome == "answered" and (req.personal or {}).get("topic") == "loan" and loan not in ("", "all", "both", "every"):
        state.focus["loan"] = loan
    last4 = str((req.action or {}).get("card_last4") or "")
    if last4 and reply.outcome in ("confirm", "action_done"):
        state.focus["card"] = last4


_FAMILY_NOUN = {"Savings Account": ("savings accounts", "सेविंग्स अकाउंट", "savings accounts"),
                "Credit Card": ("credit cards", "क्रेडिट कार्ड", "credit cards"),
                "Current Account": ("current accounts", "करंट अकाउंट", "current accounts"),
                "Fixed Deposit": ("fixed deposits", "फिक्स्ड डिपॉज़िट", "fixed deposits"),
                "Debit Card": ("debit cards", "डेबिट कार्ड", "debit cards")}


def _or_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def template(kind: str, language: str, **values) -> str:
    return fill(TEMPLATES, kind, language, **values)


_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TICKET_STATUS = re.compile(r"\b(complaint|ticket|shikayat|request)\b.{0,30}\b(registered|raised|done|filed|logged|status|darj)\b|"
                            r"शिकायत.{0,20}(दर्ज|हुई|हो गई)", re.I)
# Words every Hindi or English speaker uses: on their own they say nothing about which language the caller wants.
# "हो" / "हे" are how speech recognition often writes an English "hey" (live call 7fa9b902).
_NEUTRAL = {"yes", "yeah", "yep", "ok", "okay", "sure", "no", "hello", "hi", "hey", "right", "fine", "please", "haan", "han",
            "ji", "accha", "achha", "acha", "hmm", "हाँ", "हां", "जी", "ओके", "अच्छा", "हेलो", "हैलो", "हो", "हे", "हाय",
            "namaste", "namaskar", "नमस्ते", "नमस्कार"}
# "Can you speak in Hindi?", "English mein bolo", "हिंदी में बात कीजिए": the caller's own choice, whatever script it is in.
_ASK_HINDI = re.compile(r"\b(speak|talk|reply|answer|continue|baat|bolo|boliye)\b.{0,25}\bhindi\b|\bhindi\b.{0,12}\b(mein|me|please|bolo|"
                        r"boliye|baat)\b|हिंदी में|हिन्दी में", re.I)
_ASK_ENGLISH = re.compile(r"\b(speak|talk|reply|answer|continue|baat|bolo|boliye)\b.{0,25}\benglish\b|\benglish\b.{0,12}\b(mein|me|"
                          r"please|bolo|boliye|baat)\b|इंग्लिश में|अंग्रेज़ी में|अंग्रेजी में", re.I)
# Common Hindi words written in Latin letters: two or more of them make a Latin-script sentence Hinglish, not English.
_HINGLISH_WORDS = {"hai", "hain", "kya", "kab", "kitna", "kitne", "kitni", "mera", "meri", "mere", "mujhe", "aap", "aapka",
                   "aapke", "nahi", "nahin", "kaise", "kyun", "kyon", "batao", "bataiye", "bata", "chahiye", "karna", "karo",
                   "kiya", "tha", "thi", "mein", "aur", "abhi", "wala", "wali", "baaki", "hua", "hui", "gaya", "gayi", "dijiye",
                   "paise", "paisa", "kat", "kata", "gaye", "bhi", "se", "ka", "ki", "ke", "ko", "toh", "yeh", "ye", "woh",
                   "kuch", "koi", "raha", "rahi", "rahe", "sakta", "sakti", "sakte", "chahta", "chahti", "chahte", "nikla",
                   "nikle", "dekho", "bataye", "batana", "karwa", "karwana", "kijiye", "hoga", "hogi",
                   "kyunki", "lekin", "matlab", "wapas", "kab", "kahan", "kaun", "kaunsa", "kitna", "hamara", "humara",
                   "apna", "apni", "apne", "mere", "tera", "aapki", "aapko", "unka", "uska", "iska", "isme", "usme"}


def reply_language(text: str, llm_language: str | None, fallback: str) -> str:
    """The language to reply in, decided in code from the caller's own words in this turn, not by the LLM's label.
    - An explicit request ("can you speak in Hindi?", "English mein bolo") decides first.
    - Filler words ("hey", "okay", "हाँ", "नमस्ते") are ignored; a turn with nothing else keeps `fallback` (the call's
      language), so a greeting or an acknowledgement never sets or changes it.
    - Mostly Devanagari words -> Hindi; half and half -> the LLM's label.
    - Mostly Latin words -> English or Hinglish, never Hindi: the LLM's choice between the two stands; when it said
      Hindi, two or more romanised Hindi words make it Hinglish, else English.
    Live call 7fa9b902: "हो. Okay, so I want to know when was my last home loan EMI?" (an English "hey" heard as "हो")
    was answered in Hindi because any Devanagari letter, or the LLM's label, decided it."""
    if _ASK_HINDI.search(text) and not _ASK_ENGLISH.search(text):  # an explicit choice comes first
        return "hi"
    if _ASK_ENGLISH.search(text) and not _ASK_HINDI.search(text):
        return "en"
    words = [w for w in re.findall(r"[\wऀ-ॿ]+", text.lower()) if not w.isdigit() and w not in _NEUTRAL]
    if not words:
        return fallback
    devanagari = sum(1 for w in words if _DEVANAGARI.search(w))
    if devanagari * 2 > len(words):
        return "hi"
    if devanagari * 2 == len(words):  # half and half ("मेरा balance"): the LLM's reading of the whole sentence
        return llm_language if llm_language in ("hi", "en", "hinglish") else "hi"
    # Latin letters: any romanised Hindi word makes it Hinglish, otherwise English. The LLM's label is not used here
    # (calls a6545287, d42aa7ed, 3eab4732: plain English sentences were labelled Hinglish and answered in Hinglish).
    return "hinglish" if any(w in _HINGLISH_WORDS for w in words) else "en"


def _carries_no_language(text: str) -> bool:
    """A short reply made only of such words ("Yes.", "Okay sure"): the call keeps its language."""
    w = re.findall(r"[\wऀ-ॿ]+", text.lower())
    return 0 < len(w) <= 3 and all(x in _NEUTRAL for x in w)
_DIGIT_NAMES = {w: str(d) for d, words in enumerate([
    "zero oh shunya शून्य", "one ek एक", "two do दो", "three teen तीन", "four char chaar चार",
    "five paanch panch पांच पाँच", "six chhe chhah छह छः", "seven saat सात", "eight aath आठ", "nine nau नौ"])
    for w in words.split()}
_DIGIT_NAME = re.compile(r"(?<![\wऀ-ॿ])(" + "|".join(sorted(_DIGIT_NAMES, key=len, reverse=True)) + r")(?![\wऀ-ॿ])", re.I)


def spoken_digits(text: str) -> str:
    """'saat ek shunya ...' / 'seven one zero ...' -> '7 1 0 ...' (for reading a mobile number said aloud)."""
    return _DIGIT_NAME.sub(lambda m: _DIGIT_NAMES[m.group(1).lower()], text)


# The opener (22 Nov 2026; replaces the timed "one moment" filler): the understanding call also writes a short line
# ("Sure, let me check the FD rates for you."). It is said the moment understanding ends, while search and the answer
# model run, and the answer model is told it was said, so the caller hears one continuous reply with no repeated
# "let me check". Code decides whether it is said and checks it: it is spoken without the figure check.
_OPENER_BAD = re.compile(r"[\d₹%?\[\]#*@/]|https?:")  # \d is any script's digits
_OPENER_OUTCOME = re.compile(r"\b(block|blocked|unblock|approved?|refund|reversed?|guarantee|done|ब्लॉक|रिफंड)\b", re.I)


def opener_for(u: Understanding, lang: str, guided: bool) -> str:
    """The LLM's opener if this turn may have one, else "". Only for a product question answered from the KB (the slow
    path: search, then the answer model). Bank-data, action, fraud and hand-over replies are templates, ready at once,
    so they need no opener."""
    text = re.sub(r"\s+", " ", u.opener or "").strip()
    primary = u.requests[0] if u.requests else None
    if (not text or guided or u.risk or lang != u.language or primary is None
            or primary.kind not in ("general", "new_customer") or primary.goal == "choose" or primary.scope == "choose"
            or sum(1 for a in u.asks if a.get("type") == "question") >= 3):  # 3+ questions: the agenda intro is said instead
        return ""
    if not 2 <= len(text.split()) <= 12 or len(text) > 90 or _OPENER_BAD.search(text) or _OPENER_OUTCOME.search(text) \
            or redact(text) != text:
        return ""
    return text if text[-1] in ".!।" else f"{text}."


def _trim(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def auth_payload(result) -> dict:
    """What a client needs to show for an auth step. The simulated SMS exists only with OTP_DELIVERY=simulated."""
    out = {"status": result.status, "masked_mobile": result.masked_mobile or None}
    if result.status == "invalid":
        out["attempts_left"] = result.attempts_left
    if result.simulated_sms:
        out["simulated_sms"] = result.simulated_sms
    if result.status == "verified" and result.customer:
        out["name"] = result.customer.name
    return out


# What the caller is waiting to hear, for "Before I share your ..." (by personal topic).
_ACK_WHAT = {"balance": ("balance", "बैलेंस", "balance"), "transactions": ("transactions", "ट्रांज़ैक्शन की जानकारी", "transactions ki jaankari"),
             "loan": ("loan details", "लोन की जानकारी", "loan ki jaankari"), "fd": ("FD details", "FD की जानकारी", "FD ki jaankari"),
             "credit_card": ("card details", "कार्ड की जानकारी", "card ki jaankari"),
             "debit_card": ("card details", "कार्ड की जानकारी", "card ki jaankari")}


def auth_ack(topic: str, language: str) -> str:
    what = _ACK_WHAT.get(topic, ("account details", "अकाउंट की जानकारी", "account ki jaankari"))
    return template("auth_ack", language, what=what[{"en": 0, "hi": 1}.get(language, 2)])


def auth_message(result, language: str, why_said: bool = False) -> str:
    """What the caller hears for an auth step (otp_sent, invalid, locked...). `why_said`: the reason for verifying
    was already given (auth_ack), so only the code part follows."""
    kind = AUTH_TEMPLATE.get(result.status, "locked")
    if kind == "otp_sent":
        return template("otp_code" if why_said else kind, language, last4=result.masked_mobile[-4:])
    if kind == "otp_invalid":
        return template(kind, language, left=result.attempts_left, tries="try" if result.attempts_left == 1 else "tries")
    return template(kind, language)


@dataclass
class Reply:
    trace_id: str
    session_id: str | None
    outcome: str  # answered | clarify | refused | needs_verification | handoff | smalltalk | out_of_scope | error
    intent: str
    language: str
    answer: str
    speech_text: str
    route: str | None = None  # what a later phase should do: auth_required | handoff
    reason: str | None = None  # why it was refused, if it was
    citations: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    auth: dict | None = None  # verification step for the client: status, masked mobile, simulated SMS (never traced)
    handoff: dict | None = None  # the call now waits for a person: {id, department, name}


@dataclass
class TurnEvent:
    """`understood` (language/intent known), `sentence` (one checked sentence, ready to speak), `auth` (show the
    keypad / simulated SMS), `done` (final Reply)."""

    kind: str
    language: str = "en"
    intent: str = ""
    text: str = ""
    speech: str = ""
    reply: Reply | None = None
    auth: dict | None = None  # kind "auth": a verification step the client must show (keypad, simulated SMS)
    handoff: dict | None = None  # kind "handoff": stop the AI, play hold music, wait for a person


class Conversation:
    def __init__(self, llm: LLM, retriever: Retriever, traces: TraceStore, settings, auth: AuthService | None = None,
                 audit: AuditStore | None = None, journeys: Journeys | None = None):
        self.llm, self.retriever, self.traces, self.s, self.auth, self.audit = llm, retriever, traces, settings, auth, audit
        self.journeys = journeys
        self.states = StateStore(traces.db)
        self.setup_id, self.setup = setup_of(settings, PROMPT_VERSION)  # what /bench groups this brain's turns by
        self.summariser = None  # set by the container; writes the AI handover summary in the background
        self._background: set[asyncio.Task] = set()

    async def understand(self, question: str, history: list[dict], facts: dict | None = None, pending: str = "",
                         focus: str = "", found: list[str] | None = None) -> tuple[Understanding, dict]:
        # Long answers are trimmed: the window is for resolving follow-ups, the facts carry what must persist.
        convo = "".join(f"Caller: {t['question']}\nAssistant: {_trim(t['answer'])}\n" for t in history)
        user = build_input(question, convo, describe_facts(facts or {}), pending, focus, found)
        try:
            data, meta = await self.llm.json(self.s.understand_model, UNDERSTAND_SYSTEM, user, UNDERSTAND_SCHEMA,
                                             effort=self.s.understand_reasoning_effort or None)
        except LLMError as e:
            if "expected schema" not in str(e):  # only a rejected value is repaired; an outage is not
                raise
            data, note = repair(getattr(e, "body", ""))
            if data is not None:  # fixed in code from the rejected generation: no second call
                meta = {"model": self.s.understand_model, "schema_repair": "local", "invalid": note}
            else:
                data, meta = await self.llm.json(self.s.understand_model, UNDERSTAND_SYSTEM, user, UNDERSTAND_SCHEMA_LENIENT,
                                                 effort=self.s.understand_reasoning_effort or None)
                meta.update(lenient_retry=True, schema_repair="retry", invalid=note)
            logger.warning("router output repaired", extra={"data": {"how": meta["schema_repair"], "invalid": note}})
        u = parse(data, question, facts or {}, merge_facts)
        u.language = reply_language(question, u.language, u.language)  # the caller's words decide, not the label
        return u, meta

    async def ask(self, question: str, session_id: str | None = None, extra_timings: dict | None = None) -> Reply:
        reply = None
        async for event in self.turn(question, session_id, extra_timings):
            if event.kind == "done":
                reply = event.reply
        return reply

    async def turn(self, question: str, session_id: str | None = None,
                   extra_timings: dict | None = None) -> AsyncIterator[TurnEvent]:
        """Run one turn, yielding events as they become available. Always ends with a `done` event.
        Also times the first sentence of the reply on every path (`first_reply_ms`, the brain's time to first output),
        which the /bench page compares across setups."""
        use(for_session(session_id))  # the catalogue release this call is on (a canary or the main one), for the whole turn
        started = time.perf_counter()
        timings: dict = dict(extra_timings or {})
        inner = self._turn(question, session_id, timings, started)
        try:
            async for event in inner:
                if event.kind == "sentence" and "first_reply_ms" not in timings:
                    timings["first_reply_ms"] = _ms(started)  # the same dict the trace is saved from
                yield event
        finally:
            await inner.aclose()  # a barge-in closes this generator: the inner one records the interrupted turn

    async def _turn(self, question: str, session_id: str | None, timings: dict, started: float) -> AsyncIterator[TurnEvent]:
        tid = str(uuid.uuid4())
        trace_ctx.set(tid)
        data: dict = {"setup_id": self.setup_id, "setup": self.setup, "prompt_version": PROMPT_VERSION,
                      "content": release_of(current()), "models": {
            "understand": self.s.understand_model, "answer": self.s.answer_model,
            "embedder": self.retriever.embedder.name, "reranker": self.retriever.reranker.name}}
        raw = question.strip()
        bank_calls: list = []  # every bank system call made for this turn (evidence on /calls)
        calls_token = BANK_CALLS.set(bank_calls)
        if session_id and self.auth and is_code_entry(raw) and await asyncio.to_thread(self.auth.awaiting_code, session_id):
            async for event in self._code_turn(tid, raw, session_id, timings, started):
                yield event
            return
        question = redact(raw)
        reply: Reply | None = None
        lang, intent = "en", "unknown"
        history: list = []
        channel = "call" if timings.get("input") in ("speech", "text", "after_verification") else "chat"
        state = CallState()
        try:
            history, facts, waiting, state = (await asyncio.to_thread(lambda: (
                self.traces.history(session_id, self.s.history_turns), self.traces.facts(session_id),
                self.traces.pending(session_id), self.states.load(session_id)))) if session_id else ([], {}, None, state)
            state_before = asdict(state)
            previous, state.answered = list(state.answered), []  # bank-data requests answered last turn (copy check)
            # 1. The agent asked something ("Shall I block card ending 6712?"): a yes / no is read in code,
            #    with no LLM call. Anything else drops the question and the turn is understood normally.
            step, dropped, own_yes, agenda_go = None, None, None, None
            # After the keypad code: the agenda items that waited for it are answered from what was understood before
            # the code, with no second reading of the text (a second reading lost a request: smoke A3, 17 Nov).
            resume = agenda.resume_after_verification(state) if str(timings.get("input", "")).startswith("after_verification") else []
            if waiting and self.journeys:
                lang = waiting.get("lang", "en")
                if waiting.get("kind") == "agenda_next":  # "Shall I go on to X?": a plain yes / no is read in code
                    said = read_reply(raw)
                    if not expired(waiting) and (said in ("yes", "no") or is_goodbye(raw)):
                        agenda_go = ("continue" if said == "yes" and not is_goodbye(raw) else "stop", waiting)
                elif waiting.get("kind") == "check_own" and not expired(waiting) and read_reply(raw) == "yes":
                    own_yes = waiting  # handled below, like any other request for the caller's own data
                elif waiting.get("kind") == "charges_which":  # "taken from my account" -> their charges; else the router reads it
                    own_yes = waiting if not expired(waiting) and wants_own_charges(raw) else None
                elif not expired(waiting):
                    step = await asyncio.to_thread(self.journeys.resolve, session_id, waiting, raw, lang, channel)
                if step is None:
                    dropped = None if (own_yes or agenda_go) else waiting
                    await asyncio.to_thread(self.traces.set_pending, session_id, None)
            waited: list[int] = []  # bank-data questions waiting on "which account?" (agenda.after_account_choice)
            if step is None:  # what was waiting on the caller has been answered or left: its own flow carries it
                waited = agenda.settle(state)
            # Asked for the registered mobile number: read it from the caller's own words (before redaction),
            # in code - it never goes to the LLM.
            mobile = _MOBILE.search(spoken_digits(raw.translate(_DEVANAGARI_DIGITS)))
            if resume:
                lang = state.agenda_meta.get("language") or (history[-1]["language"] if history else None) or "en"
                async for event in self._run_agenda(agenda.plan(resume), lang, tid, session_id, question, history, facts,
                                                    state, timings, data):
                    if event.kind == "understood":
                        intent = event.intent
                    if event.kind == "done":
                        reply = event.reply
                    else:
                        yield event
                # the record shows everything that sentence asked (answered now or still on the agenda)
                data["understanding"]["requests"] = [asdict(agenda.to_request(i)) for i in resume if i.get("req")]
            elif agenda_go is not None:
                async for event in self._agenda_reply(agenda_go[0], agenda_go[1], raw, lang, tid, session_id, question,
                                                      history, facts, state, timings, data):
                    if event.kind == "understood":
                        intent = event.intent
                    if event.kind == "done":
                        reply = event.reply
                    else:
                        yield event
            elif own_yes is not None:
                intent = "personal"
                req = Request("personal", own_yes["question"], own_yes.get("personal") or {}, product=own_yes.get("product", ""))
                shell = Understanding(lang, "personal", req.question, "", req.personal, facts=facts)
                async for event in self._process([req], shell, lang, tid, session_id, question, state, timings, data):
                    if event.kind == "done":
                        reply = event.reply
                    else:
                        yield event
            elif step is not None:
                intent = "confirmation"
                customer = await asyncio.to_thread(self.auth.verified_customer, session_id) if self.auth else None
                replies = []
                async for event in self._emit_step(tid, session_id, step, lang, intent, data, customer):
                    if event.kind == "done":
                        replies.append(event.reply)
                    else:
                        yield event
                if step.outcome == "cancelled":
                    state.mark(str((waiting or {}).get("kind")), declined=True)
                elif step.outcome not in ("confirm", "clarify"):
                    state.mark(str((waiting or {}).get("kind")))
                if step.pending is None:  # the question is settled; what waited behind it is offered below
                    waited += agenda.settle(state, declined=step.outcome == "cancelled")
                reply = _merge(replies)
            elif mobile and self.auth and await asyncio.to_thread(self.auth.awaiting_mobile, session_id):
                lang, intent = (history[-1]["language"] if history else "en") or "en", "personal"
                result = await asyncio.to_thread(self.auth.submit_mobile, session_id, re.sub(r"\D", "", mobile.group(1)))
                async for event in self._auth_step(tid, session_id, result, lang, intent):
                    if event.kind == "done":
                        reply = event.reply
                    else:
                        yield event
            else:
                async for event in self._understood(tid, session_id, raw, question, history, facts, dropped, state,
                                                    timings, data, previous):
                    if event.kind == "understood":
                        lang, intent = event.language, event.intent
                    if event.kind == "done":
                        reply = event.reply
                    else:
                        yield event
                again = (dropped or {}).get("question") if (dropped or {}).get("kind") == "action" else ""
                if (again and reply is not None and reply.outcome in ("answered", "refused") and read_reply(raw) == "question"
                        and not await asyncio.to_thread(self.traces.pending, session_id)):
                    yield TurnEvent("sentence", lang, intent, again, speech_text(again, lang))
                    reply.answer, reply.speech_text = f"{reply.answer} {again}".strip(), f"{reply.speech_text} {speech_text(again, lang)}".strip()
                    await asyncio.to_thread(self.traces.set_pending, session_id, {**dropped, "at": make_pending("x", lang)["at"]})
            if waited and reply is not None:
                agenda.after_account_choice(state, waited, bool(data.get("resumed_question")), reply.outcome)
            if session_id and reply is not None and state.agenda:
                async for event in self._offer_next(session_id, state, reply, data):
                    yield event
                data["agenda"] = [{"id": i["id"], "label": agenda.label(i, "en"), "status": i["status"]} for i in state.agenda]
            if session_id and asdict(state) != state_before:
                await asyncio.to_thread(self.states.save, session_id, state)
        except (asyncio.CancelledError, GeneratorExit):
            # caller interrupted (voice barge-in): keep an audit record of what was asked and how far we got
            timings["total_ms"] = _ms(started)
            data.update(timings=timings, outcome="interrupted", grounding={"level": "n/a", "basis": ["caller interrupted"]})
            trace = {"id": tid, "session_id": session_id, "question": question, "language": lang,
                     "intent": intent, "outcome": "interrupted", "answer": "", "total_ms": timings["total_ms"], "data": data}
            log_turn(session_id, tid, question, "", intent, "interrupted", None, self.traces.save(trace))
            raise
        except Exception as e:
            logger.exception("turn failed")
            data["error"] = f"{type(e).__name__}: {e}"
            if intent == "unknown":  # understanding never finished: the call's language, or the script the caller used
                lang = (history[-1].get("language") if history else None) or reply_language(raw, None, lang)
            text = template("unavailable", lang)  # a system problem, said plainly - not "no information"
            yield TurnEvent("sentence", lang, intent, text, speech_text(text, lang))
            reply = Reply(tid, session_id, "error", intent, lang, text, speech_text(text, lang), route="handoff",
                          reason="internal_error")
            # The reply offers "our team": a real question, so "yes" transfers in code with no LLM call. Before, the
            # yes went to the LLM too and failed the same way (live call f4d36d3a, Groq rate limit).
            if session_id and self.journeys:
                try:
                    offer = self.journeys.offer_transfer("general", lang, "the agent could not answer (system error)")
                    await asyncio.to_thread(self.traces.set_pending, session_id, offer.pending)
                except Exception:  # noqa: BLE001 - the error reply is said either way
                    logger.warning("could not record the transfer offer", exc_info=True)
        timings["total_ms"] = _ms(started)
        reply.timings = timings
        data.update(timings=timings, outcome=reply.outcome, reason=reply.reason, route=reply.route,
                    citations=reply.citations, bank_calls=list(bank_calls))
        try:
            BANK_CALLS.reset(calls_token)
        except ValueError:  # reset from another context (a generator closed elsewhere): the next turn sets its own list
            pass
        if len(reply.answer.split()) > 80 and "?" not in reply.answer:  # style rule 1: flagged for review, never cut
            data["long_reply"] = len(reply.answer.split())
        data["grounding"] = grounding(reply.outcome, reply.intent, data, reply.citations, timings)
        trace = {"id": tid, "session_id": session_id, "question": question, "language": reply.language,
                 "intent": reply.intent, "outcome": reply.outcome, "answer": reply.answer,
                 "total_ms": timings["total_ms"], "data": data}
        await asyncio.to_thread(self._record, trace)
        log(logger, "turn", outcome=reply.outcome, intent=reply.intent, language=reply.language, reason=reply.reason,
            grounding=data["grounding"]["level"], total_ms=timings["total_ms"])
        yield TurnEvent("done", reply.language, reply.intent, reply=reply)

    # ───────── understanding a turn and running its requests ─────────

    async def _understood(self, tid, session_id, raw, question, history, facts, dropped, state: CallState, timings,
                          data, previous: list[dict] | None = None) -> AsyncIterator[TurnEvent]:
        chosen = "en"  # the language picked when the call started, for a first turn that is only "hey" / "हाँ"
        if not history and session_id:
            chosen = ((await asyncio.to_thread(self.traces.session, session_id)) or {}).get("language") or "en"
        lang0 = (history[-1]["language"] if history else None) or reply_language(raw, None, chosen)
        if is_courtesy(raw):  # "thanks", "धन्यवाद", "bye": a short reply in code, no LLM call
            data["courtesy"] = True
            u = Understanding(lang0 if lang0 in LANGUAGE_INSTRUCTIONS else "en", "smalltalk", question, "")
            yield TurnEvent("understood", u.language, "smalltalk")
            if is_goodbye(raw):
                data["goodbye"] = True  # no "Shall I go on?" after a goodbye
                item = None if state.agenda_meta.get("bye_reminded") else agenda.next_offer(state)
                if item is not None and session_id and self.journeys:  # "Before you go, you'd also asked about X." (once)
                    state.agenda_meta["bye_reminded"] = True
                    text = agenda.offer_text(state, item, u.language, bye=True)
                    await asyncio.to_thread(self.traces.set_pending, session_id, make_pending(
                        "agenda_next", u.language, **agenda.offer_pending(state, item, text)))
                    async for event in self._say(tid, session_id, "smalltalk", u.language, text, "answered"):
                        yield event
                    return
            async for event in self._smalltalk(tid, session_id, raw, u, u.language, state):
                yield event
            return
        if is_fragment(raw):  # "so, you know," is not a turn: say nothing and keep listening
            data["fragment"] = True
            yield TurnEvent("understood", lang0, "smalltalk")
            yield TurnEvent("done", lang0, "smalltalk", reply=Reply(tid, session_id, "smalltalk", "smalltalk", lang0, "", ""))
            return
        if session_id and is_repeat(raw) and (last := await asyncio.to_thread(self.traces.last_answer, session_id)):
            data["repeat"] = True  # "can you repeat that": the last answer again, no LLM call
            yield TurnEvent("understood", lang0, "smalltalk")
            yield TurnEvent("sentence", lang0, "smalltalk", last, speech_text(last, lang0))
            yield TurnEvent("done", lang0, "smalltalk", reply=Reply(tid, session_id, "answered", "smalltalk", lang0, last,
                                                                    speech_text(last, lang0)))
            return
        if (not dropped and history and history[-1].get("outcome") == "clarify" and read_reply(raw) == "yes"
                and len(raw.split()) <= 2):  # regression pack: "Yes." after "which account?" became a card-statement answer
            data["bare_yes"] = True
            yield TurnEvent("understood", lang0, "smalltalk")
            async for event in self._say(tid, session_id, "smalltalk", lang0, template("bare_yes", lang0), "clarify"):
                yield event
            return
        if session_id and self.journeys and _REPEAT_ASKED.search(raw) and len(raw.split()) <= 12:
            handoff = await asyncio.to_thread(self.journeys.handoffs.for_session, session_id)
            if handoff and handoff.get("status") in ("waiting", "active"):  # regression pack: it started a second transfer
                yield TurnEvent("understood", lang0, "smalltalk")
                async for event in self._say(tid, session_id, "smalltalk", lang0, template("context_passed", lang0), "handoff"):
                    yield event
                return
        if session_id and self.audit and _TICKET_STATUS.search(raw):  # "is the complaint registered?" (regression pack)
            ticket = await asyncio.to_thread(self._last_ticket, session_id)
            if ticket:  # raised in this call: said from the record, never guessed
                data["ticket_status"] = ticket
                yield TurnEvent("understood", lang0, "smalltalk")
                async for event in self._say(tid, session_id, "smalltalk", lang0, template("ticket_status", lang0, ref=ticket), "answered"):
                    yield event
                return
        if injection(raw):  # caught in code: no LLM call, and it cannot be talked out of it
            data["safety_trigger"] = {"injection": True}
            lang = reply_language(raw, None, "en")
            u = Understanding(lang, "manipulation", question, "")
            yield TurnEvent("understood", lang, "manipulation")
            async for event in self._fixed(tid, session_id, u, lang):
                yield event
            return
        t = time.perf_counter()
        warming = asyncio.create_task(asyncio.to_thread(self.retriever.warm))  # in parallel with the LLM
        # Prefetch: a verified caller's accounts are read while the LLM understands the turn (the read is cached for this
        # turn, so a balance or transactions answer does not wait for the bank). A bank problem surfaces later, where it is handled.
        prefetch = asyncio.create_task(asyncio.to_thread(self.auth.verified_customer, session_id)) if (self.auth and session_id) else None
        asked = describe_pending(dropped) if dropped else state.flow.get("question", "")
        u, meta = await self.understand(question, history, facts, asked, state.focus_text())
        if history:
            u.recent = f"Caller: {history[-1]['question']}\nYou: {_trim(history[-1]['answer'], 220)}"
        await warming
        if prefetch:
            await asyncio.gather(prefetch, return_exceptions=True)
        timings["understand_ms"] = _ms(t)
        u = _corrected(u, raw)
        u = await self._without_copies(u, raw, question, facts, asked, state, previous or [], timings, data)
        gap = coverage_gap(question, u.asks)
        if gap and not dropped:  # words no listed question covers: one more look for a question the first list missed
            await self._second_look(u, question, history, facts, state, timings, data, len(gap.split()))
        if session_id and u.facts != facts:
            await asyncio.to_thread(self.traces.save_facts, session_id, u.facts)
        data["understanding"], data["understand_llm"] = asdict(u), meta
        if u.risk:
            data["safety_trigger"] = {"risk": u.risk}
        lang = u.language if u.language in LANGUAGE_INSTRUCTIONS else "en"
        if history and lang != lang0 and _carries_no_language(raw):  # a Hindi caller's "Yes." stays Hindi (call f4d36d3a)
            u.language = lang = lang0
        yield TurnEvent("understood", lang, u.intent)
        lead = opener_for(u, lang, guided=bool(state.flow))
        if lead:  # said by `_answer` if this turn's first request reaches the KB answer (see opener_for)
            data["opener"], data["opener_for"] = lead, u.standalone_question
        if broad_charges(u, raw):  # "charges on my account": taken from it, or the bank's general charges? Asked, not guessed
            text = template("charges_which", lang)
            data["clarify"] = "charges_which"
            if session_id and self.journeys:
                await asyncio.to_thread(self.traces.set_pending, session_id, make_pending(
                    "charges_which", lang, text=text, question=u.standalone_question, personal={"topic": "charges", "detail": "charges"}))
            async for event in self._say(tid, session_id, u.intent, lang, text, "clarify"):
                yield event
            return
        if dropped and u.reply_to_pending == "decline":  # a "no" that came with a new request still counts as a no
            state.mark(str(dropped.get("kind")), declined=True)
        if state.flow and not u.risk:  # a guided "help me choose" flow is waiting for this answer
            if u.reply_to_pending in ("answer", "accept", "decline") and all(
                    r.kind in ("general", "new_customer", "smalltalk") for r in u.requests):
                async for event in self._flow(tid, session_id, raw, u, lang, state, timings, data,
                                              answered=u.reply_to_pending != "decline"):
                    yield event
                return
            state.flow = {}  # the caller moved on: the flow is dropped, the new request is answered
        # A short reply naming an account ("second one", "dusra wala") answers a pending "which account?",
        # whatever kind of request the LLM gave it.
        # Decided by what the reply says, not its length (call a6545287: a 7-word Hindi answer missed a 6-word limit and
        # the caller's "last 5 transactions" was lost): it names an account and nothing else (no other topic, problem,
        # request for a person or product question).
        waiting_query = (await asyncio.to_thread(self.auth.pending_query, session_id)
                         if self.auth and session_id and account_choice(question) else None)
        choosing = bool(waiting_query) and not new_request(raw, keep={waiting_query.get("topic", "")}, general=False)
        if choosing:
            async for event in self._process([Request("personal", u.standalone_question, u.personal)], u, lang, tid,
                                             session_id, question, state, timings, data):
                yield event
            return
        new_requests = [r for r in u.requests if r.kind != "smalltalk"]
        if dropped and dropped.get("kind") == "agenda_next":  # the reply to "Shall I go on to X?", read by the router
            how = u.agenda_reply if u.agenda_reply != "none" else ("stop" if u.reply_to_pending == "decline" else "none")
            # "go on" / "skip that one" / "the second one" answer our question: their words are not a new question.
            # Only "no, but what's my FD rate?" carries one, so only a stop goes on to the new requests.
            if how in ("continue", "skip", "choose") or (how == "stop" and not new_requests):
                async for event in self._agenda_reply(how, dropped, raw, lang, tid, session_id, question, history, facts,
                                                      state, timings, data, pick=u.agenda_pick, announced=True):
                    yield event
                return
            if how == "stop":
                agenda.stop(state)
            elif how == "skip":
                agenda.skip(state, dropped.get("item"))
        if not session_id or not new_requests:  # small talk only, or no call to keep an agenda for
            async for event in self._process(u.requests, u, lang, tid, session_id, question, state, timings, data):
                yield event
            return
        # The call agenda: every separate thing asked goes on it; code decides what is answered now (services/agenda.py).
        for r in new_requests:
            if r.kind == "fraud" and not r.risk:
                r.risk = list(u.risk)
        items = agenda.add(state, new_requests, u.extra_asks, u.followup)
        for item in items:
            item.setdefault("department", u.department)
        state.agenda_meta["language"] = lang
        await asyncio.to_thread(self.states.save, session_id, state)  # saved before answering: a barge-in can't lose it
        opening = agenda.intro(items, lang)
        if opening:
            data.pop("opener", None)  # the intro is the first line instead
            yield TurnEvent("sentence", lang, u.intent, opening, speech_text(opening, lang))
        async for event in self._run_agenda(agenda.plan(items), lang, tid, session_id, question, history, facts, state,
                                            timings, data, u=u):
            if event.kind == "done" and opening:
                event.reply.answer = f"{opening} {event.reply.answer}".strip()
                event.reply.speech_text = speech_text(event.reply.answer, event.reply.language)
            yield event

    async def _process(self, queue: list[Request], u: Understanding, lang, tid, session_id, question, state: CallState,
                       timings, data) -> AsyncIterator[TurnEvent]:
        """Run the requests one after another. A request that ends in a question to the caller (or a hand-over) holds
        the rest. Requests that belong to the call agenda (`ref`) are recorded there, so what is left stays on the
        agenda and is offered afterwards ("Shall I go on to ...?"); nothing runs again on its own."""
        batch = queue[:MAX_REQUESTS]
        journeys = bool(self.journeys and self.auth and session_id)
        replies, summary = [], []
        for i, req in enumerate(batch):
            view = u.for_request(req)
            # The text stored with a verification request (for the record and plain chat): when everything the caller
            # said is still to do, their own words; else the requests still to do. After the code, agenda items resume
            # from what was understood the first time (`agenda.resume_after_verification`), not from this text.
            whole = i == 0 and [r.question for r in batch] == [r.question for r in u.requests]
            view.resume_text = question if whole else " ".join(r.question for r in batch[i:])
            task = task_for(req)
            reply = None
            async for event in self._dispatch(task, tid, session_id, question, view, lang, timings, data, journeys, state):
                if event.kind == "done":
                    reply = event.reply
                else:
                    yield event
            if reply is None:
                continue
            if journeys and reply.outcome in ("refused", "error"):  # "I can connect you to a specialist. Shall I?" is a real question
                await asyncio.to_thread(self.traces.set_pending, session_id, self.journeys.offer_transfer(
                    view.department, lang, f"the agent could not answer ({reply.reason or reply.outcome})").pending)
            replies.append(reply)
            summary.append({"task": task.name, "access": task.access, "outcome": reply.outcome, "agenda_item": req.ref or None})
            _note(state, task, req, reply)
            if req.ref:
                agenda.record(state, req.ref, reply.outcome)
            if reply.outcome == "auth_required":  # the rest of this turn's items wait for the code with it
                break
            more = batch[i + 1:]
            if more and (reply.outcome in HOLDING or (session_id and await asyncio.to_thread(self.traces.pending, session_id))):
                break
        data["requests"] = summary
        if not replies:
            raise RuntimeError("no request produced a reply")
        final = _merge(replies)
        yield TurnEvent("done", final.language, final.intent, reply=final)

    # ───────── the call agenda (services/agenda.py) ─────────

    async def _run_agenda(self, items: list[dict], lang, tid, session_id, question, history, facts, state: CallState,
                          timings, data, u: Understanding | None = None) -> AsyncIterator[TurnEvent]:
        """Answer these agenda items now. Items not understood yet (the 4th+ question of a turn) are understood first,
        from the caller's own words for them. `u`: this turn's understanding, when the items come from this turn."""
        reqs = []
        for item in items:
            if item.get("req") is None and not await self._understand_item(item, history, facts, state, timings, data):
                item["status"] = "failed"
                continue
            reqs.append(agenda.to_request(item))
        if not reqs:
            raise RuntimeError("no agenda item could be understood")
        if u is None:  # a later turn ("yes, go on" / after the code): a view of the stored items, nothing re-read
            first = items[0]
            u = Understanding(lang, reqs[0].kind, reqs[0].question, "", reqs[0].personal, facts=facts, action=reqs[0].action,
                              issue=reqs[0].issue, department=first.get("department") or "general", requests=reqs,
                              risk=list(reqs[0].risk), goal=reqs[0].goal, product=reqs[0].product,
                              own_check=reqs[0].own_check, scope=reqs[0].scope, named=reqs[0].named)
            if history:
                u.recent = f"Caller: {history[-1]['question']}\nYou: {_trim(history[-1]['answer'], 220)}"
            data["understanding"] = asdict(u)
            data["agenda_run"] = [i["id"] for i in items]
            yield TurnEvent("understood", lang, u.intent)
        else:
            u = replace(u, requests=reqs)  # same turn: keep its search keywords and recent exchange
        async for event in self._process(reqs, u, lang, tid, session_id, question, state, timings, data):
            yield event

    async def _agenda_reply(self, how: str, offer: dict, raw: str, lang, tid, session_id, question, history, facts,
                            state: CallState, timings, data, pick: int = 0, announced: bool = False) -> AsyncIterator[TurnEvent]:
        """The caller's answer to "Shall I go on to X?": continue (X, or X with the next short one), stop (the rest are
        skipped), skip (X is skipped, the next one follows), choose (the one they named)."""
        data["agenda_reply"] = how
        if how == "stop":
            agenda.stop(state)
            bye = is_goodbye(raw)
            if bye:
                data["goodbye"] = True
            text = (reply_for(raw, lang) if bye else "") or agenda.say("stopped", lang)
            if not announced:
                yield TurnEvent("understood", lang, "smalltalk")
            async for event in self._say(tid, session_id, "smalltalk", lang, text, "answered"):
                yield event
            return
        listed = sorted(agenda.open_items(state), key=lambda i: (-i["batch"], i["id"]))
        if how == "skip":
            agenda.skip(state, offer.get("item"))
            listed = [i for i in listed if i["id"] != offer.get("item")]
            run = agenda.plan(listed)
        elif how == "choose":
            chosen = agenda.picked(state, offer, pick)
            run = [chosen] if chosen is not None and chosen["status"] in agenda.OPEN else agenda.plan(listed)
        else:
            start = next((n for n, i in enumerate(listed) if i["id"] == offer.get("item")), 0)
            run = agenda.plan(listed[start:])
        if not run:  # nothing left (already answered elsewhere): say so plainly
            if not announced:
                yield TurnEvent("understood", lang, "smalltalk")
            async for event in self._say(tid, session_id, "smalltalk", lang, agenda.say("stopped", lang), "answered"):
                yield event
            return
        async for event in self._run_agenda(run, lang, tid, session_id, question, history, facts, state, timings, data):
            if announced and event.kind == "understood":
                continue
            yield event

    async def _understand_item(self, item: dict, history, facts, state: CallState, timings, data) -> bool:
        """An agenda item the caller asked but the first reading only listed (beyond the three detailed per turn): read
        it now from the caller's own words for it. One router call, the same as any other turn."""
        t = time.perf_counter()
        try:
            v, meta = await self.understand(item["quote"], history, facts, "", state.focus_text())
        except LLMError:
            logger.warning("agenda item not understood", exc_info=True)
            return False
        v = _corrected(v, item["quote"])
        timings["understand_ms"] = timings.get("understand_ms", 0) + _ms(t)
        r = v.requests[0]
        if r.kind == "fraud" and not r.risk:
            r.risk = list(v.risk)
        item["req"] = r.to_dict()
        item["department"] = v.department
        item["label"] = item.get("label") or r.label
        data.setdefault("agenda_understood", []).append({"item": item["id"], "llm": meta})
        return True

    async def _without_copies(self, u: Understanding, raw, question, facts, asked, state: CallState, previous: list[dict],
                              timings, data) -> Understanding:
        """Drop bank-data requests the LLM copied from the turn before (`copied`). If nothing is left, the sentence is read
        once more without the conversation (the conversation is where the copy came from). That reading is used if it
        found a real request; if it only finds a general question or small talk, the first reading stands: the caller
        may really have asked the same thing again, and "and the total?" needs the conversation to make sense."""
        copies = [r for r in u.requests if copied(r, raw, previous)]
        if len(copies) < len(u.requests):  # riders go only while a real request is left
            riders = [r for r in u.requests if r not in copies and rider(r, raw, previous)]
            if len(riders) + len(copies) < len(u.requests):
                copies += riders
        if copies:
            data["copied_requests"] = [r.question for r in copies]
            kept = [r for r in u.requests if r not in copies]
            if kept or issue_screened(raw):  # a problem the caller reports leads the turn anyway (with_issue)
                u.requests = kept
                if kept:
                    first = kept[0]
                    u.intent, u.standalone_question, u.personal, u.issue = first.kind, first.question, first.personal, first.issue
            else:
                t = time.perf_counter()
                try:
                    v, meta = await self.understand(question, [], facts, asked, state.focus_text())
                except LLMError:
                    logger.warning("re-reading a copied request failed", exc_info=True)
                    v, meta = None, {}
                timings["reread_ms"] = _ms(t)
                if v is not None:
                    v = _corrected(v, raw)
                    real = [r for r in v.requests if r.kind in ("personal", "issue", "fraud", "action", "human")
                            and not copied(r, raw, previous)]
                    data["reread"] = {"used": bool(real), "llm": meta}
                    if real:
                        u = v
        u.requests = without_duplicates(u.requests)
        if u.requests:
            first = u.requests[0]
            u.intent, u.standalone_question, u.personal, u.issue = first.kind, first.question, first.personal, first.issue
        carry_period(u, raw, previous)
        return u

    async def _second_look(self, u: Understanding, question, history, facts, state: CallState, timings, data, gap_words: int) -> None:
        """The coverage check found many of the caller's words in no listed question. One more router call, told what
        was already found, adds any question the first reading missed. Logged, so how often it happens is measured."""
        t = time.perf_counter()
        found = [a["quote"] for a in u.asks]
        try:
            v, meta = await self.understand(question, history, facts, "", state.focus_text(), found=found)
        except LLMError:
            logger.warning("second look failed", exc_info=True)
            data["coverage_retry"] = {"gap_words": gap_words, "error": True}
            return
        v = _corrected(v, question)
        added = [r for r in v.requests if r.kind != "smalltalk"
                 and not any(r.kind == x.kind and r.question.lower() == x.question.lower() for x in u.requests)]
        u.requests += added
        u.extra_asks += v.extra_asks
        u.asks += v.asks
        timings["second_look_ms"] = _ms(t)
        data["coverage_retry"] = {"gap_words": gap_words, "added": len(added) + len(v.extra_asks), "llm": meta}

    async def _offer_next(self, session_id: str, state: CallState, reply: Reply, data: dict) -> AsyncIterator[TurnEvent]:
        """End of a turn: hand open items to a person who is taking the call, or offer the next one ("Shall I go on to
        X?" / "Shall I go back to X?"). Never when the reply already ends in a question, a code request or a goodbye."""
        if reply.handoff or reply.outcome == "handoff":
            agenda.hand_over(state)
            return
        if (not reply.answer or reply.outcome in HOLDING or data.get("goodbye") or not self.journeys
                or await asyncio.to_thread(self.traces.pending, session_id)):
            return
        item = agenda.next_offer(state)
        if item is None:
            return
        lang = reply.language
        text = agenda.offer_text(state, item, lang)
        await asyncio.to_thread(self.traces.set_pending, session_id, make_pending(
            "agenda_next", lang, **agenda.offer_pending(state, item, text)))
        data["agenda_offer"] = item["id"]
        yield TurnEvent("sentence", lang, reply.intent, text, speech_text(text, lang))
        reply.answer = f"{reply.answer} {text}".strip()
        reply.speech_text = speech_text(reply.answer, lang)

    async def _dispatch(self, task, tid, session_id, question, view: Understanding, lang, timings, data,
                        journeys: bool, state: CallState) -> AsyncIterator[TurnEvent]:
        """The handler the registry names for this task. Access rules are enforced here, in code."""
        verified_world = bool(self.auth and session_id)
        if view.intent == "smalltalk":
            async for event in self._smalltalk(tid, session_id, question, view, lang, state):
                yield event
        elif task.handler == "kb":
            plan = pick_strategy(view.intent, view.goal, view.product, view.standalone_question, view.scope, view.named)
            data["strategy"] = plan
            if plan == "guided" and session_id:
                async for event in self._guided(tid, session_id, view, lang, state, timings, data):
                    yield event
                return
            stream = self._answer(tid, session_id, question, view, lang, timings, data, strategy=plan)
            async for event in stream:
                if (event.kind == "done" and journeys and view.intent == "general" and view.own_check
                        and event.reply.outcome == "answered" and can_offer_own(view.product)
                        and "check_own" not in state.declined):
                    try:
                        verified = await asyncio.to_thread(self.auth.verified_customer, session_id) is not None
                    except BankError:
                        verified = False
                    text, offer = own_offer(view.product, view.standalone_question, lang, verified)
                    await asyncio.to_thread(self.traces.set_pending, session_id, make_pending("check_own", lang, text=text, **offer))
                    yield TurnEvent("sentence", lang, view.intent, text, speech_text(text, lang))
                    event.reply.answer = f"{event.reply.answer} {text}"
                    event.reply.speech_text = speech_text(event.reply.answer, lang)
                    data["offer"] = "check_own"
                elif event.kind == "done" and journeys and view.intent == "new_customer" and event.reply.outcome in ("answered", "clarify"):
                    step = self.journeys.lead_offer(lang)  # a new customer who got an answer is offered help opening the account
                    await asyncio.to_thread(self.traces.set_pending, session_id, step.pending)
                    for sentence in step.sentences:
                        yield TurnEvent("sentence", lang, view.intent, sentence, speech_text(sentence, lang))
                    event.reply.answer = f"{event.reply.answer} {' '.join(step.sentences)}"
                    event.reply.speech_text = speech_text(event.reply.answer, lang)
                yield event
        elif task.handler == "bank_read" and verified_world:
            async for event in self._personal(tid, session_id, question, view, lang, timings, data, state):
                yield event
        elif (task.handler in ("action", "workflow") or task.access == "people") and journeys:
            async for event in self._journey(tid, session_id, question, view, lang, data, state):
                yield event
        elif task.handler == "handoff" and task.access == "open" and journeys:
            # An explicit request for a person is the consent: transfer straight away, no "Is that okay?".
            # (Consent is asked only when WE propose a transfer.)
            customer = await asyncio.to_thread(self.auth.verified_customer, session_id)
            department = "priority" if customer and customer.segment == "priority" and customer.rm_name else view.department
            channel = "call" if timings.get("input") in ("speech", "text", "after_verification") else "chat"
            step = await asyncio.to_thread(self.journeys.start_handoff, session_id, customer, department,
                                           "caller asked to speak to a person", lang, channel)
            async for event in self._emit_step(tid, session_id, step, lang, view.intent, data, customer):
                yield event
        else:  # fixed replies; also the safe fallback when there is no session to verify (plain /v1/ask)
            async for event in self._fixed(tid, session_id, view, lang):
                yield event

    async def _code_turn(self, tid, raw: str, session_id: str, timings: dict, started: float) -> AsyncIterator[TurnEvent]:
        """Six digits arrived as a chat message while a code is pending. The code never reaches the LLM, the history
        or the logs: the turn is stored as "[code]". Typed text stands in for the keypad; digits that came through
        speech recognition are NOT accepted (the caller is asked to use the keypad)."""
        lang = await asyncio.to_thread(self.traces.last_language, session_id) or "en"
        by_voice = timings.get("input") == "speech"
        pending_question = None
        if by_voice:
            text, outcome, payload = template("code_by_keypad", lang), "auth_required", None
        else:
            payload, text, lang = await self.verify(session_id, raw)
            pending_question = payload.pop("pending_question", None) if payload.get("status") == "verified" else None
            outcome = "auth_required" if payload["status"] in ("otp_sent", "invalid") else (
                "answered" if payload["status"] == "verified" else "handoff")
        if payload:
            yield TurnEvent("auth", lang, "personal", auth=payload)
        yield TurnEvent("sentence", lang, "personal", text, speech_text(text, lang))
        timings["total_ms"] = _ms(started)
        data = {"prompt_version": PROMPT_VERSION, "content": release_of(current()), "timings": timings, "outcome": outcome, "code_entry": True,
                "grounding": {"level": "n/a", "basis": ["verification code (never stored)"]}}
        trace = {"id": tid, "session_id": session_id, "question": "[code]", "language": lang, "intent": "personal",
                 "outcome": outcome, "answer": text, "total_ms": timings["total_ms"], "data": data}
        await asyncio.to_thread(self._record, trace)
        reply = Reply(tid, session_id, outcome, "personal", lang, text, speech_text(text, lang),
                      route="verify" if outcome == "auth_required" else None, auth=payload)
        if pending_question:  # verified: now answer what the caller asked before the code was needed
            async for event in self.turn(pending_question, session_id, {"input": "after_verification"}):
                yield event
            return
        yield TurnEvent("done", lang, "personal", reply=reply)

    def _record(self, trace: dict) -> None:
        """Save the turn (hash-chained), write the transcript log lines and raise any alerts. Runs in a thread
        after the last sentence was produced, so it adds nothing to the caller's wait."""
        digest = self.traces.save(trace)
        fields = (trace["data"].get("personal") or {}).get("fields") if trace["outcome"] == "answered" else None
        if fields is None and trace["intent"] in ("action", "issue", "fraud", "confirmation") and trace["outcome"] != "refused":
            fields = [trace["outcome"]]  # account activity: the log says what happened, never the amounts or names
        log_turn(trace["session_id"], trace["id"], trace["question"], trace["answer"], trace["intent"],
                 trace["outcome"], fields, digest)
        if self.audit:
            try:
                self.audit.after_turn(trace["session_id"], trace, trace["data"]["grounding"]["level"])
            except Exception:  # an alert problem must never break the conversation
                logger.exception("alert check failed")

    async def _smalltalk(self, tid, session_id, question, u: Understanding, lang, state: CallState) -> AsyncIterator[TurnEvent]:
        """A short line that fits what was said. The full greeting is said once per call (the voice line already played it)."""
        text = reply_for(question, lang)
        if not text:
            disclosed = bool(session_id) and await asyncio.to_thread(self.traces.has_event, session_id, "disclosure")
            if state.greeted or disclosed:
                text = SMALLTALK["hello_again"].get(lang, SMALLTALK["hello_again"]["en"])
            else:
                text = template("smalltalk", lang)
            state.greeted = True
        yield TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))
        yield TurnEvent("done", lang, u.intent, reply=Reply(tid, session_id, "smalltalk", u.intent, lang, text, speech_text(text, lang)))

    async def _fixed(self, tid, session_id, u: Understanding, lang) -> AsyncIterator[TurnEvent]:
        """Template replies: actions and issues (to a person - not on this line yet), handoff, smalltalk, guardrails."""
        if u.intent == "manipulation":
            logger.warning("security: manipulation attempt", extra={"data": {"trace_id": tid}})
        if u.intent in ("personal", "fraud"):  # no session (plain /v1/ask) or personal answers switched off
            kind, outcome, route = "needs_verification", "needs_verification", "auth_required"
        elif u.intent in ("action", "issue"):
            kind, outcome, route = ("action_unavailable" if u.intent == "action" else "issue"), "handoff", "handoff"
        else:
            outcome, route = OUTCOME_FOR_INTENT[u.intent], ROUTE_FOR_INTENT.get(u.intent)
            kind = u.intent if u.intent in TEMPLATES else outcome
        text = template(kind, lang)
        yield TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))
        yield TurnEvent("done", lang, u.intent, reply=Reply(tid, session_id, outcome, u.intent, lang, text,
                                                             speech_text(text, lang), route=route))

    async def _personal(self, tid, session_id, question, u: Understanding, lang, timings, data,
                        state: CallState | None = None) -> AsyncIterator[TurnEvent]:
        """The caller's own products: verify first (RMN + OTP), then answer from bank data with templates."""
        customer = await asyncio.to_thread(self.auth.verified_customer, session_id)
        if customer is None:
            result = await asyncio.to_thread(self.auth.start, session_id, u.resume_text or question)
            # "Sure." first: the question is acknowledged before the security step (PM review, 17 Nov)
            ack = auth_ack(personal_query(u.personal).topic, lang) if result.status == "otp_sent" else ""
            async for event in self._auth_step(tid, session_id, result, lang, u.intent, ack):
                yield event
            return
        q = personal_query(u.personal)
        if q.detail == "pending_check" and state is not None and state.focus.get("about") == "hold":
            q = PersonalQuery(**(asdict(q) | {"item": "hold"}))  # "did the pending payment get reversed?" right after the hold
        if q.topic == "loan":  # the loan the call is about, unless the caller names one or says "all my loans"
            q = loan_in_focus(q, question, (state.focus.get("loan") if state else "") or "")
        # The caller is answering "which account?": keep the question they asked, take only the account.
        pending = await asyncio.to_thread(self.auth.pending_query, session_id)
        choice = account_choice(question) or q.account
        if choice == "other":  # "the other one": the account that is not in focus
            focus = (state.focus.get("account") if state else "") or ""
            mine, _ = resolve_accounts(customer, focus, lang) if focus else (None, None)
            rest = [a for a in customer.accounts if mine and a not in mine]
            choice = rest[0].last4 if len(rest) == 1 else ""
            q = PersonalQuery(**(asdict(q) | {"account": choice}))
        # Resumed whatever topic the LLM gave the reply (it read "दूसरे account के बारे में" as an account-status question),
        # unless the caller's own words name a different topic.
        if pending and choice and not new_request(question, keep={pending.get("topic", "")}, general=False):
            q = PersonalQuery(**(pending | {"account": choice}))
            data["resumed_question"] = True
        t = time.perf_counter()
        ans = personal_answer(customer, q, lang, self.s.demo_today)
        timings["bank_ms"] = _ms(t)
        await asyncio.to_thread(self.auth.set_pending_query, session_id, asdict(q) if ans.outcome == "clarify" else None)
        if ans.fields:
            await asyncio.to_thread(self.auth.record_read, session_id, customer, q.topic, ans.fields)
        data["personal"] = {"customer": customer.cif, "query": asdict(q), "fields": ans.fields}
        if ans.offer and self.journeys:  # "I can raise a request to review it" -> a yes now does exactly that
            offer = await asyncio.to_thread(self.journeys.offer_pending, session_id, customer, ans.offer, lang)
            await asyncio.to_thread(self.traces.set_pending, session_id, offer)
        for sentence in ans.sentences:
            yield TurnEvent("sentence", lang, u.intent, sentence, speech_text(sentence, lang))
        if ans.needs_kb:  # e.g. "benefits of my account": the variant from bank data, the benefits from the KB
            general = Understanding(lang, "general", ans.needs_kb, ans.needs_kb, facts=u.facts)
            held: list[TurnEvent] = []  # an optional KB part is held back until we know the KB could answer
            async for event in self._answer(tid, session_id, ans.needs_kb, general, lang, timings, data):
                if event.kind == "done":
                    if ans.kb_optional and event.reply.outcome not in ("answered", "clarify"):
                        text = " ".join(ans.sentences + ans.after)  # the KB had nothing: the bank facts stand alone
                        for sentence in ans.after:
                            yield TurnEvent("sentence", lang, u.intent, sentence, speech_text(sentence, lang))
                        yield TurnEvent("done", lang, u.intent, reply=Reply(
                            tid, session_id, "answered", u.intent, lang, text, speech_text(text, lang),
                            citations=[{"source": "core banking (simulated: dummy data)", "fields": ans.fields}]))
                        return
                    for e in held:
                        yield e
                    for sentence in ans.after:
                        yield TurnEvent("sentence", lang, u.intent, sentence, speech_text(sentence, lang))
                    event.reply.answer = f"{' '.join(ans.sentences)} {event.reply.answer} {' '.join(ans.after)}".strip()
                    event.reply.speech_text = speech_text(event.reply.answer, lang)
                    event.reply.intent = u.intent
                    yield event
                elif ans.kb_optional:
                    held.append(event)
                else:
                    yield event
            return
        text = " ".join(ans.sentences)
        yield TurnEvent("done", lang, u.intent, reply=Reply(
            tid, session_id, ans.outcome, u.intent, lang, text, speech_text(text, lang),
            route="handoff" if q.topic == "other" else None,
            citations=[{"source": "core banking (simulated: dummy data)", "fields": ans.fields}] if ans.fields else []))

    async def _journey(self, tid, session_id, question, u: Understanding, lang, data,
                       state: CallState | None = None) -> AsyncIterator[TurnEvent]:
        """Card actions, fraud, complaints: verify first (same RMN + code flow), then the journey."""
        try:
            customer = await asyncio.to_thread(self.auth.verified_customer, session_id)
        except BankError:  # the bank's systems are down: for a fraud report, still get the caller to the fraud team
            if u.intent != "fraud":
                raise
            data["bank_down"] = True
            async for event in self._emit_step(tid, session_id, self.journeys.fraud_bank_down(lang), lang, u.intent, data):
                yield event
            return
        if customer is None:
            result = await asyncio.to_thread(self.auth.start, session_id, u.resume_text or question)
            prefix, suffix = "", ""
            if u.intent == "fraud":  # the report itself never waits for verification: the fraud team is one sentence away
                prefix = template(fraud_opening(u.risk), lang)
                suffix = template("fraud_team_option", lang) if result.status == "otp_sent" else ""
                if state is not None:
                    state.mark("fraud_opened")
            async for event in self._auth_step(tid, session_id, result, lang, u.intent, prefix, suffix):
                yield event
            return
        q = personal_query(u.personal)
        req = dict(u.action or {}) | {"department": u.department, "risk": u.risk,
                                      "opened": bool(state and "fraud_opened" in state.done)}
        if u.intent == "fraud" and state is not None:
            state.mark("fraud_opened")
        if u.intent == "fraud":
            step = await asyncio.to_thread(self.journeys.fraud, session_id, customer, req, q, lang)
        elif u.intent == "action":
            req["type"] = req.get("type") if req.get("type") in ("block", "unblock", "controls") else "other"
            step = await asyncio.to_thread(self.journeys.action, session_id, customer, req, lang)
        else:
            step = await asyncio.to_thread(self.journeys.issue, session_id, customer, u.issue, q, lang)
        async for event in self._emit_step(tid, session_id, step, lang, u.intent, data, customer):
            yield event

    async def _emit_step(self, tid, session_id, step: Step, lang, intent, data, customer=None) -> AsyncIterator[TurnEvent]:
        """Say a journey step, remember any question now waiting, and record what happened."""
        if session_id:
            await asyncio.to_thread(self.traces.set_pending, session_id, step.pending)
        if step.fields and customer and self.auth:
            await asyncio.to_thread(self.auth.record_read, session_id, customer, intent, step.fields)
        data["journey"] = dict(step.data) | ({"waiting_for": step.pending["kind"]} if step.pending else {})
        for sentence in step.sentences:
            yield TurnEvent("sentence", lang, intent, sentence, speech_text(sentence, lang))
        if step.handoff:
            if self.summariser and session_id:
                task = asyncio.create_task(self.summariser.summarise(session_id, step.handoff["id"]))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            yield TurnEvent("handoff", lang, intent, handoff=step.handoff)
        text = " ".join(step.sentences)
        yield TurnEvent("done", lang, intent, reply=Reply(tid, session_id, step.outcome, intent, lang, text,
                                                          speech_text(text, lang), route=step.route, handoff=step.handoff))

    async def _auth_step(self, tid, session_id, result, lang, intent, prefix: str = "", suffix: str = "") -> AsyncIterator[TurnEvent]:
        text = f"{prefix} {auth_message(result, lang, why_said=bool(prefix))} {suffix}".strip()
        payload = auth_payload(result)
        if result.status in ("locked", "too_many_codes") and self.journeys:
            await asyncio.to_thread(self.traces.set_pending, session_id, self.journeys.offer_transfer(
                "fraud" if intent == "fraud" else "general", lang, "caller could not be verified").pending)
        yield TurnEvent("auth", lang, intent, auth=payload)
        yield TurnEvent("sentence", lang, intent, text, speech_text(text, lang))
        waiting = result.status in ("otp_sent", "need_mobile", "not_registered")
        yield TurnEvent("done", lang, intent, reply=Reply(
            tid, session_id, "auth_required" if waiting else "handoff", intent, lang, text, speech_text(text, lang),
            route="verify" if waiting else "handoff", auth=payload))

    async def verify(self, session_id: str, code: str) -> tuple[dict, str, str]:
        """Check a keypad code. Returns (auth payload, what to say, language); the caller then re-asks the
        question that was waiting (payload["pending_question"])."""
        use(for_session(session_id))
        result = await asyncio.to_thread(self.auth.verify, session_id, code)
        lang = await asyncio.to_thread(self.traces.last_language, session_id) or "en"
        message = auth_message(result, lang)
        # An open request from the last 30 days: asked about only when the caller hasn't said why they called. A caller who
        # already said "my card was stolen" is helped with that, not asked "is today's call about your open dispute?"
        # (live call 210b57c4: the question was asked and the answer then went on without waiting for a reply).
        if result.status == "verified" and result.customer and self.journeys and not (result.pending_question or "").strip():
            note = await asyncio.to_thread(self.journeys.open_request_note, result.customer, lang)
            message = f"{message} {note}".strip()
        return auth_payload(result) | {"pending_question": result.pending_question}, message, lang

    async def handoff_timeout(self, session_id: str, handoff_id: str, language: str) -> str:
        use(for_session(session_id))
        return await asyncio.to_thread(self.journeys.handoff_timeout, session_id, handoff_id, language)

    async def resend(self, session_id: str) -> tuple[dict, str, str]:
        use(for_session(session_id))
        result = await asyncio.to_thread(self.auth.resend, session_id)
        lang = await asyncio.to_thread(self.traces.last_language, session_id) or "en"
        return auth_payload(result), auth_message(result, lang), lang

    async def _answer(self, tid, session_id, question, u: Understanding, lang, timings, data,
                      strategy: str = "direct") -> AsyncIterator[TurnEvent]:
        lead = ""  # the opener, said first while search and the answer model run (opener_for)
        if data.get("opener") and data.get("opener_for") == u.standalone_question:
            lead = data.pop("opener")
            data["opener_said"] = lead
            yield TurnEvent("sentence", lang, u.intent, lead, speech_text(lead, lang))

        def finish(outcome: str, text: str, reason: str | None = None, citations=None) -> TurnEvent:
            text = f"{lead} {text}".strip()  # the record holds everything the caller heard
            reply = Reply(tid, session_id, outcome, u.intent, lang, text, speech_text(text, lang),
                          route="handoff" if outcome == "refused" else None, reason=reason, citations=citations or [])
            return TurnEvent("done", lang, u.intent, reply=reply)

        def refusal(reason: str, spoken: list[str]) -> list[TurnEvent]:
            text = template("conflict" if reason == "conflicting_sources" else "partial_refer" if spoken else "refuse", lang)
            full = " ".join(spoken + [text])
            events = [TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))]
            events.append(finish("refused", full, reason))
            return events

        dense_query = u.search_query_en if self.s.dense_query == "english" and u.search_query_en else u.standalone_question
        reranked, fused = await self.retriever.search(dense_query, u.search_query_en or u.standalone_question, timings)
        data["retrieval"] = {
            "dense_query": dense_query, "keyword_query": u.search_query_en,
            "fused_top": [h.brief() | {"text": h.text} for h in fused[:15]],
            "reranked": [h.brief() | {"text": h.text} for h in reranked],
        }
        n_context = self.s.context_chunks if self.s.rerank else self.s.context_chunks_rrf
        context: list[Hit] = [h for h in reranked[:n_context]
                              if (h.rerank_score or 0) >= self.s.min_rerank_score * 0.5]
        if self.s.rerank:
            weak = not context or (reranked[0].rerank_score or 0) < self.s.min_rerank_score
        else:  # RERANK=false: no relevance score; the closest passage by meaning (embedding similarity) decides
            closest = max((h.dense_score or 0 for h in context), default=0.0)
            data["retrieval"].update(reranker="off", closest_dense=round(closest, 4))
            weak = not context or closest < self.s.min_dense_score
        if weak:
            for e in refusal("low_relevance", []):
                yield e
            return
        # Decided in code, before the answer model sees anything (PHASE-4-PLAN §5.2): which passages may be used, and
        # whether the caller must first say which variant they mean.
        context, source_notes = select_sources(f"{question} {u.standalone_question}", context, self.s.demo_today)
        if source_notes:
            data["source_notes"] = source_notes
        if not context:
            for e in refusal("low_relevance", []):
                yield e
            return
        # Relevance, product identity and duplicates (kbmeta.trim_context, tuned on saved golden runs), then for a
        # comparison: every named product must have a passage, or code says which side is missing.
        names = named_products(f"{question} {u.standalone_question}", u.named, fused + context)
        context, trim_notes = trim_context(context, names, strategy, f"{question} {u.standalone_question}")
        if trim_notes:
            data["context_trim"] = trim_notes
        missing = missing_products(context, names) if strategy == "compare" else []
        if missing and len(missing) < len(names):
            data["compare_missing"] = missing
        elif missing:
            missing = []  # nothing about any of them: the normal checks below decide
        options = None if strategy in ("overview", "recommend", "compare", "full") else variant_gate(
            f"{question} {u.standalone_question} {u.search_query_en}", describe_facts(u.facts), context, fused)
        if options:
            # Name the family's accounts, not only the two that matched the question (smoke G1: "like BSBDA or Aarambh",
            # the two zero-balance accounts): the ones that matched first, then the rest of the catalogue.
            family = next((product_of(h.section_path)[0] for h in context + fused if product_of(h.section_path)[1] in options), "")
            catalogue = await asyncio.to_thread(family_variants, self.retriever.db, family) if family else []
            names = list(dict.fromkeys(options + catalogue))
            data["variant_gate"] = names
            noun = _FAMILY_NOUN.get(family, (f"{family.lower()}s",) * 3)[{"en": 0, "hi": 1}.get(lang, 2)]
            text = template("variant_clarify", lang, n=len(names), family=noun, list=_or_list(names[:4]))
            yield TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))
            yield finish("clarify", text, "variant_not_named")
            return
        data["context_chunk_ids"] = [h.chunk_id for h in context]

        sources = "\n\n".join(f"[{i}] {h.section_path} ({provenance(h)}){mention_note(h, names)}\n{h.text}"
                                for i, h in enumerate(context, start=1))
        details = describe_facts(u.facts, u.product)
        catalog = current()
        strategy_lines = catalog.prompts["strategy"]
        messages = [
            {"role": "system", "content": catalog.answer_system(LANGUAGE_INSTRUCTIONS[lang])},
            {"role": "user", "content": f"Sources:\n{sources}\n\n" + (f"Caller details: {details}\n" if details else "")
             + (f"Last exchange:\n{u.recent}\n" if u.recent else "")
             + (f'You have just said to the caller: "{lead}" Continue straight into the answer, as the same reply: '
                "don't repeat it, and don't start with another acknowledgement (no \"Sure\", \"Let me check\", \"Okay\").\n"
                if lead else "")
             + f"What to do: {strategy_lines.get(strategy, strategy_lines['direct'])}"
             + (f" There is no source for {_or_list(missing)}: leave it out.\n" if missing else "\n")
             + f"Question: {u.standalone_question}"},
        ]
        source_text = [f"{h.section_path} {h.effective_from or ''} {h.text}" for h in context]
        base_allowed = (question, u.standalone_question)

        t = time.perf_counter()
        meta: dict = {}
        raw, mode = "", None  # mode: answer | clarify | no_answer, decided from the first characters
        spoken: list[str] = []
        cited: set[int] = set()
        checks: list[dict] = []
        splitter = SentenceSplitter()
        stream = self.llm.stream(self.s.answer_model, messages, meta, effort=self.s.answer_reasoning_effort or None)
        failed, capped = None, False
        # The prompt sets the length (progressive disclosure, services/dialogue.py). This is only a safety net for a
        # runaway answer, at a sentence boundary, and never for an explicit request for the full details.
        unlimited = strategy == "full"
        cap, word_cap = (999, 99999) if unlimited else (SAFETY_LIMIT["sentences"], SAFETY_LIMIT["words"])
        try:
            async for delta in stream:
                delta = delta.translate(_BRACKETS)
                raw += delta
                if mode is None:
                    mode = _output_mode(raw)
                    if mode != "answer":
                        continue
                    delta = raw.lstrip()  # release everything buffered so far
                elif mode != "answer":
                    continue
                for sentence in splitter.feed(delta):
                    sentence = clean_markup(sentence)
                    if not sentence:
                        continue
                    failed = self._check_sentence(sentence, context, source_text, base_allowed, cited, checks)
                    if failed:
                        break
                    if "first_sentence_ms" not in timings:
                        timings["first_sentence_ms"] = _ms(t)
                    spoken.append(sentence)
                    yield TurnEvent("sentence", lang, u.intent, sentence, speech_text(sentence, lang))
                    if len(spoken) >= cap or _words(spoken) >= word_cap:  # a phone answer is short; the caller can ask for more
                        capped = True
                        break
                if failed or capped:
                    break
        finally:
            await stream.aclose()
        timings["llm_ttft_ms"], timings["llm_ms"] = meta.get("ttft_ms"), _ms(t)
        data["answer_llm"], data["raw_answer"], data["checks"] = meta, raw.strip(), checks

        if mode is None:  # output ended while still ambiguous (very short or empty)
            head = raw.strip()
            mode = "no_answer" if not head or head.startswith("NO_ANSWER") else "conflict" if head.startswith("CONFLICT") else (
                "clarify" if head.upper().startswith("CLARIFY:") else "answer")
            if mode == "answer":
                splitter.feed(head)
        if mode in ("no_answer", "conflict"):
            for e in refusal("conflicting_sources" if mode == "conflict" else "not_in_sources", []):
                yield e
            return
        if mode == "clarify":
            ask_back = raw.strip().split(":", 1)[1].strip()
            markers = sorted({int(n) for n in _MARKER.findall(ask_back) if 1 <= int(n) <= len(context)})
            unsafe = unsafe_output(ask_back, " ".join(source_text))
            if unsafe or unsupported_numbers(ask_back, allowed_numbers(*base_allowed, *source_text)):
                data["checks"] = checks + ([{"sentence": ask_back, "unsafe": unsafe}] if unsafe else [])
                for e in refusal("unsafe_output" if unsafe else "figure_check_failed", []):
                    yield e
                return
            shown = re.sub(r"\s*\[\d+\]", "", ask_back)
            yield TurnEvent("sentence", lang, u.intent, shown, speech_text(shown, lang))
            yield finish("clarify", shown, citations=[
                {"marker": n, "chunk_id": context[n - 1].chunk_id, "document": context[n - 1].document_title,
                 "version": context[n - 1].version_no, "section": context[n - 1].section_path,
                 "effective_from": context[n - 1].effective_from, "snippet": context[n - 1].text[:240]} for n in markers])
            return
        if not failed and not capped:
            for sentence in splitter.flush():
                sentence = clean_markup(sentence)
                if not sentence:
                    continue
                failed = self._check_sentence(sentence, context, source_text, base_allowed, cited, checks)
                if failed:
                    break
                spoken.append(sentence)
                yield TurnEvent("sentence", lang, u.intent, sentence, speech_text(sentence, lang))
                if len(spoken) >= cap or _words(spoken) >= word_cap:
                    capped = True
                    break
        if (failed and not (spoken and cited)) or not cited:
            for e in refusal(failed or "uncited", spoken):
                yield e
            return
        if missing:  # a one-sided comparison is said honestly, by code
            text = phrase("answer.compare_missing", lang).format(names=_or_list(missing))
            spoken.append(text)
            yield TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))
        tail = "partial" if failed else "more_kb" if capped and "?" not in " ".join(spoken) else None
        if tail:  # keep what was checked and said; never guess the rest, never read on and on
            text = template(tail, lang)
            spoken.append(text)
            yield TurnEvent("sentence", lang, u.intent, text, speech_text(text, lang))
            data["answer_tail"] = tail
        citations = [{"marker": n, "chunk_id": context[n - 1].chunk_id, "document": context[n - 1].document_title,
                      "version": context[n - 1].version_no, "section": context[n - 1].section_path,
                      "effective_from": context[n - 1].effective_from, "snippet": context[n - 1].text[:240]}
                     for n in sorted(cited)]
        target = TARGET_WORDS.get(strategy)
        if target and _words(spoken) > target * 1.25:  # longer than the strategy aims for: flagged for review, not cut
            data["long_reply_target"] = {"strategy": strategy, "words": _words(spoken), "target": target}
        yield finish("answered", " ".join(spoken), reason=f"partial_{failed}" if failed else None, citations=citations)

    # ───────── guided "help me choose" flows (services/dialogue.py) ─────────

    async def _guided(self, tid, session_id, u: Understanding, lang, state: CallState, timings, data) -> AsyncIterator[TurnEvent]:
        """Start a flow: ask the first question that changes the recommendation (or recommend straight away)."""
        nxt = next_question(u.product, u.facts, [], lang)
        if nxt is None:
            async for event in self._recommend(tid, session_id, u.product, u, lang, timings, data):
                yield event
            return
        state.flow = {"name": u.product, "asked": [], "key": nxt[0], "question": nxt[1]}
        async for event in self._say(tid, session_id, u.intent, lang, nxt[1], "clarify"):
            yield event

    async def _flow(self, tid, session_id, raw, u: Understanding, lang, state: CallState, timings, data,
                    answered: bool = True) -> AsyncIterator[TurnEvent]:
        """The caller answered a flow question (or declined to): save the answer, then ask the next one or recommend."""
        flow = state.flow
        key = flow.get("key")
        data["strategy"] = "guided"
        if answered and key and str(u.facts.get(key) or "").strip() in ("", "unknown"):
            u.facts = merge_facts(u.facts, {key: raw})  # the caller's own words are the answer
            if session_id:
                await asyncio.to_thread(self.traces.save_facts, session_id, u.facts)
        flow["asked"] = list(dict.fromkeys(flow.get("asked", []) + ([key] if key else [])))
        nxt = next_question(flow["name"], u.facts, flow["asked"], lang) if answered else None
        if nxt:
            flow["key"], flow["question"] = nxt
            async for event in self._say(tid, session_id, "general", lang, nxt[1], "clarify"):
                yield event
            return
        state.flow = {}
        async for event in self._recommend(tid, session_id, flow["name"], u, lang, timings, data):
            yield event

    async def _recommend(self, tid, session_id, name: str, u: Understanding, lang, timings, data) -> AsyncIterator[TurnEvent]:
        question, search = recommend_request(name, u.facts)
        view = Understanding(lang, "general", question, search, facts=u.facts, product=name, goal="choose", recent=u.recent)
        data["strategy"] = "recommend"
        async for event in self._answer(tid, session_id, question, view, lang, timings, data, strategy="recommend"):
            if event.kind == "done" and self.journeys and session_id and event.reply.outcome == "answered":
                step = self.journeys.lead_offer(lang, apply=name != "savings_account")
                await asyncio.to_thread(self.traces.set_pending, session_id, step.pending)
                for sentence in step.sentences:
                    yield TurnEvent("sentence", lang, "general", sentence, speech_text(sentence, lang))
                event.reply.answer = f"{event.reply.answer} {' '.join(step.sentences)}"
                event.reply.speech_text = speech_text(event.reply.answer, lang)
            yield event

    def _last_ticket(self, session_id: str) -> str:
        """The reference of the last complaint or request raised in this call ("" if none)."""
        with self.audit.db.connection() as conn:
            row = conn.execute("SELECT detail FROM call_events WHERE session_id = %s AND kind = 'ticket' ORDER BY created_at DESC LIMIT 1",
                               (session_id,)).fetchone()
        return str((row or {}).get("detail", {}).get("id") or "") if row else ""

    async def _say(self, tid, session_id, intent: str, lang, text: str, outcome: str) -> AsyncIterator[TurnEvent]:
        yield TurnEvent("sentence", lang, intent, text, speech_text(text, lang))
        yield TurnEvent("done", lang, intent, reply=Reply(tid, session_id, outcome, intent, lang, text, speech_text(text, lang)))

    @staticmethod
    def _check_sentence(sentence, context, source_text, base_allowed, cited: set, checks: list) -> str | None:
        """None if the sentence may be released; otherwise the refusal reason."""
        if "NO_ANSWER" in sentence or "CONFLICT" in sentence or sentence.upper().startswith("CLARIFY:"):
            return "malformed_output"
        unsafe = unsafe_output(sentence, " ".join(source_text))  # never ask for a secret or give an unknown link
        if unsafe:
            checks.append({"sentence": sentence, "unsafe": unsafe})
            logger.warning("reply blocked before speech", extra={"data": {"rule": unsafe}})
            return "unsafe_output"
        markers = [int(n) for n in _MARKER.findall(sentence) if 1 <= int(n) <= len(context)]
        allowed = allowed_numbers(*base_allowed, *(source_text[n - 1] for n in markers))
        bad = unsupported_numbers(sentence, allowed)
        spelled = spelled_figures(sentence)  # a figure in words can't be checked against the source: not allowed
        checks.append({"sentence": sentence, "markers": markers, "unsupported_numbers": bad + spelled})
        if bad or spelled:
            return "figure_check_failed"
        cited.update(markers)
        return None

    async def search_only(self, query: str) -> dict:
        """Retrieval without answering - for debugging what the search finds."""
        timings: dict = {}
        t = time.perf_counter()
        u, _ = await self.understand(redact(query), [])
        timings["understand_ms"] = _ms(t)
        dense_query = u.search_query_en if self.s.dense_query == "english" and u.search_query_en else u.standalone_question
        reranked, fused = await self.retriever.search(dense_query, u.search_query_en or query, timings)
        return {"understanding": asdict(u), "timings": timings,
                "reranked": [h.brief() | {"text": h.text} for h in reranked],
                "fused_top": [h.brief() for h in fused[:20]]}


# The detail is the more specific signal: when the model's topic disagrees with it, the detail decides.
_DETAIL_TOPIC = {
    **dict.fromkeys(("minimum_balance", "balance_maintained", "charges"), "charges"),
    **dict.fromkeys(("salary_account", "linked_debit_card", "benefits", "status"), "account"),
    **dict.fromkeys(("all_fds", "first_maturing", "maturity_amount", "rate", "auto_renew", "premature_value"), "fd"),
    **dict.fromkeys(("outstanding", "next_emi", "emi_status", "emis_count", "emi_increase", "bounce_charges", "foreclosure"), "loan"),
    **dict.fromkeys(("amount_due", "payment_status", "available_limit", "international", "lounge", "annual_fee",
                     "statement"), "credit_card"),
    **dict.fromkeys(("controls", "declines"), "debit_card"),
    **dict.fromkeys(("upcoming", "inactive", "highest"), "summary"),
    "duplicate": "transactions", "available_balance": "balance",
}
# Regression pack, 17 Nov: "How much do I still owe?" on a gold-loan call came back as loan + `amount_due`, and the detail
# moved the question to credit cards ("I don't see a credit card in your name"); "when was my last EMI deducted?" as loan +
# `payment_status` read out card payments. On a loan, these details mean the loan's own fact.
_LOAN_SAME = {"amount_due": "outstanding", "payment_status": "emi_status", "statement": "emi_status", "available_limit": "outstanding"}
_TOPIC_KEEPS = {("transactions", "salary_account"), ("account", "available_balance"), ("credit_card", "outstanding"),
               ("credit_card", "status"), ("debit_card", "status"),  # "do I have any cards?" (call de568948: account status)
               ("transactions", "payment_status")}  # "was my UPI payment to Suresh completed?" is a transaction, not a card


def personal_query(p: dict | None) -> PersonalQuery:
    """The understanding's `personal` block as a typed query (unknown values fall back to safe defaults)."""
    p = p or {}
    topic = p.get("topic") if p.get("topic") in TOPICS else "other"
    detail = p.get("detail") if p.get("detail") in DETAILS + CODE_DETAILS else ""
    if topic == "loan" and detail in _LOAN_SAME:  # a loan question labelled with a card detail stays on the loan
        detail = _LOAN_SAME[detail]
    if detail in _DETAIL_TOPIC and (topic, detail) not in _TOPIC_KEEPS:
        topic = _DETAIL_TOPIC[detail]
    if topic == "transactions" and detail == "salary_account":  # "was my salary credited?"
        detail, p = "", {**p, "keyword": p.get("keyword") or "salary"}
    return PersonalQuery(topic=topic, detail=detail,
                         account=str(p.get("account") or ""), item=_specific_item(p.get("item")),
                         card=p.get("card") if p.get("card") in ("debit", "credit") else "",
                         direction=p.get("direction") or "any",
                         period=p.get("period") if p.get("period") in PERIODS else "",
                         count=int(p.get("count") or 0), min_amount=float(p.get("min_amount") or 0),
                         amount=float(p.get("amount") or 0), keyword=str(p.get("keyword") or "").lower(),
                         total=bool(p.get("total")))


_GENERIC_ITEMS = {"credit", "debit", "card", "credit card", "debit card", "loan", "fd", "account", "my card", "the card",
                  "annual_fee", "annual fee"}


def _specific_item(value) -> str:
    """The FD / loan / card the caller named, or "" when the router passed a generic word (a live test: "credit" for
    "my credit card" made the agent say it could not find a card called "credit")."""
    item = str(value or "").strip()
    return "" if item.lower() in _GENERIC_ITEMS else item


def _output_mode(raw: str) -> str | None:
    """'no_answer' / 'clarify' once a sentinel is complete, 'answer' once the text can't be one, else None (wait)."""
    head = raw.lstrip()
    if head.startswith("NO_ANSWER"):
        return "no_answer"
    if head.startswith("CONFLICT"):
        return "conflict"
    if head.upper().startswith("CLARIFY:"):
        return "clarify"
    if head and not ("NO_ANSWER".startswith(head[:9]) or "CONFLICT".startswith(head[:8])
                     or "CLARIFY:".startswith(head[:8].upper())):
        return "answer"
    return None

