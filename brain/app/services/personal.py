"""Answers about the verified caller's own products, from bank data, through fixed templates (en / hi / hinglish).

No LLM writes these answers: the understand step only picks the topic and filters, and the figures come straight
from the bank data, so they cannot be invented. Only the last 4 digits of any account or card are ever said.
When the data cannot answer ("I can see X, not Y"), the reply says what is visible and offers a transfer.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain import Account, Customer

TOPICS = ("balance", "transactions", "fd", "loan", "credit_card", "debit_card", "summary", "other")
PERIODS = ("", "today", "yesterday", "last_7_days", "this_week", "this_month", "last_month", "last_30_days")
_HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


@dataclass
class PersonalQuery:
    topic: str = "other"
    account: str = ""  # which account: "salary", "4521", "digital", "second", "all", or "" (not said)
    direction: str = "any"  # any | debit | credit
    period: str = ""
    count: int = 0  # number of transactions asked for (0 = default 5)
    min_amount: float = 0
    keyword: str = ""  # a word to match in transactions ("upi", "salary", "refund") or a detail ("minimum due")
    total: bool = False  # "how much did I spend": sum the matching transactions


@dataclass
class PersonalAnswer:
    sentences: list[str]
    outcome: str = "answered"  # answered | clarify
    fields: list[str] = field(default_factory=list)  # what was disclosed (audit)


# ───────── wording ─────────

T = {
    "acct": {"en": "Your {type} savings account ending {l4}", "hi": "आपके {type} बचत खाते (आख़िरी अंक {l4})",
             "hinglish": "Aapke {type} savings account (ending {l4})"},
    "balance": {"en": "{acct} has a balance of ₹{amt}.", "hi": "{acct} में ₹{amt} बैलेंस है।",
                "hinglish": "{acct} mein ₹{amt} balance hai."},
    "which": {"en": "You have {n} accounts: {list}. Which one would you like?",
              "hi": "आपके {n} खाते हैं: {list}। आप किस खाते के बारे में जानना चाहेंगे?",
              "hinglish": "Aapke {n} accounts hain: {list}. Kaunsa account dekhna hai?"},
    "no_match": {"en": "I couldn't find an account matching that. Your accounts are: {list}.",
                 "hi": "उस तरह का कोई खाता नहीं मिला। आपके खाते हैं: {list}।",
                 "hinglish": "Aisa koi account nahi mila. Aapke accounts hain: {list}."},
    "no_salary": {"en": "I can't tell which one is your salary account, as I don't see salary credits in recent "
                        "transactions. Your accounts are: {list}. Which one would you like?",
                  "hi": "हाल के लेनदेन में सैलरी क्रेडिट नहीं दिख रहा, इसलिए सैलरी खाता पहचान नहीं पा रही। आपके खाते हैं: {list}। किस खाते के बारे में बताऊँ?",
                  "hinglish": "Recent transactions mein salary credit nahi dikh raha, isliye salary account pehchaan nahi pa rahi. "
                              "Aapke accounts hain: {list}. Kaunsa account dekhna hai?"},
    "txn_intro": {"en": "Your last {k} transactions on the account ending {l4}:",
                  "hi": "खाता (आख़िरी अंक {l4}) के पिछले {k} लेनदेन:",
                  "hinglish": "Account (ending {l4}) ke pichhle {k} transactions:"},
    "txn_found": {"en": "I found {k} matching transactions on the account ending {l4}:",
                  "hi": "खाता (आख़िरी अंक {l4}) में {k} मिलते-जुलते लेनदेन मिले:",
                  "hinglish": "Account (ending {l4}) mein {k} matching transactions mile:"},
    "txn": {"en": "On {date}, {desc}, a {kind} of ₹{amt}.", "hi": "{date} को {desc}, ₹{amt} {kind}।",
            "hinglish": "{date} ko {desc}, ₹{amt} {kind}."},
    "txn_total": {"en": "Matching debits on the account ending {l4} total ₹{amt}, across {k} transactions.",
                  "hi": "खाता (आख़िरी अंक {l4}) में मिलते-जुलते डेबिट कुल ₹{amt} हैं, {k} लेनदेन में।",
                  "hinglish": "Account (ending {l4}) mein matching debits total ₹{amt} hain, {k} transactions mein."},
    "txn_none": {"en": "I don't see any matching transactions on the account ending {l4}.",
                 "hi": "खाता (आख़िरी अंक {l4}) में ऐसा कोई लेनदेन नहीं दिख रहा।",
                 "hinglish": "Account (ending {l4}) mein aisa koi transaction nahi dikh raha."},
    "txn_scope": {"en": "I can see the last {n} transactions, from {oldest}.",
                  "hi": "मुझे {oldest} से अब तक के पिछले {n} लेनदेन दिखते हैं।",
                  "hinglish": "Mujhe {oldest} se ab tak ke pichhle {n} transactions dikhte hain."},
    "txn_no_names": {"en": "Transaction details show only the type, like UPI Transfer or POS Purchase, not the names of people or merchants.",
                     "hi": "लेनदेन में सिर्फ़ प्रकार दिखता है, जैसे UPI Transfer या POS Purchase, लोगों या दुकानों के नाम नहीं।",
                     "hinglish": "Transactions mein sirf type dikhta hai, jaise UPI Transfer ya POS Purchase, logon ya merchants ke naam nahi."},
    "fd": {"en": "Your fixed deposit of ₹{amt} earns {rate} and {verb} on {date}.",
           "hi": "आपकी ₹{amt} की FD पर {rate} ब्याज है और यह {date} को {verb}।",
           "hinglish": "Aapki ₹{amt} ki FD par {rate} interest hai aur yeh {date} ko {verb}."},
    "fd_verb": {"en": ("matures", "matured"), "hi": ("मैच्योर होगी", "मैच्योर हो चुकी है"),
                "hinglish": ("mature hogi", "mature ho chuki hai")},
    "fd_one": {"en": "I can see one fixed deposit in your profile.", "hi": "आपकी प्रोफ़ाइल में एक FD दिख रही है।",
               "hinglish": "Aapki profile mein ek FD dikh rahi hai."},
    "no_fd": {"en": "I don't see any fixed deposit in your profile.", "hi": "आपकी प्रोफ़ाइल में कोई FD नहीं दिख रही।",
              "hinglish": "Aapki profile mein koi FD nahi dikh rahi."},
    "loan": {"en": "Your loan has ₹{out} outstanding.", "hi": "आपके लोन पर ₹{out} बकाया है।",
             "hinglish": "Aapke loan par ₹{out} outstanding hai."},
    "emi": {"en": "Your EMI is ₹{emi}; {paid} are paid and {left} are left, and the next one is due on {date}.",
            "hi": "आपकी EMI ₹{emi} है; {paid} भरी जा चुकी हैं और {left} बाकी हैं, अगली EMI {date} को है।",
            "hinglish": "Aapki EMI ₹{emi} hai; {paid} bhari ja chuki hain aur {left} baaki hain, agli EMI {date} ko hai."},
    "loan_one": {"en": "I can see one loan in your profile; its type isn't shown.",
                 "hi": "आपकी प्रोफ़ाइल में एक लोन दिख रहा है; उसका प्रकार नहीं दिखता।",
                 "hinglish": "Aapki profile mein ek loan dikh raha hai; uska type nahi dikhta."},
    "no_loan": {"en": "I don't see any loan in your profile.", "hi": "आपकी प्रोफ़ाइल में कोई लोन नहीं दिख रहा।",
                "hinglish": "Aapki profile mein koi loan nahi dikh raha."},
    "cc": {"en": "Your {type} credit card ending {l4} has ₹{out} outstanding, due on {due}.",
           "hi": "आपके {type} क्रेडिट कार्ड (आख़िरी अंक {l4}) पर ₹{out} बकाया है, जिसकी आख़िरी तारीख {due} है।",
           "hinglish": "Aapke {type} credit card (ending {l4}) par ₹{out} outstanding hai, due date {due} hai."},
    "cc_limit": {"en": "Your credit limit is ₹{limit}, so ₹{avail} is available.",
                 "hi": "आपकी क्रेडिट लिमिट ₹{limit} है, यानी ₹{avail} उपलब्ध है।",
                 "hinglish": "Aapki credit limit ₹{limit} hai, yaani ₹{avail} available hai."},
    "cc_bill": {"en": "Your last statement was generated on {date}.", "hi": "आपका पिछला स्टेटमेंट {date} को बना था।",
                "hinglish": "Aapka pichhla statement {date} ko generate hua tha."},
    "cc_no_min": {"en": "The minimum amount due isn't shown here.", "hi": "न्यूनतम देय राशि यहाँ नहीं दिखती।",
                  "hinglish": "Minimum amount due yahan nahi dikhta."},
    "no_cc": {"en": "I don't see a credit card in your profile.", "hi": "आपकी प्रोफ़ाइल में कोई क्रेडिट कार्ड नहीं दिख रहा।",
              "hinglish": "Aapki profile mein koi credit card nahi dikh raha."},
    "dc": {"en": "Your {variant} debit card ends in {l4}.", "hi": "आपके {variant} डेबिट कार्ड के आख़िरी अंक {l4} हैं।",
           "hinglish": "Aapke {variant} debit card ke last digits {l4} hain."},
    "dc_controls": {"en": "Online transactions are {ecom}, card payments at shops are {pos}, and ATM withdrawals are {atm}.",
                    "hi": "ऑनलाइन लेनदेन {ecom} हैं, दुकानों पर कार्ड से भुगतान {pos} है, और ATM निकासी {atm} है।",
                    "hinglish": "Online transactions {ecom} hain, dukaanon par card payment {pos} hai, aur ATM withdrawal {atm} hai."},
    "on_off": {"en": ("enabled", "disabled"), "hi": ("चालू", "बंद"), "hinglish": ("chalu", "band")},
    "no_dc": {"en": "I don't see a debit card in your profile.", "hi": "आपकी प्रोफ़ाइल में कोई डेबिट कार्ड नहीं दिख रहा।",
              "hinglish": "Aapki profile mein koi debit card nahi dikh raha."},
    "sum_accounts": {"en": "You have {n} savings {word} with a total balance of ₹{amt}.",
                     "hi": "आपके {n} बचत खाते हैं, जिनमें कुल ₹{amt} हैं।",
                     "hinglish": "Aapke {n} savings accounts hain, jinmein total ₹{amt} hai."},
    "sum_fd": {"en": "You have a fixed deposit of ₹{amt}.", "hi": "आपकी ₹{amt} की एक FD है।",
               "hinglish": "Aapki ₹{amt} ki ek FD hai."},
    "sum_loan": {"en": "You have a loan with ₹{amt} outstanding.", "hi": "आपके एक लोन पर ₹{amt} बकाया है।",
                 "hinglish": "Aapke ek loan par ₹{amt} outstanding hai."},
    "sum_cc": {"en": "Your {type} credit card has ₹{amt} outstanding.", "hi": "आपके {type} क्रेडिट कार्ड पर ₹{amt} बकाया है।",
               "hinglish": "Aapke {type} credit card par ₹{amt} outstanding hai."},
    "sum_total": {"en": "Your total deposits are ₹{amt}.", "hi": "आपकी कुल जमा राशि ₹{amt} है।",
                  "hinglish": "Aapki total deposits ₹{amt} hain."},
    "detail_unseen": {"en": "I can't see that detail here. I can connect you to our team for it.",
                      "hi": "यह जानकारी मुझे यहाँ नहीं दिखती। इसके लिए मैं आपको हमारी टीम से जोड़ सकती हूँ।",
                      "hinglish": "Yeh jaankari mujhe yahan nahi dikhti. Iske liye main aapko hamari team se connect kar sakti hoon."},
    "cannot_see": {"en": "I can see your account balances, recent transactions, fixed deposit, loan and EMI, and card details, "
                         "but not that detail. I can connect you to our team for it.",
                   "hi": "मुझे आपके खातों का बैलेंस, हाल के लेनदेन, FD, लोन और EMI, और कार्ड की जानकारी दिखती है, लेकिन यह जानकारी नहीं। "
                         "इसके लिए मैं आपको हमारी टीम से जोड़ सकती हूँ।",
                   "hinglish": "Mujhe aapke accounts ka balance, recent transactions, FD, loan aur EMI, aur card details dikhti hain, "
                               "lekin yeh jaankari nahi. Iske liye main aapko hamari team se connect kar sakti hoon."},
}


def t(key: str, lang: str, **values) -> str:
    return T[key].get(lang, T[key]["en"]).format(**values)


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
    if d is None:
        return "-"
    return f"{d.day} {_HI_MONTHS[d.month - 1]} {d.year}" if lang == "hi" else d.strftime("%d %b %Y").lstrip("0")


def period_range(period: str, today: date) -> tuple[date, date] | None:
    if period == "today":
        return today, today
    if period == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "this_week":
        return today - timedelta(days=today.weekday()), today
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "last_30_days":
        return today - timedelta(days=29), today
    return None


# ───────── answering ─────────


# Details callers ask about that the bank data doesn't hold: answer what is visible, then say so.
_UNSEEN = ("renew", "premature", "break", "penalty", "lounge", "minimum balance", "statement paid", "refund status",
           "nominee", "interest earned", "charge applied")


def answer(customer: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    handler = {"balance": _balance, "transactions": _transactions, "fd": _fd, "loan": _loan,
               "credit_card": _credit_card, "debit_card": _debit_card, "summary": _summary}.get(q.topic)
    if handler is None:
        return PersonalAnswer([t("cannot_see", lang)])
    result = handler(customer, q, lang, today)
    if result.outcome == "answered" and any(w in q.keyword for w in _UNSEEN):
        result.sentences.append(t("detail_unseen", lang))
    return result


_KNOWN_TYPES = ("bsbda", "digital", "vikaas", "value", "platinum", "royale")
_CHOICE_WORDS = [
    ("all", r"\b(both|all|every|dono|donon|sab|sabhi)\b|दोनों|सभी|सब"),
    ("first", r"\b(first|1st|pehla|pehle|pahla)\b|पहला|पहले"),
    ("second", r"\b(second|2nd|dusra|doosra|dusre|doosre)\b|दूसरा|दूसरे"),
    ("third", r"\b(third|3rd|teesra|tisra|teesre)\b|तीसरा|तीसरे"),
    ("salary", r"\bsalary\b|सैलरी|वेतन"),
]


def account_choice(text: str) -> str:
    """The account a caller names when answering "which account?" ("second one", "dusra wala", "0002", "salary"),
    read in code - not left to the LLM. Empty if the text doesn't name one."""
    t = text.lower()
    if m := re.search(r"(?<!\d)(\d{4})(?!\d)", t):
        return m.group(1)
    for value, pattern in _CHOICE_WORDS:
        if re.search(pattern, t):
            return value
    return next((k for k in _KNOWN_TYPES if re.search(rf"\b{k}\b", t)), "")


