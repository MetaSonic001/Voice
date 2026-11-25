"""Answers about the verified caller's own products, from the bank's systems, through fixed templates (en / hi / hinglish).

No LLM writes these answers. The understand step only picks a topic, one detail from a fixed list and filters;
the figures come straight from the bank data, so they cannot be invented. Only the last 4 digits of any account,
card, FD or loan are ever said. Where the data can't answer, the reply says what it can see and offers a person.
"""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from app.services.content import Table
from app.domain import Account, Customer, Loan, Transaction

TOPICS = ("balance", "transactions", "account", "charges", "fd", "loan", "credit_card", "debit_card", "summary", "other")
PERIODS = ("", "today", "yesterday", "last_7_days", "this_week", "this_month", "last_month", "last_30_days")
DETAILS = (
    "",  # the usual answer for the topic
    # account / balance / charges
    "available_balance", "salary_account", "linked_debit_card", "minimum_balance", "balance_maintained", "charges",
    "benefits", "status",
    # FD
    "all_fds", "first_maturing", "maturity_amount", "rate", "auto_renew", "premature_value",
    # loan
    "outstanding", "next_emi", "emi_status", "emis_count", "emi_increase", "bounce_charges", "foreclosure",
    # credit card
    "amount_due", "payment_status", "available_limit", "international", "lounge", "annual_fee", "statement",
    # debit card
    "controls", "declines",
    # transactions / summary
    "duplicate", "upcoming", "inactive", "highest",
)
# Set only by code corrections (`understanding.asked_only`), never offered to the LLM: kept out of DETAILS so the
# router's schema (and every cached eval request) stays the same.
CODE_DETAILS = ("loan_count", "loan_count_left", "hold_check", "available_only", "total_scope", "failed_only", "pending_check", "hold_why", "emi_last_paid", "emi_recovery", "repaid_amount")
_HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


@dataclass
class PersonalQuery:
    topic: str = "other"
    detail: str = ""  # one of DETAILS
    account: str = ""  # which savings account: "salary", "4521", "digital", "second", "all", or "" (not said)
    item: str = ""  # which FD / loan / card: last 4 digits ("7824", "6712") or a type ("home", "personal", "zenith")
    card: str = ""  # debit | credit | "" (for card questions)
    direction: str = "any"  # any | debit | credit
    period: str = ""
    count: int = 0  # number of transactions asked for (0 = default 5)
    min_amount: float = 0
    amount: float = 0  # an amount the caller mentions ("the ₹799 debit", "around 2,000")
    keyword: str = ""  # a word to match in transactions: channel, merchant or person ("upi", "rajesh", "restaurant")
    total: bool = False  # "how much did I spend": sum the matching transactions


@dataclass
class PersonalAnswer:
    sentences: list[str]
    outcome: str = "answered"  # answered | clarify
    fields: list[str] = field(default_factory=list)  # what was disclosed (audit)
    needs_kb: str = ""  # the answer needs the knowledge base too (e.g. "benefits of the Value savings account")
    offer: dict = field(default_factory=dict)  # the answer ends with an offer a "yes" should carry out:
    # {"handoff": dept} | {"ticket": category} | {"action": request}
    kb_optional: bool = False  # the knowledge-base part only adds to the bank facts: if the KB has no answer, say only those
    after: list[str] = field(default_factory=list)  # said after the knowledge-base part (e.g. the offer to review a charge)


# ───────── wording ─────────
# Short, warm and natural: what a good phone banker would say. {placeholders} are filled in code.

# Why money is on hold, said in the caller's language: the bank gives the reason in English ("a pending card payment at a
# fuel station"). A Hindi sentence with an English aside in brackets was read out as one run-on phrase (PM review, 17 Nov).
# Production: the core bank sends a hold reason code, mapped here the same way.
_HOLD_REASONS = {"a pending card payment at a fuel station": ("फ्यूल स्टेशन पर कार्ड से किया गया एक पेमेंट अभी पेंडिंग है",
                                                             "fuel station par card se kiya gaya ek payment abhi pending hai")}


def hold_reason(reason: str | None, lang: str) -> str:
    """The hold reason as a clause in the caller's language; English (or an unknown reason) as the bank gave it."""
    if lang == "en" or not reason:
        return reason or "a pending payment"
    known = _HOLD_REASONS.get(reason.strip().lower())
    if known:
        return known[0 if lang == "hi" else 1]
    return "एक पेमेंट अभी पेंडिंग है" if lang == "hi" else "ek payment abhi pending hai"


T = Table("bank")  # brain/content/catalog.json, "bank.*"

VERIFY_LOAN = {"home": ("home", "होम", "home"), "personal": ("personal", "पर्सनल", "personal"),
               "vehicle": ("vehicle", "व्हीकल", "vehicle"), "gold": ("gold", "गोल्ड", "gold"), "business": ("business", "बिज़नेस", "business")}
CHARGE_KB = {  # what to look up in the knowledge base to explain a charge found in the bank data
    "amb_shortfall": "How is the charge for not maintaining the average monthly balance worked out on the AU {type} savings account?",
    "debit_card_annual_fee": "What is the annual fee for the AU debit card on the {type} savings account?",
    "sms_alerts": "What are the SMS alert charges on AU savings accounts?",
    "emi_bounce": "What is the charge when a loan EMI bounces?",
}
CHARGE_NAMES = {"amb_shortfall": ("not keeping the minimum balance", "न्यूनतम बैलेंस न रखने", "minimum balance na rakhne"),
                "sms_alerts": ("SMS alerts", "SMS अलर्ट", "SMS alerts"),
                "debit_card_annual_fee": ("the debit card annual fee", "डेबिट कार्ड सालाना फीस", "debit card annual fee"),
                "emi_bounce": ("an EMI bounce", "EMI बाउंस", "EMI bounce")}
_L = {"en": 0, "hi": 1, "hinglish": 2}


def fill(table: dict, key: str, lang: str, **values) -> str:
    """One fixed sentence from a template table (personal.T, journeys.J, actions.W, conversation.TEMPLATES), in the
    caller's language (English when a language is missing), with the values filled in."""
    if "acct" in values and "Acct" not in values:  # "{Acct}" starts a sentence with the account
        values["Acct"] = _cap(values["acct"])
    text = table[key].get(lang, table[key]["en"])
    text = text.format(**values) if values else text
    return re.sub(r"आपके (\d{4}) वाला", r"आपके \1 वाले", text)  # "आपके 6712 वाले कार्ड पर" (oblique case)


def t(key: str, lang: str, **values) -> str:
    return fill(T, key, lang, **values)


def money(amount: float) -> str:
    """Indian digit grouping: 946213 -> '9,46,213'."""
    whole, paise = divmod(round(amount * 100), 100)
    s = str(whole)
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    out = (head + "," + tail) if head else tail
    return f"{out}.{paise:02d}" if paise else out


def day(d: date | None, lang: str) -> str:
    """A date as a person says it on the phone: "10 December" ("10 दिसंबर"). The year only when it isn't obvious
    (more than about six months away); the full month name, because "Dec" is read letter by letter."""
    if d is None:
        return "-"
    year = f" {d.year}" if abs((d - date.today()).days) > 180 else ""
    return f"{d.day} {_HI_MONTHS[d.month - 1]}{year}" if lang == "hi" else f"{d.day} {d.strftime('%B')}{year}"


_HI_DAYS = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")
# Part of the day said with a 12-hour time; a 24-hour "21:40" is how a statement is printed, not how people talk.
_DAY_PARTS = {"en": ("at night", "in the morning", "in the afternoon", "in the evening", "at night"),
              "hi": ("रात", "सुबह", "दोपहर", "शाम", "रात"), "hinglish": ("raat", "subah", "dopahar", "shaam", "raat")}


def _clock(time: str, lang: str) -> str:
    """"21:40" -> "9:40 in the evening" / "शाम 9:40" / "shaam 9:40"; "" if there is no time."""
    try:
        h, m = (int(x) for x in time.split(":")[:2])
    except ValueError:
        return ""
    part = _DAY_PARTS.get(lang, _DAY_PARTS["en"])[0 if h < 4 else 1 if h < 12 else 2 if h < 16 else 3 if h < 21 else 4]
    hh = f"{h % 12 or 12}:{m:02d}"
    return f"{hh} {part}" if lang == "en" else f"{part} {hh}"


