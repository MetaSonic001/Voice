"""Dialogue decisions made in code (ADR 0024): caller facts and their scope, the response strategy for a request,
the guided "help me choose" flows and the "shall I check yours?" offer.

The LLM understands the turn and phrases answers; this module decides what the agent does next. Everything here is
plain data and pure functions, so it is cheap and easy to test.

Caller facts
- Only what the caller said in this call (the understanding step returns the facts stated in the current turn; code
  merges them here). Bank data never becomes a "fact": balances, rates, ages for eligibility come from the bank.
- Scope: CALL facts last the whole call (senior citizen, occupation). TASK facts belong to the product being discussed
  (tenure, amount, spend, travel...) and are cleared when the caller moves to a different product.
- Only the facts relevant to the product in question are sent to the answer model.
"""

import re

from app.services.content import phrase
from app.services.privacy import redact

CALL_FACTS = ("senior_citizen", "occupation")
TASK_FACTS = ("product", "tenure", "amount", "account", "fd_type", "purpose", "balance_preference", "monthly_spend", "travel")
FACT_KEYS = CALL_FACTS + TASK_FACTS
_LABELS = {"senior_citizen": "senior citizen", "balance_preference": "balance preference", "monthly_spend": "monthly spend",
           "fd_type": "FD type"}

# Facts the answer model gets, per product type (call facts always).
RELEVANT = {
    "fd": ("tenure", "amount", "fd_type", "purpose"),
    "rd": ("tenure", "amount", "purpose"),
    "savings_account": ("account", "purpose", "balance_preference"),
    "current_account": ("account", "purpose", "balance_preference"),
    "credit_card": ("monthly_spend", "travel", "purpose"),
    "debit_card": ("account",),
    "loan": ("amount", "tenure", "purpose"),
}


_FAMILIES = [("fd", r"\bfd\b|fixed deposit|term deposit|एफडी|सावधि"), ("rd", r"\brd\b|recurring|आरडी"),
             ("credit_card", r"credit|क्रेडिट|zenith|\bcc\b"), ("debit_card", r"debit card|डेबिट"),
             ("loan", r"loan|लोन|\bemi\b"), ("account", r"saving|current|account|खाता|खाते|अकाउंट")]


def product_family(text: str) -> str:
    """'Zenith+ credit card' and 'credit card' are one family; 'FD' and 'fixed deposit' too. '' if unknown."""
    low = text.lower()
    return next((name for name, pattern in _FAMILIES if re.search(pattern, low)), "")


def merge_facts(known: dict, new: dict) -> dict:
    """The caller's latest clear statement wins; silence keeps what was known ("not said" never means "no").
    A different product starts a new task: the task facts of the old one are dropped, call facts stay."""
    out = dict(known)
    product = str(new.get("product") or "").strip()
    if product and out.get("product"):
        before, now = product_family(out["product"]), product_family(product)
        if before and now and before != now:
            for key in TASK_FACTS:
                out.pop(key, None)
    for key in FACT_KEYS:
        value = str(new.get(key) or "").strip()
        if value and value.lower() != "unknown":
            out[key] = redact(value)[:60]
    return out


def describe_facts(facts: dict, product: str | None = None) -> str:
    """Facts as one line. With a product: only the call facts plus the ones that matter for that product."""
    keys = None if product is None else set(CALL_FACTS) | set(RELEVANT.get(product, ()))
    return "; ".join(f"{_LABELS.get(k, k)}: {v}" for k, v in facts.items()
                     if v and v != "unknown" and k in FACT_KEYS and (keys is None or k in keys))


# ───────── response strategy (decided in code, recorded on every turn) ─────────

_FULL = re.compile(r"\b(all (the )?(features|details|benefits|charges|fees)|every (feature|detail|charge)|everything|"
                   r"full (details|breakdown|list)|complete details|in detail)\b|saari (details|jaankari)|poori jaankari|"
                   r"सारी जानकारी|पूरी जानकारी|सभी (फीचर|शुल्क|चार्ज)", re.I)
_OVERVIEW = re.compile(r"\b(all|every|each|compare|comparison|list|sabhi|sab|saare|sare)\b|सभी|सब|सारे|तुलना", re.I)
_BROAD = re.compile(r"^\s*(tell me (more )?about|what is|what's|what are|explain|details (of|about)|info(rmation)? (on|about)|"
                    r"describe)\b|ke baare mein|के बारे में", re.I)
# A named detail makes it a specific question, answered on its own ("What is the forex markup on Zenith+?").
_DETAIL = re.compile(r"\b(fees?|charges?|rate|interest|markup|limit|lounge|rewards?|points|eligib\w*|minimum|penalty|"
                     r"documents?|waiver|cashback|how (many|much)|tenure|emi|age|income)\b|शुल्क|ब्याज|चार्ज", re.I)


_ALL_WORDS = re.compile(r"\b(all|every|everything|full|complete|whole|detail\w*|sab|sabhi|saari|sari|poori|puri|pura)\b|"
                        r"सभी|सब|सारी|सारे|पूरी|पूरा|विस्तार", re.I)


