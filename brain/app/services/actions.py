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


W = {
    "debit": {"en": "debit card", "hi": "डेबिट कार्ड", "hinglish": "debit card"},
    "credit": {"en": "{v} credit card", "hi": "{v} क्रेडिट कार्ड", "hinglish": "{v} credit card"},
    "ending": {"en": "{card} ending {l4}", "hi": "{card} (आख़िरी अंक {l4})", "hinglish": "{card} (ending {l4})"},
    "which": {"en": "Sure, I can do that. Which card is it: {list}?", "hi": "ज़रूर। कौन सा कार्ड: {list}?",
              "hinglish": "Zaroor. Kaunsa card: {list}?"},
    "none_match": {"en": "I can't find a card ending {l4}. Your cards are: {list}.", "hi": "आख़िरी अंक {l4} वाला कोई कार्ड नहीं मिला। आपके कार्ड: {list}।",
                   "hinglish": "Ending {l4} wala koi card nahi mila. Aapke cards: {list}."},
    "no_cards": {"en": "I don't see any card in your name.", "hi": "आपके नाम पर कोई कार्ड नहीं दिख रहा।",
                 "hinglish": "Aapke naam par koi card nahi dikh raha."},
    "confirm_temp_block": {"en": "Just to confirm, I'll temporarily block your {card}. You can unblock it any time, and if you'd rather block it permanently, just say so. Shall I go ahead?",
                           "hi": "पुष्टि के लिए: मैं आपका {card} अस्थायी रूप से ब्लॉक करूँगी। आप इसे कभी भी फिर से चालू कर सकते हैं; अगर स्थायी ब्लॉक चाहिए तो बताइए। क्या मैं आगे बढ़ूँ?",
                           "hinglish": "Confirm kar doon: main aapka {card} temporarily block karungi. Aap ise kabhi bhi unblock kar sakte hain; agar permanent block chahiye toh bataiye. Kya main aage badhun?"},
    "confirm_block": {"en": "Just to confirm, I'll permanently block your {card}. This can't be undone, and we'll send you a new card. Shall I go ahead?",
                      "hi": "पुष्टि के लिए: मैं आपका {card} स्थायी रूप से ब्लॉक करूँगी। यह वापस नहीं हो सकता, और हम आपको नया कार्ड भेजेंगे। क्या मैं आगे बढ़ूँ?",
                      "hinglish": "Confirm kar doon: main aapka {card} permanently block karungi. Yeh wapas nahi ho sakta, aur hum aapko naya card bhejenge. Kya main aage badhun?"},
    "confirm_unblock": {"en": "Just to confirm, I'll unblock your {card} so you can use it again. Shall I go ahead?",
                        "hi": "पुष्टि के लिए: मैं आपका {card} फिर से चालू करूँगी। क्या मैं आगे बढ़ूँ?",
                        "hinglish": "Confirm kar doon: main aapka {card} unblock karungi taaki aap use phir se use kar sakein. Kya main aage badhun?"},
    "confirm_controls": {"en": "Just to confirm, on your {card} I'll {changes}. Shall I go ahead?",
                         "hi": "पुष्टि के लिए: आपके {card} पर मैं {changes}। क्या मैं आगे बढ़ूँ?",
                         "hinglish": "Confirm kar doon: aapke {card} par main {changes}. Kya main aage badhun?"},
    "turn_on": {"en": "switch on {what}", "hi": "{what} चालू करूँगी", "hinglish": "{what} chalu karungi"},
    "turn_off": {"en": "switch off {what}", "hi": "{what} बंद करूँगी", "hinglish": "{what} band karungi"},
    "and": {"en": " and ", "hi": " और ", "hinglish": " aur "},
    "done_temp_block": {"en": "Done. Your {card} is now temporarily blocked.", "hi": "हो गया। आपका {card} अब अस्थायी रूप से ब्लॉक है।",
                        "hinglish": "Ho gaya. Aapka {card} ab temporarily blocked hai."},
    "done_block": {"en": "Done. Your {card} is now permanently blocked, and a replacement card will be sent to your registered address.",
                   "hi": "हो गया। आपका {card} अब स्थायी रूप से ब्लॉक है, और नया कार्ड आपके रजिस्टर्ड पते पर भेजा जाएगा।",
                   "hinglish": "Ho gaya. Aapka {card} ab permanently blocked hai, aur naya card aapke registered address par bheja jayega."},
    "done_unblock": {"en": "Done. Your {card} is active again.", "hi": "हो गया। आपका {card} फिर से चालू है।",
                     "hinglish": "Ho gaya. Aapka {card} phir se active hai."},
    "done_controls": {"en": "Done. On your {card}, {state}.", "hi": "हो गया। आपके {card} पर {state}।", "hinglish": "Ho gaya. Aapke {card} par {state}."},
    "reference": {"en": "Your reference number is {ref}, and you'll get an SMS confirming it.",
                  "hi": "आपका रेफ़रेंस नंबर {ref} है, और आपको इसका SMS भी आएगा।",
                  "hinglish": "Aapka reference number {ref} hai, aur aapko iska SMS bhi aayega."},
    "already": {"en": "Your {card} is already {state}, so there's nothing to change.", "hi": "आपका {card} पहले से {state} है, कुछ बदलने की ज़रूरत नहीं।",
                "hinglish": "Aapka {card} pehle se {state} hai, kuch change karne ki zaroorat nahi."},
    "not_blocked": {"en": "Your {card} isn't blocked; it's working normally.", "hi": "आपका {card} ब्लॉक नहीं है; यह ठीक से चल रहा है।",
                    "hinglish": "Aapka {card} block nahi hai; yeh normally chal raha hai."},
    "perm_blocked": {"en": "Your {card} was permanently blocked, so it can't be switched back on. Our cards team can arrange a replacement.",
                     "hi": "आपका {card} स्थायी रूप से ब्लॉक है, इसलिए इसे फिर से चालू नहीं किया जा सकता। हमारी कार्ड टीम नया कार्ड भिजवा सकती है।",
                     "hinglish": "Aapka {card} permanently blocked hai, isliye ise phir se chalu nahi kiya ja sakta. Hamari cards team replacement arrange kar sakti hai."},
    "blocked_first": {"en": "Your {card} is blocked right now, so I'd need to unblock it before changing its settings. Shall I unblock it?",
                      "hi": "आपका {card} अभी ब्लॉक है, इसलिए सेटिंग बदलने से पहले इसे चालू करना होगा। क्या मैं इसे चालू करूँ?",
                      "hinglish": "Aapka {card} abhi blocked hai, isliye settings change karne se pehle ise unblock karna hoga. Kya main unblock karun?"},
    "debit_only": {"en": "Those settings are only on debit cards; for your credit card I can switch international use on or off.",
                   "hi": "ये सेटिंग सिर्फ़ डेबिट कार्ड पर हैं; क्रेडिट कार्ड पर मैं अंतरराष्ट्रीय इस्तेमाल चालू या बंद कर सकती हूँ।",
                   "hinglish": "Yeh settings sirf debit card par hain; credit card par main international use on ya off kar sakti hoon."},
    "failed": {"en": "I'm sorry, I couldn't complete that just now, and nothing has been changed on your card. I can connect you to our cards team to do it for you. Shall I?",
               "hi": "माफ़ कीजिए, अभी यह पूरा नहीं हो पाया, और आपके कार्ड में कुछ नहीं बदला। मैं आपको हमारी कार्ड टीम से जोड़ सकती हूँ। जोड़ दूँ?",
               "hinglish": "Sorry, abhi yeh complete nahi ho paaya, aur aapke card mein kuch change nahi hua. Main aapko hamari cards team se connect kar sakti hoon. Kar doon?"},
    "unconfirmed": {"en": "I sent the request, but I couldn't confirm it went through, so please don't rely on it yet. Let me connect you to our cards team to check. Shall I?",
                    "hi": "मैंने अनुरोध भेजा, लेकिन पुष्टि नहीं हो पाई, इसलिए अभी इस पर भरोसा न करें। जाँच के लिए मैं आपको कार्ड टीम से जोड़ दूँ?",
                    "hinglish": "Maine request bheji, lekin confirm nahi ho paaya, isliye abhi is par bharosa mat kijiye. Check karne ke liye main aapko cards team se connect kar doon?"},
}
WHAT = {
    "atm": {"en": "ATM withdrawals", "hi": "ATM निकासी", "hinglish": "ATM withdrawals"},
    "pos": {"en": "shop payments", "hi": "दुकान पर भुगतान", "hinglish": "shop payments"},
    "ecom": {"en": "online payments", "hi": "ऑनलाइन भुगतान", "hinglish": "online payments"},
    "contactless": {"en": "tap-to-pay", "hi": "टैप-टू-पे", "hinglish": "tap-to-pay"},
    "international": {"en": "international use", "hi": "अंतरराष्ट्रीय इस्तेमाल", "hinglish": "international use"},
}
STATE = {"temp_blocked": {"en": "temporarily blocked", "hi": "अस्थायी रूप से ब्लॉक", "hinglish": "temporarily blocked"},
         "blocked": {"en": "permanently blocked", "hi": "स्थायी रूप से ब्लॉक", "hinglish": "permanently blocked"},
         "active": {"en": "active", "hi": "चालू", "hinglish": "active"}}


def w(key: str, lang: str, **values) -> str:
    return W[key].get(lang, W[key]["en"]).format(**values)


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
            return None, w("already", lang, card=label, state=STATE[card.status].get(lang, STATE[card.status]["en"])), False
        a = CardAction(kind, card.id, card.last4, getattr(card, "variant", ""), "block" if state == "blocked" else "temp_block",
                       {"status": state})
        return a, w("confirm_block" if state == "blocked" else "confirm_temp_block", lang, card=label), False
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