def _when(d: date, time: str, lang: str, today: date | None = None) -> str:
    """When something happened, as a caller would say it: "On Friday, 6 November, at 9:15 in the morning", or "Today,
    at 9:15 in the morning" / "Yesterday, ..." when `today` is given."""
    clock = _clock(time, lang) if time else ""
    rel = {0: ("Today", "आज", "Aaj"), 1: ("Yesterday", "कल", "Kal")}.get((today - d).days) if today else None
    if rel:
        word = rel[_L.get(lang, 0)]
        if lang == "hi":
            return word + (f" {clock} बजे" if clock else "") + ","
        if lang == "hinglish":
            return word + (f" {clock} baje" if clock else "") + ","
        return word + (f", at {clock}" if clock else "")
    if lang == "hi":
        return f"{_HI_DAYS[d.weekday()]}, {day(d, lang)} को" + (f" {clock} बजे" if clock else "") + ","
    if lang == "hinglish":
        return f"{d.strftime('%A')}, {day(d, lang)} ko" + (f" {clock} baje" if clock else "") + ","
    return f"On {d.strftime('%A')}, {day(d, lang)}" + (f", at {clock}" if clock else "")


def period_range(period: str, today: date) -> tuple[date, date] | None:
    return {"today": (today, today), "yesterday": (today - timedelta(days=1),) * 2,
            "last_7_days": (today - timedelta(days=6), today), "this_week": (today - timedelta(days=today.weekday()), today),
            "this_month": (today.replace(day=1), today), "last_30_days": (today - timedelta(days=29), today),
            "last_month": ((today.replace(day=1) - timedelta(days=1)).replace(day=1), today.replace(day=1) - timedelta(days=1)),
            }.get(period)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ───────── answering ─────────


def answer(customer: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    handler = {"balance": _balance, "transactions": _transactions, "account": _account, "charges": _charges, "fd": _fd,
               "loan": _loan, "credit_card": _credit_card, "debit_card": _debit_card, "summary": _summary}.get(q.topic)
    if handler is None:
        return PersonalAnswer([t("cannot_see", lang)], offer={"handoff": "general"})
    return handler(customer, q, lang, today)


_KNOWN_TYPES = ("bsbda", "digital", "vikaas", "value", "platinum", "royale", "kisan", "swadesh", "aarambh")
_CHOICE_WORDS = [
    ("all", r"\b(both|all|every|dono|donon|sab|sabhi)\b|दोनों|सभी|सब"),
    ("other", r"\b(the other|other one|other account|another one)\b|doosra wala|dusra wala|दूसरा वाला"),
    ("first", r"\b(first|1st|pehla|pehle|pahla)\b|पहला|पहले"),
    ("second", r"\b(second|2nd|dusra|doosra|dusre|doosre)\b|दूसरा|दूसरे"),
    ("third", r"\b(third|3rd|teesra|tisra|teesre)\b|तीसरा|तीसरे"),
    ("salary", r"\bsalary\b|सैलरी|वेतन"),
]


def account_choice(text: str) -> str:
    """The account a caller names when answering "which account?" ("second one", "dusra wala", "0002", "salary"),
    read in code - not left to the LLM. Empty if the text doesn't name one."""
    low = text.lower()
    if m := re.search(r"(?<!\d)(\d{4})(?!\d)", low):
        return m.group(1)
    for value, pattern in _CHOICE_WORDS:
        if re.search(pattern, low):
            return value
    return next((k for k in _KNOWN_TYPES if re.search(rf"\b{k}\b", low)), "")


# (en, hi, hinglish, feminine in Hindi) for the things a caller may have several of
_NOUNS = {"account": ("account", "अकाउंट", "account", False), "FD": ("FD", "FD", "FD", True),
          "loan": ("loan", "लोन", "loan", False), "credit card": ("credit card", "क्रेडिट कार्ड", "credit card", False),
          "debit card": ("debit card", "डेबिट कार्ड", "debit card", False)}
_ORD = {"en": ("first", "second", "third", "fourth", "fifth", "sixth"),
        "hi": (("पहला", "दूसरा", "तीसरा", "चौथा", "पाँचवाँ", "छठा"), ("पहली", "दूसरी", "तीसरी", "चौथी", "पाँचवीं", "छठी")),
        "hinglish": (("pehla", "doosra", "teesra", "chautha", "paanchva", "chhatha"), ("pehli", "doosri", "teesri", "chauthi", "paanchvi", "chhathi"))}


def plural(noun: str, lang: str) -> str:
    word = _NOUNS[noun][{"hi": 1, "hinglish": 2}.get(lang, 0)]
    return word if lang == "hi" or word.endswith("s") else word + "s"


def _listing(items, lang: str, label, noun: str) -> str:
    """The caller's accounts / cards / FDs as one spoken sentence, so they can pick one: "One is your Digital account
    ending 4512, which is inactive, and the other is your Value account ending 4521." An inactive one is said as such."""
    en, hi, hinglish, fem = _NOUNS[noun]
    word = {"hi": hi, "hinglish": hinglish}.get(lang, en)
    parts = []
    for x in items:
        name = " ".join(p for p in (label(x), word) if p)
        inactive = getattr(x, "status", "") == "inactive"
        if lang == "hi":
            parts.append(f"{'आपकी' if fem else 'आपका'} {name}, जिसके आख़िरी चार अंक {x.last4} हैं" + (", जो अभी इनएक्टिव है" if inactive else ""))
        elif lang == "hinglish":
            parts.append(f"{'aapki' if fem else 'aapka'} {name}, jiske last 4 digits {x.last4} hain" + (", jo abhi inactive hai" if inactive else ""))
        else:
            parts.append(f"your {name} ending {x.last4}" + (", which is inactive" if inactive else ""))
    if len(parts) == 1:
        return {"hi": f"आपके पास {parts[0]}", "hinglish": f"Aapke paas {parts[0]}"}.get(lang, f"There's {parts[0]}")
    if len(parts) == 2:
        return {"hi": f"एक है {parts[0]}, और {'दूसरी' if fem else 'दूसरा'} है {parts[1]}",
                "hinglish": f"Ek hai {parts[0]}, aur {'doosri' if fem else 'doosra'} hai {parts[1]}"}.get(
            lang, f"One is {parts[0]}, and the other is {parts[1]}")
    if lang == "en":
        ords = _ORD["en"]
        said = [f"the {ords[i]} is {p}" for i, p in enumerate(parts[:6])]
    else:
        ords = _ORD[lang][1 if fem else 0]
        said = [f"{ords[i]} {'है' if lang == 'hi' else 'hai'} {p}" for i, p in enumerate(parts[:6])]
    joined = ", ".join(said[:-1]) + {"hi": ", और ", "hinglish": ", aur "}.get(lang, ", and ") + said[-1]
    return _cap(joined)


def _accounts(customer: Customer, hint: str, lang: str, default_all: bool = False):
    """The account(s) the caller means, or a question back when it's unclear. `default_all`: no account named
    means every account (searches like "was my salary credited", "UPI spend this week")."""
    accounts = customer.accounts
    listing = _listing(accounts, lang, lambda a: a.type, "account")
    h = hint.strip().lower()
    recognised = (h in ("all", "both", "every", "first", "second", "third") or "salary" in h
                  or any(ch.isdigit() for ch in h) or any(k in h for k in _KNOWN_TYPES))
    if len(accounts) == 1 or h in ("all", "both", "every") or (default_all and not recognised):
        return accounts, None
    if not recognised:
        return None, PersonalAnswer([t("which", lang, n=len(accounts), list=listing)], "clarify")
    digits = "".join(ch for ch in h if ch.isdigit())
    if digits:
        picked = [a for a in accounts if a.number.endswith(digits)]
    elif "salary" in h:
        picked = [a for a in accounts if a.is_salary]
        if not picked:
            return None, PersonalAnswer([t("no_salary", lang), t("which", lang, n=len(accounts), list=listing)], "clarify")
    elif h in ("first", "second", "third"):
        i = ("first", "second", "third").index(h)
        picked = accounts[i:i + 1]
    else:
        picked = [a for a in accounts if a.type.lower() in h or h in a.type.lower()]
    if not picked:
        return None, PersonalAnswer([t("no_match", lang, list=listing)], "clarify")
    if len(picked) > 1 and h not in ("all", "both"):
        return None, PersonalAnswer([t("which", lang, n=len(picked), list=_listing(picked, lang, lambda a: a.type, "account"))], "clarify")
    return picked, None


def _pick(items: list, hint: str, lang: str, what: tuple, label, keys) -> tuple[list | None, PersonalAnswer | None]:
    """Pick FD(s) / loan(s) / card(s) by last digits or type. One item, or "all", needs no question."""
    h = hint.strip().lower()
    if not h or h in ("all", "both", "every"):
        return items, None
    digits = "".join(ch for ch in h if ch.isdigit())
    picked = [x for x in items if digits and x.last4.endswith(digits[-4:])] if digits else \
        [x for x in items if any(h in k.lower() or k.lower() in h for k in keys(x))]
    if not picked:
        noun = {"FDs": "FD", "loans": "loan", "credit cards": "credit card"}.get(what[0], "account")
        return None, PersonalAnswer([t("no_match", lang, list=_listing(items, lang, label, noun))], "clarify")
    return picked, None


def _status(s: str, lang: str) -> str:
    return T["status_words"].get(lang, T["status_words"]["en"]).get(s, s)


def _acct(a: Account, lang: str, asked: str = "") -> str:
    """The account in words. `asked`: the caller's own word for it; "salary" is said back ("your salary account, the
    Value account ending 4521")."""
    key = "acct_salary" if asked == "salary" and a.is_salary else "acct"
    return t(key, lang, type=a.type, l4=a.last4)


def _balance(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    accounts, ask = _accounts(c, q.account, lang, default_all=True)  # "my balance": every account, not "which one?"
    if ask:
        return ask
    if q.detail == "available_only":  # "how much can I use right now?"
        lines = []
        for a in accounts:
            avail = money(a.available if a.available is not None else a.balance)
            if a.status == "inactive":  # a balance is not permission to pay (PM review, 17 Nov)
                lines.append(_cap(t("avail_inactive", lang, avail=avail, acct=_acct(a, lang, q.account))))
            elif a.hold_amount:  # ledger, hold and available said apart, so "excluding the hold" is answered
                lines.append(_cap(t("avail_after_hold", lang, acct=_acct(a, lang, q.account), bal=money(a.balance), hold=money(a.hold_amount),
                                    reason=hold_reason(a.hold_reason, lang), avail=avail)))
            else:
                lines.append(_cap(t("avail_line", lang, avail=avail, acct=_acct(a, lang, q.account))))
        return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in accounts])
    lines = []
    for a in accounts:
        lines.append(_cap(t("balance", lang, acct=_acct(a, lang, q.account), amt=money(a.balance))))
        if a.hold_amount:  # a hold is mentioned only when there is one ("nothing on hold" is noise on a phone call)
            lines.append(t("balance_hold", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang), avail=money(a.available or 0)))
    return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in accounts])