def _accounts(customer: Customer, hint: str, lang: str, default_all: bool = False) -> tuple[list[Account] | None, PersonalAnswer | None]:
    """The account(s) the caller means, or a question back when it's unclear. `default_all`: no account named
    means every account (searches like "was my salary credited", "UPI spend this week")."""
    accounts = customer.accounts
    listing = ", ".join(f"{a.type} {'ending ' if lang != 'hi' else ''}{a.last4}" for a in accounts)
    h = hint.strip().lower()
    recognised = h in ("all", "both", "every", "first", "second", "third") or "salary" in h         or any(ch.isdigit() for ch in h) or any(k in h for k in _KNOWN_TYPES)
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
            return None, PersonalAnswer([t("no_salary", lang, list=listing)], "clarify")
    elif h in ("first", "second", "third"):
        i = ("first", "second", "third").index(h)
        picked = accounts[i:i + 1]
    else:
        picked = [a for a in accounts if a.type.lower() in h or h in a.type.lower()]
    if not picked:
        return None, PersonalAnswer([t("no_match", lang, list=listing)], "clarify")
    if len(picked) > 1 and h not in ("all", "both"):
        picked_listing = ", ".join(f"{a.type} {'ending ' if lang != 'hi' else ''}{a.last4}" for a in picked)
        return None, PersonalAnswer([t("which", lang, n=len(picked), list=picked_listing)], "clarify")
    return picked, None


