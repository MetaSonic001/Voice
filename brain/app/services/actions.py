"""Controlled actions the agent may take itself, on a verified caller's cards, after an explicit yes.

Allowed (RBI's 2020 card-security direction asks banks to offer these through IVR as well as app and branch):
  block a card (temporarily, or permanently = hot-listing), unblock a temporarily blocked card,
  switch debit-card channels on or off (ATM, shop, online, tap-to-pay, international),
  switch a credit card's international use on or off.
Not allowed on this line, and routed to the right team instead: unblocking a permanently blocked card, card
replacement, limit changes, closing an FD, paying a bill, changing the registered mobile or address, reversing a
fee, refunds. Those need checks a person (or the customer's own app session) must do.

Flow (your diagram C): identify the card -> read its state -> confirm in one line -> clear yes -> execute ->
read the state back -> only then say it's done, with the reference number.
"""

import uuid
from dataclasses import asdict, dataclass, field

from app.domain import Customer
from app.services.content import Table
from app.services.personal import fill
from app.ports import Bank, BankError, BankUnconfirmed

CONTROL_KEYS = ("atm", "pos", "ecom", "contactless", "international")


@dataclass
class CardAction:
    kind: str  # debit | credit
    card_id: str
    last4: str
    variant: str
    action: str  # temp_block | block | unblock | controls
    changes: dict
    key: str = field(default_factory=lambda: str(uuid.uuid4()))  # Idempotency-Key: a retry never does it twice

    def to_dict(self) -> dict:
        return asdict(self)


W = Table("card")  # brain/content/catalog.json, "card.*"
WHAT = {
    "atm": {"en": "ATM withdrawals", "hi": "ATM से पैसे निकालना", "hinglish": "ATM withdrawals"},
    "pos": {"en": "shop payments", "hi": "दुकान पर पेमेंट", "hinglish": "shop payments"},
    "ecom": {"en": "online payments", "hi": "ऑनलाइन पेमेंट", "hinglish": "online payments"},
    "contactless": {"en": "tap-to-pay", "hi": "टैप-टू-पे", "hinglish": "tap-to-pay"},
    "international": {"en": "international use", "hi": "इंटरनेशनल इस्तेमाल", "hinglish": "international use"},
}
STATE = {"temp_blocked": {"en": "temporarily blocked", "hi": "टेम्पररी ब्लॉक", "hinglish": "temporarily blocked"},
         "blocked": {"en": "permanently blocked", "hi": "परमानेंट ब्लॉक", "hinglish": "permanently blocked"},
         "active": {"en": "active", "hi": "चालू", "hinglish": "active"}}


def w(key: str, lang: str, **values) -> str:
    return fill(W, key, lang, **values)


def card_label(kind: str, variant: str, last4: str, lang: str) -> str:
    card = w("debit", lang) if kind == "debit" else w("credit", lang, v=variant)
    return w("ending", lang, card=card, l4=last4)


def _cards(customer: Customer) -> list[tuple[str, object]]:
    return [("debit", d) for d in customer.debit_cards] + [("credit", k) for k in customer.credit_cards]


