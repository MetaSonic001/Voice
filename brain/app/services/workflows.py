"""Finding the transaction a caller means (PHASE-4-PLAN §3.4). Pure functions over the customer's bank data.

The rule that fixed issues A and K: **never guess the transaction.** A payment is only offered as "the one" when the
caller gave something to match (an amount, a time, or a merchant / channel). With nothing to match, the answer is
"I could not find it, tell me more", not the latest debit.
"""

from dataclasses import asdict, dataclass
from datetime import date

from app.domain import Customer
from app.services.personal import PersonalQuery, period_range

CARD_CHANNELS = ("POS", "ECOM", "ATM")


@dataclass
class Candidate:
    source: str  # card (a credit card) | account (a savings account)
    amount: float
    who: str
    on: str  # ISO date (kept as text so it survives the pending-question store)
    channel: str
    status: str  # success | pending | failed | reversed | unknown
    reference: str = ""
    card_kind: str = ""  # debit | credit when the payment was made with a card
    card_last4: str = ""
    account_last4: str = ""

    @property
    def is_card_payment(self) -> bool:
        return bool(self.card_kind)

    @property
    def date(self) -> date:
        return date.fromisoformat(self.on)

    def to_dict(self) -> dict:
        return asdict(self)


def find_candidates(c: Customer, q: PersonalQuery, today: date, keyword: str = "") -> list[Candidate]:
    """Debits that match what the caller said, newest first. Empty when they gave nothing to match."""
    span = period_range(q.period, today)
    key = (keyword or q.keyword or "").lower()
    if not (q.amount or span or key):
        return []
    found: list[Candidate] = []
    for card in c.credit_cards:
        for x in card.transactions:
            if _fits(x.amount, x.date, f"{x.merchant}", q, span, key, today):
                found.append(Candidate("card", x.amount, x.merchant, x.date.isoformat(), "CARD", x.status,
                                       card_kind="credit", card_last4=card.last4))
    for a in c.accounts:
        for x in a.transactions:
            if x.kind != "Debit" or not _fits(x.amount, x.date, f"{x.description} {x.counterparty} {x.channel}", q, span, key, today):
                continue
            by_card = x.channel in CARD_CHANNELS and a.debit_card is not None
            found.append(Candidate("account", x.amount, x.counterparty or x.description, x.date.isoformat(), x.channel, x.status,
                                   x.reference, "debit" if by_card else "", a.debit_card.last4 if by_card else "", a.last4))
    return sorted(found, key=lambda x: x.on, reverse=True)


def _fits(amount: float, on: date, text: str, q: PersonalQuery, span, key: str, today: date) -> bool:
    if on > today:
        return False
    if q.amount and abs(amount - q.amount) > max(q.amount * 0.02, 1):
        return False
    if span and not span[0] <= on <= span[1]:
        return False
    return not key or key in text.lower()


def recent_card_spend(c: Customer, kind: str, last4: str, today: date, days: int = 7) -> list[Candidate]:
    """Payments made with one card in the last `days` days (after a lost card: 'do you recognise these?')."""
    out: list[Candidate] = []
    if kind == "credit":
        for card in c.credit_cards:
            for x in card.transactions:
                if card.last4 == last4 and 0 <= (today - x.date).days <= days:
                    out.append(Candidate("card", x.amount, x.merchant, x.date.isoformat(), "CARD", x.status,
                                         card_kind="credit", card_last4=last4))
    else:
        for a in c.accounts:
            if a.debit_card and a.debit_card.last4 == last4:
                for x in a.transactions:
                    if x.kind == "Debit" and x.channel in CARD_CHANNELS and 0 <= (today - x.date).days <= days:
                        out.append(Candidate("account", x.amount, x.counterparty or x.description, x.date.isoformat(), x.channel,
                                             x.status, x.reference, "debit", last4, a.last4))
    return sorted(out, key=lambda x: x.on, reverse=True)


def recent_debits(c: Customer, today: date, days: int = 7) -> list[Candidate]:
    """The latest debits on every account and credit card, newest first: shown to a caller who reports a debit they don't
    recognise without saying which one ("is it one of these?"). Asking, never guessing."""
    out: list[Candidate] = []
    for card in c.credit_cards:
        for x in card.transactions:
            if 0 <= (today - x.date).days <= days and x.status != "failed":
                out.append(Candidate("card", x.amount, x.merchant, x.date.isoformat(), "CARD", x.status,
                                     card_kind="credit", card_last4=card.last4))
    for a in c.accounts:
        for x in a.transactions:
            if x.kind == "Debit" and 0 <= (today - x.date).days <= days and x.status != "failed":
                by_card = x.channel in CARD_CHANNELS and a.debit_card is not None
                out.append(Candidate("account", x.amount, x.counterparty or x.description, x.date.isoformat(), x.channel,
                                     x.status, x.reference, "debit" if by_card else "", a.debit_card.last4 if by_card else "",
                                     a.last4))
    return sorted(out, key=lambda x: x.on, reverse=True)