def _account(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    """Which is my salary account, which debit card is linked, which variant I have, is it active, its rules."""
    if q.detail == "salary_account" and not q.account:
        salary = [a for a in c.accounts if a.is_salary]
        if not salary:
            return PersonalAnswer([t("no_salary", lang)], fields=["accounts:salary_flag"])
        lines = []
        for a in salary:
            employer = next((x.counterparty for x in a.transactions if x.description.lower().startswith("salary") and x.counterparty), "")
            lines.append(t("salary_is", lang, acct=_acct(a, lang), employer=f" from {employer}" if employer and lang != "hi" else
                           (f" ({employer})" if employer else "")))
        return PersonalAnswer(lines, fields=["accounts:salary_flag"])
    if q.detail == "minimum_balance" or q.detail == "balance_maintained":
        return _charges(c, q, lang, today)
    accounts, ask = _accounts(c, q.account, lang)
    if ask:
        return ask
    lines, fields = [], []
    for a in accounts:
        if q.detail == "linked_debit_card":
            card = a.debit_card
            lines.append(t("linked_dc", lang, Acct=_cap(_acct(a, lang)), acct=_acct(a, lang), variant=card.variant, l4=card.last4,
                           status=_status(card.status, lang)) if card else t("no_dc_on", lang, acct=_acct(a, lang)))
            fields.append(f"account:{a.last4}:debit_card")
        elif q.detail == "benefits":
            lines.append(t("variant", lang, type=a.type, l4=a.last4))
            fields.append(f"account:{a.last4}:variant")
            return PersonalAnswer(lines, fields=fields, needs_kb=f"What are the features and benefits of the AU {a.type} Savings Account?")
        elif q.detail == "status":
            lines.append(_cap(t("acct_status", lang, Acct=_cap(_acct(a, lang)), type=a.type, l4=a.last4, status=_status(a.status, lang))))
            if a.status != "active":
                lines.append(t("inactive_note", lang))
            fields.append(f"account:{a.last4}:status")
        else:
            lines.append(t("variant", lang, type=a.type, l4=a.last4))
            fields.append(f"account:{a.last4}:variant")
    return PersonalAnswer(lines, fields=fields)


def _charges(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    """Minimum balance needed, whether it's met this month, and charges applied (with the reason)."""
    accounts, ask = _accounts(c, q.account, lang)
    if ask:
        return ask
    lines, fields = [], []
    branch_note = {"urban": (" at an urban branch", " (शहरी ब्रांच)", " (urban branch)"),
                   "semi_urban": (" at a semi-urban branch", " (अर्ध-शहरी ब्रांच)", " (semi-urban branch)"),
                   "rural": (" at a rural branch", " (ग्रामीण ब्रांच)", " (rural branch)")}
    for a in accounts:
        fields.append(f"account:{a.last4}:balance_rules")
        if q.detail in ("minimum_balance", "balance_maintained", ""):
            if not a.amb_required:
                lines.append(t("amb_none", lang, acct=_acct(a, lang)))
            else:
                note = branch_note.get(a.branch_type, ("", "", ""))[_L.get(lang, 0)] if a.type == "Value" else ""
                lines.append(t("amb_rule", lang, Acct=_cap(_acct(a, lang)), acct=_acct(a, lang), req=money(a.amb_required), branch=note))
                if a.amb_this_month is not None and q.detail != "charges":
                    gap = a.amb_required - a.amb_this_month
                    lines.append(t("amb_now_short", lang, mtd=money(a.amb_this_month), gap=money(gap)) if gap > 0
                                 else t("amb_now_ok", lang, mtd=money(a.amb_this_month)))
        if q.detail in ("charges", "balance_maintained") or (q.detail == "" and a.charges):
            recent = [x for x in a.charges if (today - x.date).days <= 90]
            if not recent and q.detail == "charges":
                lines.append(t("no_charges", lang, acct=_acct(a, lang)))
            for ch in recent:
                name = CHARGE_NAMES.get(ch.type, (ch.type,) * 3)[_L.get(lang, 0)]
                lines.append(t("charge_line", lang, amt=money(ch.amount), date=day(ch.date, lang), what=name,
                               rev=" (since reversed)" if ch.reversed and lang != "hi" else (" (वापस हो चुका)" if ch.reversed else "")))
                if ch.type == "amb_shortfall" and a.amb_last_month is not None:
                    lines.append(t("amb_charge_why", lang, last=money(a.amb_last_month), req=money(a.amb_required)))
            if recent:
                kb = CHARGE_KB.get(recent[0].type, "").format(type=a.type) if q.detail == "charges" else ""
                if kb:  # bank data says what was charged and why; the approved charges document says how it is worked out
                    return PersonalAnswer(lines, fields=fields, offer={"ticket": "charge_review"}, needs_kb=kb, kb_optional=True,
                                          after=[t("charge_dispute", lang)])
                lines.append(t("charge_dispute", lang))
                return PersonalAnswer(lines, fields=fields, offer={"ticket": "charge_review"})
    return PersonalAnswer(lines, fields=fields)


def note_text(x: Transaction, lang: str) -> str:
    """The bank's note on a transaction, in the caller's language ('' if none)."""
    if not x.note:
        return ""
    try:
        note = json.loads(x.note)
    except ValueError:
        return ""
    key = f"note_{note.get('key')}"
    if key not in T:
        return ""
    values = {k: (money(v) if isinstance(v, (int, float)) else v) for k, v in note.items() if k != "key"}
    if "due" in values:
        values["due"] = day(date.fromisoformat(values["due"]), lang)
    return _cap(t(key, lang, amt=money(x.amount), **values))


def _matches(x: Transaction, q: PersonalQuery, keyword: str) -> bool:
    if q.amount and not (abs(x.amount - q.amount) <= max(q.amount * 0.1, 1)):
        return False
    if keyword:
        hay = f"{x.description} {x.counterparty} {x.channel}".lower()
        words = {"restaurant": ("restaurant", "barbeque", "dhaba", "cafe", "zomato", "swiggy"), "atm": ("atm",),
                 "salary": ("salary",), "refund": ("refund",), "upi": ("upi",), "emi": ("emi", "nach")}.get(keyword, (keyword,))
        return any(w in hay for w in words)
    return True


def _txn_line(x: Transaction, lang: str, acct4: str = "", today: date | None = None) -> str:
    """One transaction in words. The ending of the status table decides the verb: "paid" only after success."""
    tails = T["txn_tail"].get(lang, T["txn_tail"]["en"])
    desc = " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in x.description.split())  # "UPI transfer"
    desc = re.sub(r"\bPOS\b", "card", desc)  # "POS purchase" is statement jargon: a caller says "card purchase"
    returned = x.status == "failed" and bool(re.search(r"\breturned\b", desc))
    desc = re.sub(r"\s+(returned|failed|reversed)$", "", desc)  # "EMI auto-debit returned": the status is said once, below
    status = "returned" if returned else x.status
    tail = tails.get(status, tails["unknown"])[x.kind if x.kind in ("Debit", "Credit") else "Debit"]
    debit = x.kind != "Credit"
    at = x.channel in ("POS", "ECOM", "ATM")
    when, amt, who = _when(x.date, x.time, lang, today), money(x.amount), x.counterparty
    if lang == "hi":
        acct = f"आपके {acct4} वाले अकाउंट {'से' if debit else 'में'} " if acct4 else ""
        party = f"{who} {'पर' if at else 'को' if debit else 'से'} " if who else ""
        return f"{when} {acct}{party}₹{amt} का {desc} {'हुआ' if debit else 'आया'}{tail}।"
    if lang == "hinglish":
        acct = f"aapke account ending {acct4} {'se' if debit else 'mein'} " if acct4 else ""
        party = f"{who} {'par' if at else 'ko' if debit else 'se'} " if who else ""
        return f"{when} {acct}{party}₹{amt} ka {desc} {'hua' if debit else 'aaya'}{tail}."
    party = f" {'at' if at else 'to' if debit else 'from'} {who}" if who else ""
    acct = f" {'from' if debit else 'into'} your account ending {acct4}" if acct4 else ""
    first = desc.split()[0] if desc else ""
    vowel = first[:1].upper() in ("AEFHILMNORSX" if first.isupper() and len(first) > 1 else "AEIO")
    return f"{when}, there was {'an' if vowel else 'a'} {desc} of ₹{amt}{party}{acct}{tail}."


# ───────── searches and totals said as answers (PM review of call d48a5afe, 17 Nov) ─────────
# "Did Rajesh send money today?" is answered "No, I don't see any money from Rajesh today", not the generic "no such
# transaction on account (ending 9001)"; "how much did I spend through UPI this week?" gets the total with what it covers.
YES = {"en": "Yes.", "hi": "जी हाँ।", "hinglish": "Ji haan."}
_WHEN = {"today": ("today", "आज", "aaj"), "yesterday": ("yesterday", "कल", "kal"), "this_week": ("this week", "इस हफ़्ते", "is hafte"),
         "last_7_days": ("in the last 7 days", "पिछले 7 दिनों में", "pichhle 7 din mein"),
         "this_month": ("this month", "इस महीने", "is mahine"),
         "last_30_days": ("in the last 30 days", "पिछले 30 दिनों में", "pichhle 30 din mein"),
         "last_month": ("last month", "पिछले महीने", "pichhle mahine")}
_CHANNELS = {"upi": "UPI", "neft": "NEFT", "imps": "IMPS", "rtgs": "RTGS", "atm": "ATM", "nach": "NACH"}
# keywords that are a status or a kind of money, never a person ("No, I don't see any payment to Deducted" - 17 Nov)
_NOT_NAMES = {"deducted", "debited", "credited", "cut", "payment", "payments", "money", "transfer", "transaction", "transactions",
              "charged", "charge", "refund", "paid", "received", "sent", "spent", "cash", "bill", "hold", "pending", "failed"}
_TOPIC_WORDS = {"restaurant", "salary", "refund", "emi", "card", "shopping", "food", "fuel"} | set(_CHANNELS)


def _when_said(q: PersonalQuery, lang: str) -> str:
    return _WHEN[q.period][_L.get(lang, 0)] if q.period in _WHEN else ""


def _person(q: PersonalQuery) -> str:
    """The name the caller searched for ("Rajesh"), when the keyword is a person rather than a channel or a topic."""
    k = q.keyword.strip()
    if not k or k.lower() in _TOPIC_WORDS | _NOT_NAMES or any(ch.isdigit() for ch in k):
        return ""
    return k if k[:1].isupper() else k.title()


def _is_check(q: PersonalQuery) -> bool:
    """A yes/no check of money coming in ("was my salary credited this month?", "did Rajesh pay me today?")."""
    return not q.count and not q.total and (q.keyword.strip().lower() == "salary" or (q.direction == "credit" and bool(q.keyword)))


def _spent(q: PersonalQuery, lang: str, amt: str, k: int, l4: str) -> str:
    ch, when = _CHANNELS.get(q.keyword.strip().lower(), ""), _when_said(q, lang)
    if lang == "hi":
        text = f"{when} आपने{' ' + ch + ' से' if ch else ''} कुल ₹{amt} खर्च किए, {k} पेमेंट्स में" + (f", अकाउंट {l4} से" if l4 else "")
        return text.strip() + "।"
    if lang == "hinglish":
        text = f"{when} aapne{' ' + ch + ' se' if ch else ''} total ₹{amt} kharch kiye, {k} payments mein" + (f", account {l4} se" if l4 else "")
        return _cap(text.strip()) + "."
    return (f"You spent ₹{amt}{' through ' + ch if ch else ''}{' ' + when if when else ''}, across {k} payment"
            f"{'' if k == 1 else 's'}" + (f" from your account ending {l4}" if l4 else "") + ".")


def _nothing_found(q: PersonalQuery, lang: str, accounts: list, filtered: bool) -> str:
    """A search that found nothing says what was searched. (A bank system that could not be reached is a different
    reply: that raises BankError and the caller hears that the information can't be fetched right now.)"""
    if not filtered:  # no filter to match: "no transactions", not "no matching transaction"
        return t("txn_empty", lang, l4=" / ".join(a.last4 for a in accounts))
    i, name, when = _L.get(lang, 0), _person(q), _when_said(q, lang)
    ch, credit = _CHANNELS.get(q.keyword.strip().lower(), ""), q.direction == "credit"
    if name:
        what = ((f"any money from {name}", f"{name} से आया कोई पैसा", f"{name} se aaya koi paisa") if credit or q.direction == "any"
                else (f"any payment to {name}", f"{name} को गया कोई पेमेंट", f"{name} ko gaya koi payment"))[i]
    elif q.keyword.strip().lower() == "salary":
        what = ("a salary credit", "सैलरी क्रेडिट", "salary credit")[i]
    elif ch:
        what = ((f"any money received through {ch}", f"{ch} से आया कोई पैसा", f"{ch} se aaya koi paisa") if credit
                else (f"any {ch} payments", f"{ch} से कोई पेमेंट", f"{ch} se koi payment"))[i]
    else:
        what = ("a matching transaction", "ऐसा कोई ट्रांज़ैक्शन", "aisa koi transaction")[i]
    every = len(accounts) > 1  # searched every account: say so, so it doesn't sound like one was skipped
    named = accounts[0].last4 if not every and any(ch.isdigit() for ch in q.account) else ""  # "...credited to 4512?"
    if lang == "hi":
        where = 'आपके किसी भी अकाउंट में ' if every else f'आपके अकाउंट {named} में ' if named else ''
        return f"नहीं, {when + ' ' if when else ''}{where}{what} नहीं दिख रहा।"
    if lang == "hinglish":
        where = 'aapke kisi bhi account mein ' if every else f'aapke account ending {named} mein ' if named else ''
        return f"Nahi, {when + ' ' if when else ''}{where}{what} nahi dikh raha."
    where = ' on any of your accounts' if every else f' on your account ending {named}' if named else ''
    return f"No, I don't see {what}{where}{' ' + when if when else ''}."


def _numbered(lines: list[str], lang: str, last: bool = True) -> list[str]:
    """Several transactions read out one after another: "First, ... Second, ... Finally, ...", so the caller hears
    where each one starts and how many are left (PM review of call 9d31b6fd, 17 Nov). `last`: the list ends here."""
    if len(lines) < 2:
        return [_cap(x) for x in lines]
    words = _ORDINALS.get(lang, _ORDINALS["en"])
    out = []
    for i, line in enumerate(lines):
        word = words[-1] if last and i == len(lines) - 1 else words[i] if i < len(words) - 1 else ""
        out.append(f"{word}, {line[:1].lower() + line[1:] if lang != 'hi' else line}" if word else _cap(line))
    return out


_ORDINALS = {"en": ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Finally"),
             "hi": ("पहला", "दूसरा", "तीसरा", "चौथा", "पाँचवाँ", "छठा", "सातवाँ", "आठवाँ", "नौवाँ", "और आख़िरी"),
             "hinglish": ("Pehla", "Doosra", "Teesra", "Chautha", "Paanchva", "Chhatha", "Saatva", "Aathva", "Nauva", "Aur aakhri")}


def _each_account(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    k, sentences, fields = q.count or 5, [], []
    per = [(a, sorted((x for x in a.transactions if x.date <= today), key=lambda x: (x.date, x.time), reverse=True)[:k])
           for a in c.accounts]
    if len(per) > 1:  # say what is coming before the details: how many accounts, and where it starts
        sentences.append(t("txn_each_overview", lang, n=len(per), l4=per[0][0].last4))
    for a, items in per:
        fields.append(f"account:{a.last4}:transactions")
        if not items:
            sentences.append(t("txn_empty", lang, l4=a.last4))
            continue
        key = "txn_each_intro" if len(items) == k else "txn_each_only"
        sentences.append(t(key, lang, l4=a.last4, k=len(items), s="" if len(items) == 1 else "s"))
        sentences += _numbered([_txn_line(x, lang, today=today) for x in items], lang)
    return PersonalAnswer(sentences, fields=fields)


def _transactions(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if q.detail == "duplicate":
        return _duplicates(c, lang, today)
    if q.detail == "hold_check":  # "has that 2,500 been deducted?": a hold of that amount is said as a hold
        holds = [a for a in c.accounts if a.hold_amount]
        if not q.amount and len(holds) == 1:  # "what is this payment for?" right after the balance
            a = holds[0]
            return PersonalAnswer([t("hold_what", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang),
                                     acct=_acct(a, lang), avail=money(a.available or 0))], fields=[f"account:{a.last4}:balance"])
        held = next((a for a in holds if q.amount and abs(a.hold_amount - q.amount) <= 1), None)
        if held:
            return PersonalAnswer([t("hold_not_debit", lang, hold=money(held.hold_amount), reason=hold_reason(held.hold_reason, lang),
                                     avail=money(held.available or 0))], fields=[f"account:{held.last4}:balance"])
    if q.detail == "hold_why":  # "why is 2,500 rupees blocked?": the hold of that amount, or the only one
        holds = [a for a in c.accounts if a.hold_amount]
        match = [a for a in holds if q.amount and abs(a.hold_amount - q.amount) <= 1] or (holds if len(holds) == 1 else [])
        if len(match) == 1:
            a = match[0]
            return PersonalAnswer([t("hold_what", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang),
                                     acct=_acct(a, lang), avail=money(a.available or 0))], fields=[f"account:{a.last4}:balance"])
        if not holds:
            return PersonalAnswer([t("no_hold_any", lang)], fields=[f"account:{a.last4}:balance" for a in c.accounts])
    if q.detail == "total_scope":
        return PersonalAnswer([t("total_scope", lang)])
    if q.account == "each":  # "five transactions in each account": per account, each newest first
        return _each_account(c, q, lang, today)
    if q.detail == "pending_check":  # "did the pending payment get reversed?": its status now, from the record
        return _pending_check(c, lang, today, q.item == "hold")
    filtered = bool(q.period or q.direction != "any" or q.min_amount or q.amount or q.keyword or q.total or q.detail == "failed_only")
    accounts, ask = _accounts(c, q.account, lang, default_all=filtered)
    if ask:
        return ask
    fields, per_account = [], []
    span = period_range(q.period, today)
    keyword = q.keyword.strip().lower()
    for a in accounts:
        items = [x for x in a.transactions if x.date <= today]
        if span:
            items = [x for x in items if span[0] <= x.date <= span[1]]
        if q.direction in ("debit", "credit"):
            items = [x for x in items if x.kind.lower() == q.direction]
        if q.min_amount:
            items = [x for x in items if x.amount >= q.min_amount]
        items = [x for x in items if _matches(x, q, keyword)]
        if q.detail == "failed_only":  # "show failed transactions": attempts that did not go through
            items = [x for x in items if x.status == "failed"]
        if q.total:
            items = [x for x in items if x.kind == "Debit" and x.status == "success"]
        fields.append(f"account:{a.last4}:transactions")
        per_account.append((a, items))
    if q.total:  # "how much did I spend through UPI this week?": the total, said with what was added up
        sentences = [_spent(q, lang, money(sum(x.amount for x in items)), len(items), a.last4 if len(accounts) > 1 else "")
                     for a, items in per_account if items]
        return PersonalAnswer(sentences or [_nothing_found(q, lang, accounts, filtered)], fields=fields)
    merged = sorted(((a, x) for a, items in per_account for x in items), key=lambda p: (p[1].date, p[1].time), reverse=True)
    if not merged:
        return PersonalAnswer([_nothing_found(q, lang, accounts, filtered)], fields=fields)
    several = len(accounts) > 1  # name each line's account when more than one is in play
    # A filtered list ("my UPI payments this week") counts every match, not the first three: call 5884ab26 heard "₹3,940 in
    # 4 payments" and then "I found 3". Three are read out, then "Want the other 1?" (below), so count and list agree.
    shown = merged[: max(1, min(q.count or (10 if filtered else 5), 10))]
    lines = [_txn_line(x, lang, a.last4 if several else "", today) for a, x in shown]
    yes = f"{YES.get(lang, YES['en'])} " if _is_check(q) else ""  # "did my salary come?": the answer starts with yes
    notes = {id(x): note_text(x, lang) for _, x in shown if x.note and (filtered or len(shown) == 1)}
    short = bool(q.count) and len(shown) < q.count and not filtered  # asked for 5, only 1 exists
    inactive = [t("acct_inactive_note", lang)] if short and not several and accounts[0].status == "inactive" else []
    if len(shown) == 1:  # "here are the last 1" reads badly: just say it
        note = [notes[id(shown[0][1])]] if notes.get(id(shown[0][1])) else []
        if short and not several:
            return PersonalAnswer([t("txn_only_one", lang, l4=accounts[0].last4), lines[0]] + note + inactive, fields=fields)
        # A caller with more than one account hears which account this was (not only when several are in play)
        line = lines[0] if several or len(c.accounts) < 2 else _txn_line(shown[0][1], lang, shown[0][0].last4, today)
        return PersonalAnswer([yes + _cap(line)] + note, fields=fields)
    key = ("txn_merged_found" if filtered else "txn_merged_intro") if several else ("txn_found" if filtered else "txn_intro")
    if short:
        key = "txn_merged_only" if several else "txn_only"
    if key == "txn_intro" and q.account == "salary":  # the caller said "salary account": say it back
        key = "txn_intro_salary"
    sentences = [yes + t(key, lang, k=len(shown), l4=accounts[0].last4)]
    # Asked for a number ("my last five"): all of them, up to five at once (PM review, 17 Nov: "if the user asks for five,
    # provide five"). Not asked: three, then "Want the other 2?" - a long list is hard to follow on a call.
    chunk = 5 if q.count else 3
    out, rest = [], []
    said = _numbered(lines[:chunk], lang, last=len(lines) <= chunk) if len(shown) > 1 else lines[:chunk]
    for i, ((_, x), line) in enumerate(zip(shown, lines, strict=True)):
        if i < chunk:
            out.append(said[i])
            if notes.get(id(x)):
                out.append(notes[id(x)])
        else:
            rest.append(line)
    sentences += out
    if rest:  # the rest on request ("go on")
        sentences.append(t("more_offer", lang, k=len(rest)))
        return PersonalAnswer(sentences, fields=fields, offer={"more": rest})
    return PersonalAnswer(sentences + inactive, fields=fields)


def _pending_check(c: Customer, lang: str, today: date, hold_only: bool = False) -> PersonalAnswer:
    """What happened to a pending payment, from the bank's record: a hold still on the account, a transaction still
    processing or reversed. `hold_only`: the call was just about a hold, so "the pending payment" is that hold (PM review,
    17 Nov). A reversal is said only when the record says so, never guessed from a hold."""
    held = [a for a in c.accounts if a.hold_amount]
    lines = [t("hold_pending", lang, hold=money(a.hold_amount), reason=hold_reason(a.hold_reason, lang), acct=_acct(a, lang)) for a in held]
    if hold_only and lines:
        return PersonalAnswer(lines, fields=[f"account:{a.last4}:balance" for a in held])
    open_ = [(a, x) for a in c.accounts for x in a.transactions if x.date <= today and x.status in ("pending", "reversed")]
    if not open_ and not lines:
        return PersonalAnswer([t("pending_none", lang)], fields=[f"account:{a.last4}:transactions" for a in c.accounts])
    for a, x in sorted(open_, key=lambda p: (p[1].date, p[1].time), reverse=True)[:3]:
        who = x.counterparty or x.description
        key = "pending_still" if x.status == "pending" else "pending_reversed"
        lines.append(t(key, lang, amt=money(x.amount), who=who, date=day(x.date, lang)))
    return PersonalAnswer(lines, fields=sorted({f"account:{a.last4}:transactions" for a, _ in open_} | {f"account:{a.last4}:balance" for a in held}))


def _duplicates(c: Customer, lang: str, today: date) -> PersonalAnswer:
    for a in c.accounts:
        debits = [x for x in a.transactions if x.kind == "Debit" and x.status == "success" and (today - x.date).days <= 30]
        for i, x in enumerate(debits):
            for y in debits[i + 1:]:
                if x.amount == y.amount and x.counterparty == y.counterparty and x.date == y.date and x.counterparty:
                    return PersonalAnswer([t("dup_found", lang, amt=money(x.amount), who=x.counterparty, date=day(x.date, lang)),
                                           t("dispute_offer", lang)], fields=[f"account:{a.last4}:transactions"],
                                          offer={"ticket": "double_charge"})
    return PersonalAnswer([t("dup_none", lang)], fields=["accounts:transactions"])


def _fd(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    active = [f for f in c.fds if f.status == "active"]
    if not c.fds:
        return PersonalAnswer([t("no_fd", lang)])
    future, past = T["fd_verb"].get(lang, T["fd_verb"]["en"])
    pool = active or c.fds
    if q.detail == "first_maturing":
        f = min(pool, key=lambda x: x.maturity_date)
        return PersonalAnswer([t("fd_first", lang, l4=f.last4, date=day(f.maturity_date, lang)),
                               t("fd_amount_then", lang, mat=money(f.maturity_amount))],
                              fields=[f"fd:{f.last4}:maturity"])
    picked, ask = _pick(pool, q.item, lang, ("FDs",), lambda f: "", lambda f: (f.number,))
    if ask:
        return ask
    if q.detail in ("maturity_amount", "rate", "auto_renew", "premature_value") and len(picked) > 1:
        soonest = min(picked, key=lambda x: x.maturity_date)
        if (soonest.maturity_date - today).days <= 45:  # "my FD maturing next month" -> the one maturing soon
            picked = [soonest]
        else:
            return PersonalAnswer([t("which_item", lang, n=len(picked), what=plural("FD", lang), list=_listing(picked, lang, lambda f: "", "FD"))], "clarify")
    lines, fields = [], []
    if len(picked) > 1:
        lines.append(t("fd_count", lang, n=len(picked)))
    for f in picked:
        fields.append(f"fd:{f.last4}")
        if q.detail == "rate":
            lines.append(t("fd_rate", lang, l4=f.last4, rate=f"{f.rate:g}"))
        elif q.detail == "auto_renew":
            lines.append(t("fd_renew" if f.auto_renew else "fd_no_renew", lang, l4=f.last4))
        elif q.detail == "maturity_amount":
            lines.append(t("fd_amount", lang, mat=money(f.maturity_amount), l4=f.last4, date=day(f.maturity_date, lang)))
            if f.payout == "quarterly":
                lines.append(t("fd_payout", lang, p=money(f.principal)))
        elif q.detail == "premature_value":
            days = (today - f.opened_on).days
            value = f.principal * (1 + max(f.rate - 1, 0) / 100 * days / 365) if f.payout == "cumulative" else f.principal
            lines += [t("fd_break", lang, l4=f.last4, amt=money(round(value, -1))), t("fd_break_how", lang)]
        else:
            matured = f.maturity_date < today
            lines.append(t("fd", lang, l4=f.last4, p=money(f.principal), rate=f"{f.rate:g}", verb=past if matured else future,
                           date=day(f.maturity_date, lang), mat=money(f.maturity_amount)))
    return PersonalAnswer(lines, fields=fields)


def foreclosure_pct(loan: Loan) -> float | None:
    """Foreclosure charge (% of the outstanding, before GST) from published rules, or None when there isn't one.
    Floating-rate loans to individuals: nil (RBI). Personal loans (Assets KB): not allowed before 6 months,
    5% between 6 and 12 months, 3% after 12 months."""
    if loan.rate_type == "floating" and loan.type != "business":
        return 0
    if loan.type == "personal":
        return None if loan.emis_paid < 6 else 5 if loan.emis_paid <= 12 else 3
    return None


def _loan_name(loan: Loan, lang: str) -> str:
    return VERIFY_LOAN.get(loan.type, (loan.type,) * 3)[_L.get(lang, 0)]


def _loan_ref(c: Customer, loan: Loan, lang: str) -> dict:
    """{ending} / {pre} for the EMI lines: the loan's last four digits, always (as banks say "your loan ending 0107"),
    so the caller always knows which loan an EMI belongs to (owner, 17 Nov)."""
    return {"ending": f" ending {loan.last4}" if lang != "hi" else "", "pre": f"{loan.last4} वाले " if lang == "hi" else ""}


def _join(names: list[str], lang: str) -> str:
    """["home loan ending 0101", "personal loan ending 0102"] -> "... and ..." ("होम लोन 0101 और पर्सनल लोन 0102")."""
    items = names  # each already says "<type> loan" (and its digits when there are several)
    word = {"hi": " और ", "hinglish": " aur "}.get(lang, " and ")
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + word + items[-1]


_ALL_LOANS = re.compile(r"\b(all|both|every|across|combined|together)\b.{0,25}\bloans?\b|\bloans?\b.{0,15}\b(all|both|combined|together)\b|"
                        r"\b(sab|sabhi|saare|sare|dono)\b.{0,10}\bloans?\b|सभी लोन|सारे लोन|दोनों लोन|सब लोन", re.I)


def loan_in_focus(q: PersonalQuery, raw: str, focus: str) -> PersonalQuery:
    """Which loan a loan question is about. "Across all my loans" is every loan; a question that names none keeps the
    loan the call is already about ("how much have I paid so far?" after the home loan EMI). PM review, 17 Nov: it
    answered for both loans. A count of loans is always about all of them."""
    if q.detail in ("loan_count", "loan_count_left"):
        return q
    if _ALL_LOANS.search(raw):
        return replace(q, item="all")
    if not q.item.strip() and focus:
        return replace(q, item=focus)
    return q


def _loan(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.loans:
        return PersonalAnswer([t("no_loan", lang)])
    if q.detail in ("loan_count", "loan_count_left"):  # "how many loans do I have?": all of them, not the one in focus
        several = len(c.loans) > 1
        names = [f"{_loan_name(x, lang)} {'लोन' if lang == 'hi' else 'loan'}"
                 + ((f" {x.last4}" if lang == "hi" else f" ending {x.last4}") if several else "") for x in c.loans]
        lines = [t("loan_count_one", lang, type=_loan_name(c.loans[0], lang), l4=c.loans[0].last4) if len(names) == 1 else
                 t("loan_count", lang, n=len(names), list=_join(names, lang))]
        if q.detail == "loan_count_left" and len(c.loans) == 1:  # "kitne loan baaki hain": may mean the EMIs left too
            lines.append(t("emi_count", lang, paid=c.loans[0].emis_paid, left=c.loans[0].emis_left))
        return PersonalAnswer(lines, fields=[f"loan:{x.last4}" for x in c.loans])
    if q.detail in ("emis_count", "repaid_amount") and not q.item.strip() and len(c.loans) > 1:
        # "how many EMIs have I paid?" with nothing in the call saying which loan: asked, never summed across loans
        return PersonalAnswer([t("which_item", lang, n=len(c.loans), what=plural("loan", lang),
                                 list=_listing(c.loans, lang, lambda x: _loan_name(x, lang), "loan"))], "clarify")
    picked, ask = _pick(c.loans, q.item, lang, ("loans",), lambda x: x.type, lambda x: (x.type, x.number))
    if ask:
        return ask
    lines, fields = [], []
    for loan in picked:
        name, ref = _loan_name(loan, lang), _loan_ref(c, loan, lang)
        fields.append(f"loan:{loan.last4}")
        last = loan.emis[0] if loan.emis else None
        if q.detail == "emi_last_paid":  # "when did money last leave my account for my EMI?": the last EMI actually taken
            paid = next((e for e in loan.emis if e.status == "paid"), None)
            if paid and paid.paid_on:
                lines.append(t("emi_last_debit", lang, type=name, **ref, emi=money(paid.amount), date=day(paid.paid_on, lang)))
            elif paid:  # the bank gave no debit date: the due date is said as a due date
                lines.append(t("emi_paid_due_only", lang, type=name, **ref, emi=money(paid.amount), date=day(paid.due_date, lang)))
            else:
                lines.append(t("emi_no_history", lang, type=name))
            if last and last.status == "bounced":  # a returned EMI is not money that left the account
                lines.append(t("emi_last_failed", lang, emi=money(last.amount), date=day(last.due_date, lang)))
            continue
        if q.detail == "emi_recovery" and last:  # "my EMI bounced, has it been paid now?": yes or no first
            if last.status == "bounced":
                reason = t(f"reason_{last.reason}", lang) if f"reason_{last.reason}" in T else (last.reason or "")
                lines.append(t("emi_not_recovered", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang), reason=reason))
                if last.charge:
                    lines.append(t("emi_bounce_charge", lang, amt=money(last.charge)))
            else:
                lines.append(YES.get(lang, YES["en"]) + " " + t("emi_paid_of", lang, type=name, **ref, emi=money(last.amount),
                                                                  date=day(last.due_date, lang)))
            continue
        if q.detail == "repaid_amount":  # "how much have I repaid?": an amount, from the loan's own figures
            lines.append(t("repaid_of", lang, type=name, **ref, paid=loan.emis_paid, repaid=money(max(loan.sanctioned - loan.outstanding, 0)),
                           total=money(loan.sanctioned), out=money(loan.outstanding)))
            continue
        if q.detail in ("emi_status", "bounce_charges") and last:
            if last.status == "bounced":
                reason = t(f"reason_{last.reason}", lang) if f"reason_{last.reason}" in T else (last.reason or "")
                lines.append(t("emi_bounced", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang), reason=reason))
                if last.charge:
                    lines.append(t("emi_bounce_charge", lang, amt=money(last.charge)))
                note = next((note_text(x, lang) for a in c.accounts for x in a.transactions
                             if x.channel == "NACH" and x.status == "failed" and x.date == last.due_date and x.note), "")
                if note:
                    lines.append(t("emi_bounce_time", lang, note=note if note[:2].isupper() else note[:1].lower() + note[1:]))
            elif q.detail == "bounce_charges":
                lines.append(t("no_bounce", lang, type=name))
            elif last.paid_on:  # the day it was debited, from the bank's record; said once when it is the due date
                key = "emi_paid_day" if last.paid_on == last.due_date else "emi_paid_on"
                lines.append(t(key, lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang),
                               paid=day(last.paid_on, lang)))
            else:  # two loans: each payment says which loan it was
                lines.append(t("emi_paid_of", lang, type=name, **ref, emi=money(last.amount), date=day(last.due_date, lang)))
        elif q.detail in ("emi_status", "bounce_charges"):  # no past EMI on record: say so, never the next EMI instead
            lines.append(t("no_bounce" if q.detail == "bounce_charges" else "emi_no_history", lang, type=name))
        elif q.detail == "next_emi":  # each EMI says which loan it is ("Your gold loan EMI of ₹5,400 is due on...")
            lines.append(t("emi_next_of", lang, type=name, **ref, emi=money(loan.emi), date=day(loan.next_due, lang)))
        elif q.detail == "emis_count":
            lines.append(t("emi_count_of", lang, type=name, **ref, paid=loan.emis_paid, left=loan.emis_left))
        elif q.detail == "emi_increase":
            if loan.rate_changes:
                r = loan.rate_changes[0]
                lines.append(t("emi_up", lang, old=money(r.old_emi), new=money(r.new_emi), type=name, r0=f"{r.old_rate:g}",
                               r1=f"{r.new_rate:g}", date=day(r.date, lang)))
            else:
                lines.append(t("emi_same", lang, type=name, emi=money(loan.emi)))
        elif q.detail == "foreclosure":
            pct = foreclosure_pct(loan)
            if pct is None:  # no published rule for this loan type, or not yet allowed: the loan team quotes it
                lines.append(t("loan", lang, type=name, l4=loan.last4, out=money(loan.outstanding)))
                lines.append(t("foreclose_team", lang))
                return PersonalAnswer(lines, fields=fields, offer={"handoff": "loans"})
            charge = loan.outstanding * pct / 100 * 1.18
            note = "" if not pct else (f" plus about ₹{money(round(charge))} foreclosure charge" if lang == "en" else
                                       f" + लगभग ₹{money(round(charge))} फोरक्लोज़र चार्ज" if lang == "hi" else
                                       f" plus lagbhag ₹{money(round(charge))} foreclosure charge")
            lines.append(t("foreclose", lang, type=name, amt=money(round(loan.outstanding + charge)), out=money(loan.outstanding), charge=note))
        else:
            lines.append(t("loan", lang, type=name, l4=loan.last4, out=money(loan.outstanding)))
            if not q.detail and len(picked) == 1:  # an overview only: "outstanding" is answered alone, not with the EMI again
                lines.append(t("emi_next", lang, emi=money(loan.emi), date=day(loan.next_due, lang)))
    if q.detail == "emis_count" and len(picked) > 1:  # "across all my loans": the total first, then what each loan adds
        on = {"en": "{n} on your {type} loan ending {l4}", "hi": "{l4} वाले {type} लोन पर {n}", "hinglish": "{type} loan ending {l4} par {n}"}
        parts = [on.get(lang, on["en"]).format(n=x.emis_paid, type=_loan_name(x, lang), l4=x.last4) for x in picked]
        lines = [t("emis_across", lang, n=len(picked), total=sum(x.emis_paid for x in picked), parts=_join(parts, lang))]
    if q.detail == "repaid_amount" and len(picked) > 1:  # the total repaid first, then each loan's own figures
        lines = [t("repaid_across", lang, n=len(picked), amt=money(sum(max(x.sanctioned - x.outstanding, 0) for x in picked)))] + lines
    return PersonalAnswer(lines, fields=fields)


def _credit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.credit_cards:
        return PersonalAnswer([t("no_cc", lang)])
    if q.detail == "status" and not q.item:  # "do I have any credit cards?": the cards, not a bill (call de568948)
        return PersonalAnswer([t("cards_have", lang, n=len(c.credit_cards), what=plural("credit card", lang),
                                 list=_listing(c.credit_cards, lang, lambda x: x.variant, "credit card"))],
                              fields=[f"credit_card:{k.last4}" for k in c.credit_cards])
    picked, ask = _pick(c.credit_cards, q.item, lang, ("credit cards",), lambda x: x.variant, lambda x: (x.variant, x.last4))
    if ask:
        return ask
    lines, fields = [], []
    for k in picked:
        fields.append(f"credit_card:{k.last4}")
        if q.detail == "available_limit":
            lines.append(t("cc_limit", lang, variant=k.variant, l4=k.last4, avail=money(k.available), limit=money(k.limit)))
        elif q.detail == "payment_status":
            if k.last_payment_on:
                key = "cc_pay_processing" if k.last_payment_status == "processing" else "cc_pay_posted"
                lines.append(t(key, lang, amt=money(k.last_payment_amount or 0), date=day(k.last_payment_on, lang)))
        elif q.detail == "statement":
            yes, no = T["paid_words"].get(lang, T["paid_words"]["en"])
            lines.append(t("cc_statement", lang, l4=k.last4, date=day(k.statement_date, lang), paid=yes if k.previous_bill_paid else no))
        elif q.detail == "lounge":
            if k.lounge_per_quarter:
                lines.append(t("cc_lounge", lang, variant=k.variant, total=k.lounge_per_quarter, used=k.lounge_used_quarter,
                               left=max(k.lounge_per_quarter - k.lounge_used_quarter, 0)))
            else:
                lines.append(t("cc_no_lounge", lang, variant=k.variant))
        elif q.detail == "annual_fee":
            if k.annual_fee and k.annual_fee_charged_on:
                lines.append(t("cc_fee", lang, fee=money(k.annual_fee), variant=k.variant, date=day(k.annual_fee_charged_on, lang),
                               waiver=money(k.annual_fee_waiver_spend or 0), spend=money(k.spend_last_year or 0)))
            else:
                lines.append(t("cc_no_fee", lang, variant=k.variant))
        elif q.detail == "international":
            amount = q.amount or 0
            if k.international:
                lines.append(t("cc_intl_on", lang, l4=k.last4, avail=money(k.available), amt=money(amount or k.available)))
            else:
                lines.append(t("cc_intl_off", lang, l4=k.last4))
                return PersonalAnswer(lines, fields=fields, offer={"action": {
                    "type": "controls", "card": "credit", "card_last4": k.last4, "international": "on"}})
        elif (q.amount or q.keyword or q.period) and not q.detail:  # a particular spend ("the ₹8,499 transaction")
            items = [x for x in k.transactions if (not q.amount or abs(x.amount - q.amount) <= max(q.amount * 0.1, 1))
                     and (not q.keyword or q.keyword.lower() in x.merchant.lower())]
            span = period_range(q.period, today)
            items = [x for x in items if not span or span[0] <= x.date <= span[1]][:5]
            if items:
                lines.append(t("cc_txns", lang, l4=k.last4))
                lines += [t("cc_txn", lang, when=_when(x.date, "", lang), who=x.merchant, amt=money(x.amount),
                            intl=t("cc_intl", lang) if x.international else "") for x in items]
        else:
            if k.total_due:
                lines.append(t("cc_due", lang, variant=k.variant, l4=k.last4, total=money(k.total_due), min=money(k.minimum_due),
                               date=day(k.due_date, lang)))
            else:
                lines.append(t("cc_paid_up", lang, variant=k.variant, l4=k.last4))
    if not lines:
        lines = [t("txn_none", lang, l4=" / ".join(k.last4 for k in picked))]
    return PersonalAnswer(lines, fields=fields)


def _debit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    cards = c.debit_cards
    if not cards:
        return PersonalAnswer([t("no_dc", lang)])
    hint = q.item or q.account
    picked = [d for d in cards if hint and (d.last4.endswith(hint[-4:]) or d.account_last4.endswith(hint[-4:]))] or cards
    if hint and "salary" in hint.lower():
        salary = {a.last4 for a in c.accounts if a.is_salary}
        picked = [d for d in cards if d.account_last4 in salary] or picked
    if len(picked) > 1 and q.detail == "status" and not q.item:  # "do I have any debit cards?": all of them
        return PersonalAnswer([t("cards_have", lang, n=len(picked), what=plural("debit card", lang),
                                 list=_listing(picked, lang, lambda d: d.variant, "debit card"))],
                              fields=[f"debit_card:{d.last4}" for d in picked])
    if len(picked) > 1 and q.detail != "declines":
        picked = [d for d in picked if d.status != "blocked"] or picked
        if len(picked) > 1:
            return PersonalAnswer([t("which_item", lang, n=len(picked), what=plural("debit card", lang),
                                     list=_listing(picked, lang, lambda d: d.variant, "debit card"))], "clarify")
    on, off = T["on_off"].get(lang, T["on_off"]["en"])
    lines, fields = [], []
    if q.detail == "declines":
        offer = None
        for d in picked:
            acct = next((a for a in c.accounts if a.last4 == d.account_last4), None)
            declined = [x for x in (acct.transactions if acct else []) if x.status == "failed" and x.channel in ("POS", "ECOM")]
            why = t("dc_blocked_why", lang) if d.status == "temp_blocked" else t("dc_pos_off_why", lang) if not d.pos else ""
            if declined:
                x = declined[0]
                same = [y for y in declined if y.date == x.date and y.amount == x.amount]
                lines.append(t("dc_declines", lang, n=len(same), amt=money(x.amount), who=x.counterparty, date=day(x.date, lang),
                               why=why or t("dc_unknown_why", lang)))
            elif why:  # nothing declined on record yet, but the card's settings explain it
                lines.append(t("dc_cause", lang, l4=d.last4, why=why))
            if offer is None and d.status == "temp_blocked":
                offer = {"type": "unblock", "card": "debit", "card_last4": d.last4}
                lines.append(t("dc_offer_unblock", lang, l4=d.last4)
                             + ("" if d.pos else " " + t("dc_pos_also_off", lang)))
            elif offer is None and not d.pos and d.status == "active":
                offer = {"type": "controls", "card": "debit", "card_last4": d.last4, "block": "", "pos": "on",
                         "atm": "", "ecom": "", "contactless": "", "international": ""}
                lines.append(t("dc_offer_pos", lang, l4=d.last4))
        return PersonalAnswer(lines or [t("dc_no_declines", lang)], fields=["debit_card:declines"],
                              offer={"action": offer} if offer else None)
    for d in picked:
        fields.append(f"debit_card:{d.last4}")
        controls = t("dc_controls", lang, **{k: on if getattr(d, k) else off for k in ("ecom", "pos", "contactless", "atm", "international")})
        lines.append(t("dc_state", lang, l4=d.last4, status=_status(d.status, lang), controls=controls))
    return PersonalAnswer(lines, fields=fields)


def _summary(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if q.detail == "upcoming":
        horizon = today + timedelta(days=7)
        items = [f"EMI ₹{money(x.emi)} on {day(x.next_due, lang)}" for x in c.loans if x.next_due and today <= x.next_due <= horizon]
        items += [f"card bill ₹{money(k.total_due)} on {day(k.due_date, lang)}" for k in c.credit_cards
                  if k.due_date and today <= k.due_date <= horizon and k.total_due]
        items += [f"FD ending {f.last4} maturing on {day(f.maturity_date, lang)}" for f in c.fds
                  if f.status == "active" and today <= f.maturity_date <= horizon]
        return PersonalAnswer([t("upcoming", lang, items="; ".join(items)) if items else t("upcoming_none", lang)], fields=["summary:upcoming"])
    if q.detail == "inactive":
        items = [f"your {a.type} account ending {a.last4}" for a in c.accounts if a.status != "active"]
        items += [f"your debit card ending {d.last4} ({_status(d.status, lang)})" for d in c.debit_cards if d.status != "active"]
        return PersonalAnswer([t("inactive", lang, items=" and ".join(items), verb="are" if len(items) > 1 else "is") if items
                               else t("inactive_none", lang)], fields=["summary:status"])
    if q.detail == "highest":
        top = max(c.accounts, key=lambda a: a.balance)
        lines = [t("highest", lang, acct=_acct(top, lang), amt=money(top.balance))]
        if c.loans:
            big = max(c.loans, key=lambda x: x.outstanding)
            lines.append(t("highest_loan", lang, type=_loan_name(big, lang), amt=money(big.outstanding)))
        return PersonalAnswer(lines, fields=["summary"])
    deposits = sum(a.balance for a in c.accounts)
    fds = [f for f in c.fds if f.status == "active"]
    lines = [t("sum_accounts", lang, n=len(c.accounts), word="account" if len(c.accounts) == 1 else "accounts", amt=money(deposits))]
    if fds:
        lines.append(_cap(t("sum_fd", lang, n=len(fds), word="FD" if len(fds) == 1 else "FDs", amt=money(sum(f.principal for f in fds)))))
    if c.loans:
        lines.append(_cap(t("sum_loan", lang, n=len(c.loans), word="loan" if len(c.loans) == 1 else "loans",
                            amt=money(sum(x.outstanding for x in c.loans)))))
    if c.credit_cards:
        lines.append(_cap(t("sum_cc", lang, n=len(c.credit_cards), word="card" if len(c.credit_cards) == 1 else "cards",
                            amt=money(sum(k.outstanding for k in c.credit_cards)))))
    loans = t("sum_loans_total", lang, amt=money(sum(x.outstanding for x in c.loans))) if c.loans else ""
    lines.append(t("sum_total", lang, dep=money(deposits + sum(f.principal for f in fds)), loans=loans))
    return PersonalAnswer(lines, fields=["summary"])