def _acct(a: Account, lang: str) -> str:
    return t("acct", lang, type=a.type, l4=a.last4)


def _balance(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    accounts, ask = _accounts(c, q.account, lang)
    if ask:
        return ask
    return PersonalAnswer([t("balance", lang, acct=_acct(a, lang), amt=money(a.balance)) for a in accounts],
                          fields=[f"account:{a.last4}:balance" for a in accounts])


def _transactions(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    filtered = bool(q.period or q.direction != "any" or q.min_amount or q.keyword or q.total)
    accounts, ask = _accounts(c, q.account, lang, default_all=filtered)
    if ask:
        return ask
    sentences, fields = [], []
    span = period_range(q.period, today)
    keyword = q.keyword.strip().lower()
    for a in accounts:
        items = [x for x in a.transactions if x.date <= today]  # nothing "after today" in the demo data
        if span:
            items = [x for x in items if span[0] <= x.date <= span[1]]
        if q.direction in ("debit", "credit"):
            items = [x for x in items if x.kind.lower() == q.direction]
        if q.min_amount:
            items = [x for x in items if x.amount >= q.min_amount]
        if keyword:
            items = [x for x in items if keyword in x.description.lower()]
        fields.append(f"account:{a.last4}:transactions")
        if q.total:
            debits = [x for x in items if x.kind.lower() == "debit"]
            if debits:
                sentences.append(t("txn_total", lang, l4=a.last4, amt=money(sum(x.amount for x in debits)), k=len(debits)))
                continue
            items = []
        if not items:
            visible = [x for x in a.transactions if x.date <= today]
            sentences.append(t("txn_none", lang, l4=a.last4))
            if visible:
                sentences.append(t("txn_scope", lang, n=len(visible), oldest=day(visible[-1].date, lang)))
            if keyword and not any(keyword in x.description.lower() for x in a.transactions):
                sentences.append(t("txn_no_names", lang))
            continue
        shown = items[: max(1, min(q.count or 5, 10))]
        sentences.append(t("txn_found" if filtered else "txn_intro", lang, k=len(shown), l4=a.last4))
        kinds = {"en": {"Credit": "credit", "Debit": "debit"}, "hi": {"Credit": "जमा", "Debit": "डेबिट"},
                 "hinglish": {"Credit": "credit", "Debit": "debit"}}.get(lang, {})
        sentences += [t("txn", lang, date=day(x.date, lang), desc=x.description, kind=kinds.get(x.kind, x.kind.lower()),
                        amt=money(x.amount)) for x in shown]
    return PersonalAnswer(sentences, fields=fields)


def _fd(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.fd:
        return PersonalAnswer([t("no_fd", lang)])
    future, past = T["fd_verb"].get(lang, T["fd_verb"]["en"])
    matured = c.fd["maturity"] is not None and c.fd["maturity"] < today
    lines = [t("fd_one", lang)] if (q.account or "all" in q.keyword or "fds" in q.keyword) else []
    lines.append(t("fd", lang, amt=money(c.fd["balance"]), rate=c.fd["rate"], verb=past if matured else future,
                   date=day(c.fd["maturity"], lang)))
    return PersonalAnswer(lines, fields=["fd:balance", "fd:rate", "fd:maturity"])


def _loan(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    if not c.loan:
        return PersonalAnswer([t("no_loan", lang)])
    lines = [t("loan_one", lang)] if any(w in q.keyword for w in ("home", "personal", "car", "gold", "both", "type")) else []
    lines.append(t("loan", lang, out=money(c.loan["outstanding"])))
    if c.loan["emi"]:
        lines.append(t("emi", lang, emi=money(c.loan["emi"]), paid=c.loan["paid"], left=c.loan["left"],
                       date=day(c.loan["next_due"], lang)))
    return PersonalAnswer(lines, fields=["loan:outstanding", "loan:emi"])


def _credit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    cc = c.credit_card
    if not cc:
        return PersonalAnswer([t("no_cc", lang)])
    lines = [t("cc", lang, type=cc["type"], l4=cc["last4"], out=money(cc["outstanding"]), due=day(cc["due"], lang)),
             t("cc_limit", lang, limit=money(cc["limit"]), avail=money(max(cc["limit"] - cc["outstanding"], 0)))]
    if cc["bill_date"]:
        lines.append(t("cc_bill", lang, date=day(cc["bill_date"], lang)))
    if "minimum" in q.keyword:
        lines.append(t("cc_no_min", lang))
    return PersonalAnswer(lines, fields=["credit_card:outstanding", "credit_card:limit", "credit_card:dates"])


def _debit_card(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    dc = c.debit_card
    if not dc:
        return PersonalAnswer([t("no_dc", lang)])
    on, off = T["on_off"].get(lang, T["on_off"]["en"])
    state = {k: on if str(dc[k]).lower() == "enabled" else off for k in ("ecom", "pos", "atm")}
    return PersonalAnswer([t("dc", lang, variant=dc["variant"] or "", l4=dc["last4"]).replace("  ", " "),
                           t("dc_controls", lang, **state)], fields=["debit_card:last4", "debit_card:controls"])


def _summary(c: Customer, q: PersonalQuery, lang: str, today: date) -> PersonalAnswer:
    total = sum(a.balance for a in c.accounts)
    lines = [t("sum_accounts", lang, n=len(c.accounts), word="account" if len(c.accounts) == 1 else "accounts",
               amt=money(total))]
    if c.fd:
        lines.append(t("sum_fd", lang, amt=money(c.fd["balance"])))
    if c.loan:
        lines.append(t("sum_loan", lang, amt=money(c.loan["outstanding"])))
    if c.credit_card:
        lines.append(t("sum_cc", lang, type=c.credit_card["type"], amt=money(c.credit_card["outstanding"])))
    if c.fd:
        lines.append(t("sum_total", lang, amt=money(total + c.fd["balance"])))
    return PersonalAnswer(lines, fields=["summary"])