def plan(customer: Customer, req: dict, lang: str) -> tuple[CardAction | None, str, bool]:
    """(action, text, needs_card). With an action, `text` is the confirmation question. Without one, `text` says why
    (already in that state, not allowed, ...) or asks which card (`needs_card` True)."""
    kind, last4 = req.get("card") or "", "".join(ch for ch in str(req.get("card_last4") or "") if ch.isdigit())[-4:]
    cards = _cards(customer)
    if not cards:
        return None, w("no_cards", lang), False
    listing = ", ".join(card_label(k, getattr(c, "variant", ""), c.last4, lang) for k, c in cards)
    picked = [(k, c) for k, c in cards if (not kind or k == kind) and (not last4 or c.last4 == last4)]
    if not picked and last4:
        return None, w("none_match", lang, l4=last4, list=listing), True
    usable = [(k, c) for k, c in picked if c.status != "blocked"] or picked
    if req["type"] == "unblock":
        usable = [(k, c) for k, c in usable if c.status == "temp_blocked"] or usable
    if len(usable) != 1:
        return None, w("which", lang, list=", ".join(card_label(k, getattr(c, "variant", ""), c.last4, lang) for k, c in (usable or cards))), True
    kind, card = usable[0]
    label = card_label(kind, getattr(card, "variant", ""), card.last4, lang)
    action = req["type"]
    if action == "block":
        state = "blocked" if req.get("block") == "permanent" else "temp_blocked"
        if card.status == state or card.status == "blocked":
            return None, w("already_blocked", lang, card=label, state=STATE[card.status].get(lang, STATE[card.status]["en"])), False
        a = CardAction(kind, card.id, card.last4, getattr(card, "variant", ""), "block" if state == "blocked" else "temp_block",
                       {"status": state})
        key = "confirm_block" if state == "blocked" else "confirm_temp_block_asked" if req.get("block") == "temporary"             else "confirm_temp_block"
        return a, w(key, lang, card=label), False
    if card.status == "blocked":
        return None, w("perm_blocked", lang, card=label), False
    if action == "unblock":
        if card.status != "temp_blocked":
            return None, w("not_blocked", lang, card=label), False
        return CardAction(kind, card.id, card.last4, getattr(card, "variant", ""), "unblock", {"status": "active"}), \
            w("confirm_unblock", lang, card=label), False
    # channel controls (and international on / off)
    wanted = {k: req[k] == "on" for k in CONTROL_KEYS if req.get(k) in ("on", "off")}
    if kind == "credit":
        if set(wanted) - {"international"}:
            return None, w("debit_only", lang), False
    changes = {k: v for k, v in wanted.items() if getattr(card, k) != v}
    if not wanted:
        return None, "", True
    if card.status == "temp_blocked" and changes:
        return CardAction(kind, card.id, card.last4, getattr(card, "variant", ""), "unblock", {"status": "active"}), \
            w("blocked_first", lang, card=label), False
    if not changes:
        on_words = {"en": ("on", "off"), "hi": ("चालू", "बंद"), "hinglish": ("on", "off")}.get(lang, ("on", "off"))
        state = w("and", lang).join(f"{WHAT[k].get(lang, WHAT[k]['en'])} {on_words[0] if v else on_words[1]}" for k, v in wanted.items())
        return None, w("already", lang, card=label, state=state), False
    parts = [w("turn_on" if v else "turn_off", lang, what=WHAT[k].get(lang, WHAT[k]["en"])) for k, v in changes.items()]
    return CardAction(kind, card.id, card.last4, getattr(card, "variant", ""), "controls", changes), \
        w("confirm_controls", lang, card=label, changes=w("and", lang).join(parts)), False


def execute(bank: Bank, customer: Customer, a: CardAction, lang: str) -> tuple[bool, str, dict]:
    """(done, what to say, record). `done` only when the state read back from the bank matches the request."""
    label = card_label(a.kind, a.variant, a.last4, lang)
    try:
        result = bank.update_card(customer.cif, a.kind, a.card_id, a.changes, a.action, key=a.key)
    except BankUnconfirmed as e:  # sent, but no answer: never say it failed, never say it worked
        return False, w("unconfirmed", lang), {"error": str(e), "unconfirmed": True}
    except BankError as e:
        return False, w("failed", lang), {"error": str(e)}
    if any(result["after"].get(k) != v for k, v in a.changes.items()):
        return False, w("unconfirmed", lang), result
    if a.action == "controls":
        on_words = {"en": ("on", "off"), "hi": ("चालू", "बंद"), "hinglish": ("on", "off")}.get(lang, ("on", "off"))
        state = w("and", lang).join(f"{WHAT[k].get(lang, WHAT[k]['en'])} {'is' if lang == 'en' else ''} "
                                    f"{on_words[0] if v else on_words[1]}".replace("  ", " ") for k, v in a.changes.items())
        text = w("done_controls", lang, card=label, state=state)
    else:
        text = w(f"done_{a.action}", lang, card=label)
    return True, f"{text} {w('reference', lang, ref=result['reference'])}", result