def strategy(kind: str, goal: str, product: str, question: str, scope: str = "", named: list | None = None) -> str:
    """How to answer, decided in code. Knowledge-base answers use progressive disclosure: a broad question gets a short
    overview (with an offer to go deeper), a specific question gets just that detail, and only an explicit request for
    everything gets the full breakdown.

    The router's `scope` is used when it passes the code's sanity checks (compare needs two named products; full needs
    the caller to have asked for "all / everything / poori"); otherwise the code's own patterns decide.
    direct | product_overview | overview | full | compare | guided | recommend | verify_fetch | action | complaint | fraud |
    handoff | smalltalk | unsupported."""
    named = named or []
    if kind in ("general", "new_customer"):
        if (goal == "choose" or scope == "choose") and product in FLOWS:
            return "guided"
        if scope == "compare" and len(named) >= 2:
            return "compare"
        if scope == "full" and _ALL_WORDS.search(question):
            return "full"
        if scope == "overview":
            return "product_overview" if len(named) == 1 or (product and not _OVERVIEW.search(question)) else "overview"
        if scope == "specific":
            return "direct"
        if _FULL.search(question):
            return "full"
        if _OVERVIEW.search(question):
            return "overview"
        if product and _BROAD.search(question) and not _DETAIL.search(question) and len(question.split()) <= 12:
            return "product_overview"  # "Tell me about the Zenith+ card" - not "What is the forex markup on it?"
        return "direct"
    return {"personal": "verify_fetch", "action": "action", "issue": "complaint", "fraud": "fraud", "human": "handoff",
            "smalltalk": "smalltalk"}.get(kind, "unsupported")


# What the answer model is told to do, per strategy (brain/content/catalog.json prompts.strategy), and the length each
# one aims at (words). The target is what the
# prompt asks for; going over it is flagged for review (`long_reply`). A much longer safety limit in code stops a runaway
# answer at a sentence boundary; it never applies to "full".
TARGET_WORDS = {"direct": 50, "product_overview": 65, "overview": 70, "recommend": 80, "compare": 90}
SAFETY_LIMIT = {"sentences": 6, "words": 110}  # per answer; "full" has none

# ───────── guided "help me choose" flows ─────────
# Each flow asks only questions that change the recommendation, one per turn, and skips any whose answer is known.

# The questions of each flow, in order (wording: brain/content/catalog.json, "choose.<flow>.<key>").
FLOWS = {"credit_card": ("monthly_spend", "travel"), "savings_account": ("purpose", "balance_preference"),
         "fd": ("tenure", "senior_citizen")}
# An occupation already tells us what the account is for (students, farmers, salaried people have their own accounts).
_IMPLIES = {"purpose": ("occupation",)}
_LABEL_EN = {"credit_card": "credit card", "savings_account": "savings account", "fd": "fixed deposit (FD)"}
_SEARCH = {"credit_card": "credit card features benefits reward points lounge annual fee eligibility",
           "savings_account": "savings account features benefits minimum balance AMB eligibility",
           "fd": "fixed deposit FD interest rates tenure senior citizen"}


def _known(facts: dict, key: str) -> bool:
    keys = (key,) + _IMPLIES.get(key, ())
    return any(str(facts.get(k) or "").strip() not in ("", "unknown") for k in keys)


def next_question(name: str, facts: dict, asked: list[str], lang: str) -> tuple[str, str] | None:
    """(fact key, question to ask) for the next unanswered question of a flow, or None when it is time to recommend."""
    for key in FLOWS[name]:
        if key in asked or _known(facts, key):
            continue
        text = phrase(f"choose.{name}.{key}", lang)
        return key, (phrase("choose.first", lang) + text) if not asked else text
    return None


def recommend_request(name: str, facts: dict) -> tuple[str, str]:
    """(question, English search keywords) for the recommendation at the end of a flow."""
    needs = describe_facts(facts, name) or "no particular needs stated"
    return f"Which {_LABEL_EN[name]} would suit this customer, and why? Their needs: {needs}.", _SEARCH[name]


# ───────── "shall I check yours?" after a general answer ─────────

_THING = {"fd": {"en": "FD", "hi": "FD", "hinglish": "FD"},
          "loan": {"en": "loan", "hi": "लोन", "hinglish": "loan"},
          "credit_card": {"en": "credit card", "hi": "क्रेडिट कार्ड", "hinglish": "credit card"},
          "debit_card": {"en": "debit card", "hi": "डेबिट कार्ड", "hinglish": "debit card"},
          "savings_account": {"en": "account", "hi": "अकाउंट", "hinglish": "account"},
          "current_account": {"en": "account", "hi": "अकाउंट", "hinglish": "account"}}
_TOPIC = {"fd": "fd", "loan": "loan", "credit_card": "credit_card", "debit_card": "debit_card",
          "savings_account": "charges", "current_account": "charges"}


def can_offer_own(product: str) -> bool:
    return product in _TOPIC


def _detail(product: str, text: str) -> str:
    low = text.lower()
    if product == "fd":
        if re.search(r"prematur|break|before maturity|early|tod|close|तोड़|पहले|बंद", low):
            return "premature_value"
        return "auto_renew" if re.search(r"renew|रिन्यू", low) else ""
    if product == "loan":
        return "foreclosure" if re.search(r"foreclos|prepay|pre-pay|preclos|close|band|बंद", low) else ""
    if product in ("savings_account", "current_account"):
        return "minimum_balance" if re.search(r"minimum|min bal|amb|मिनिमम|न्यूनतम", low) else "charges"
    if product == "credit_card":
        return "annual_fee" if re.search(r"fee|waiv|शुल्क", low) else ""
    return ""


def own_offer(product: str, question: str, lang: str, verified: bool) -> tuple[str, dict]:
    """What to say and the pending question to store, after a general answer the caller may want checked for themselves."""
    thing = _THING[product].get(lang, _THING[product]["en"])
    text = phrase("offer.verified" if verified else "offer.unverified", lang).format(thing=thing)
    own_q = phrase("offer.own_question", lang).format(thing=thing, q=question)
    personal = {"topic": _TOPIC[product], "detail": _detail(product, question), "account": "", "item": "", "card": "",
                "amount": 0, "direction": "any", "period": "", "count": 0, "min_amount": 0, "keyword": "", "total": False}
    return text, {"question": own_q, "personal": personal, "product": product}
