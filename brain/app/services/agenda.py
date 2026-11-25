"""The call agenda: every separate thing the caller asked, and what has happened to each (19 Nov 2026).

The LLM only finds the questions (understanding: `asks` + `requests`) and reads the caller's reply to "Shall I go on?"
(`agenda_reply`). Everything else is decided here, in code, the same way every time:

| Situation | What happens |
|---|---|
| 1 question, or 2 short reads (balance + EMI date) | answered in the same reply |
| 3+ questions, a card action, a long answer | "You've asked about three things: A, B and C. Let me take them one by one." -> A -> "Shall I go on to B?" |
| a fraud / lost-card request | first (understanding.with_risk already puts it first) |
| yes / "go on" | the next item |
| no / "that's all" | the rest are skipped: "Okay, I'll leave those. Anything else?" |
| "skip that one" / "just the last one" | that one is skipped / the chosen one is answered |
| a new question in the middle | answered, then "Shall I go back to B?" |
| an item offered twice and not taken up | dropped |
| goodbye with items left | "Before you go, you'd also asked about B. Shall I cover that?" (once) |
| a hand-over to a person | the open items go to the person (the desk brief lists them) |

Items live in `CallState.agenda` (Postgres `sessions.state`), a few hundred bytes each and at most `MAX_KEPT` per call,
so nothing grows in the brain's memory between turns. This module is pure: no database, no network, no LLM.
"""

import re

from app.domain import Request
from app.services.content import Table

MAX_NEW = 8  # separate things taken from one turn
MAX_KEPT = 20  # items kept per call (settled ones are pruned first)
MAX_OFFERS = 2  # "Shall I go on / go back to X?" at most twice; then X is let go
OPEN = ("pending", "auth")  # still to answer ("auth": waiting for the caller to verify)
WAITING = "waiting"  # its answer asked something back (which account? shall I block it?)
SHORT_KINDS = ("personal", "general", "new_customer", "smalltalk", "out_of_scope")
DONE_OUTCOMES = ("answered", "action_done", "ticket", "callback", "smalltalk", "out_of_scope")
FAILED_OUTCOMES = ("refused", "error", "action_failed", "needs_verification")

TEMPLATES = Table("agenda")  # brain/content/catalog.json, "agenda.*"
_COUNT = {"en": {4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"},
          "hi": {4: "चार", 5: "पाँच", 6: "छह", 7: "सात", 8: "आठ"},
          "hinglish": {4: "chaar", 5: "paanch", 6: "chhe", 7: "saat", 8: "aath"}}
# Labels code uses when the LLM gave none (a request code added, or an unusable label). Hindi in the oblique form, to
# fit "... के बारे में".
_FALLBACK = {
    "balance": ("your balance", "आपके बैलेंस", "aapke balance"),
    "transactions": ("your transactions", "आपके ट्रांज़ैक्शन", "aapke transactions"),
    "loan": ("your loan", "आपके लोन", "aapke loan"),
    "fd": ("your FD", "आपकी FD", "aapki FD"),
    "credit_card": ("your credit card", "आपके क्रेडिट कार्ड", "aapke credit card"),
    "debit_card": ("your debit card", "आपके डेबिट कार्ड", "aapke debit card"),
    "charges": ("the charges", "चार्ज", "charges"),
    "block": ("blocking your card", "कार्ड ब्लॉक करने", "card block karne"),
    "unrecognised_debit": ("a payment you don't recognise", "उस पेमेंट", "us payment"),
    "human": ("connecting you to our team", "हमारी टीम से जोड़ने", "hamari team se connect karne"),
    "other": ("your other question", "आपके दूसरे सवाल", "aapke dusre sawaal"),
}
_LANG = {"en": 0, "hi": 1, "hinglish": 2}


def say(kind: str, lang: str, **values) -> str:
    texts = TEMPLATES[kind]
    return texts.get(lang, texts["en"]).format(**values)


# ───────── reading and building items ─────────

def find(state, item_id: int) -> dict | None:
    return next((i for i in state.agenda if i["id"] == item_id), None)


def open_items(state) -> list[dict]:
    return [i for i in state.agenda if i["status"] in OPEN]


def batch(state) -> int:
    return int(state.agenda_meta.get("batch") or 0)


def same(item: dict, r: Request) -> bool:
    """The caller asked again for something already on the agenda (after a failed code, or out of impatience)."""
    old = item.get("req") or {}
    if old.get("kind") != r.kind:
        return False
    if r.kind == "personal":
        a, b = old.get("personal") or {}, r.personal or {}
        return all((a.get(k) or "") == (b.get(k) or "") for k in ("topic", "detail", "account", "item"))
    if r.kind == "action":
        a, b = old.get("action") or {}, r.action or {}
        return all((a.get(k) or "") == (b.get(k) or "") for k in ("type", "card", "card_last4"))
    return _norm(old.get("question", "")) == _norm(r.question)


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def add(state, requests: list[Request], extra_asks: list[dict] | None = None, followup: str = "") -> list[dict]:
    """Put this turn's requests on the agenda (fully understood ones, then the extra ones not yet understood, then a
    follow-up code adds) and return them in the order they are to be handled. A request already waiting on the agenda
    is not added twice: the old item is brought forward instead."""
    state.agenda_meta["batch"] = turn = batch(state) + 1
    next_id = max((i["id"] for i in state.agenda), default=0) + 1
    out: list[dict] = []
    for r in requests:
        dup = next((i for i in state.agenda if i["status"] in OPEN + (WAITING,) and i.get("req") and same(i, r)), None)
        if dup is not None:
            dup.update(batch=turn, status="pending", req=_clean(r).to_dict(), label=dup["label"] or r.label)
            if dup not in out:
                out.append(dup)
            continue
        out.append({"id": next_id, "batch": turn, "label": r.label, "quote": "", "req": _clean(r).to_dict(),
                    "status": "pending", "offered": 0, "kind": "", "risk": list(r.risk)})
        next_id += 1
    for a in (extra_asks or [])[: max(0, MAX_NEW - len(out))]:
        out.append({"id": next_id, "batch": turn, "label": a.get("label", ""), "quote": a.get("quote", ""), "req": None,
                    "status": "pending", "offered": 0, "kind": "", "risk": []})
        next_id += 1
    if followup == "unrecognised_debit":
        req = Request("fraud", "Is there a payment I don't recognise?", {}, risk=["unrecognised_debit"])
        out.append({"id": next_id, "batch": turn, "label": "", "quote": "", "req": req.to_dict(), "status": "pending",
                    "offered": 0, "kind": "followup_unrecognised_debit", "risk": ["unrecognised_debit"], "department": "fraud"})
    state.agenda.extend(i for i in out if i not in state.agenda)
    _prune(state)
    return out


def _clean(r: Request) -> Request:
    return Request(**{**r.__dict__, "ref": 0})


def _prune(state) -> None:
    if len(state.agenda) <= MAX_KEPT:
        return
    settled = [i for i in state.agenda if i["status"] not in OPEN + (WAITING,)]
    for item in settled[: len(state.agenda) - MAX_KEPT]:
        state.agenda.remove(item)
    del state.agenda[: max(0, len(state.agenda) - MAX_KEPT)]


def from_open_requests(state) -> None:
    """A call saved before the agenda existed: its waiting requests become agenda items."""
    for n, d in enumerate(state.open_requests, start=1):
        state.agenda.append({"id": n, "batch": 0, "label": d.get("label", ""), "quote": "", "req": d, "status": "pending",
                             "offered": 0, "kind": "", "risk": list(d.get("risk") or [])})


def to_request(item: dict) -> Request:
    r = Request.from_dict(item["req"])
    r.ref, r.label, r.risk = item["id"], item.get("label") or r.label, list(item.get("risk") or r.risk)
    return r


# ───────── deciding what to do ─────────

def is_short(item: dict) -> bool:
    """A quick read that can share a reply with one other: no action, no risk, not a broad or comparison question."""
    r = item.get("req") or {}
    return (bool(r) and not item.get("kind") and not item.get("risk") and r.get("kind") in SHORT_KINDS
            and (r.get("scope") or "") in ("", "specific"))


def plan(items: list[dict]) -> list[dict]:
    """Which of these to handle now: one or two short reads together; otherwise only the first."""
    todo = [i for i in items if i["status"] in OPEN]
    if not todo:
        return []
    if len(todo) <= 2 and all(is_short(i) for i in todo):
        return todo
    return todo[:1]


def intro(items: list[dict], lang: str) -> str:
    """'You've asked about three things: ...' when a turn brings three or more."""
    todo = [i for i in items if i["status"] in OPEN]
    if len(todo) < 3:
        return ""
    labels = [label(i, lang) for i in todo]
    if len(todo) == 3:
        return say("intro3", lang, a=labels[0], b=labels[1], c=labels[2])
    counts = _COUNT.get(lang, _COUNT["en"])
    return say("intro_many", lang, n=counts.get(len(todo), str(len(todo))), a=labels[0])


def record(state, item_id: int, outcome: str) -> None:
    """What happened to one item this turn."""
    item = find(state, item_id)
    if item is None:
        return
    if outcome in DONE_OUTCOMES:
        item["status"] = "done"
    elif outcome == "cancelled":
        item["status"] = "declined"
    elif outcome == "auth_required":
        item["status"] = "auth"
    elif outcome in ("clarify", "confirm"):
        item["status"] = WAITING
    elif outcome == "handoff":
        item["status"] = "handed_over"
    elif outcome in FAILED_OUTCOMES:
        item["status"] = "failed"
    else:
        item["status"] = "done"


def settle(state, declined: bool = False) -> list[int]:
    """A new turn: what was waiting on the caller (shall I block it? which card?) has been answered or left; its own
    flow (journeys, the re-ask of a dropped action) carries it from here.
    A bank-data question waiting on "which account?" stays waiting: only this turn shows whether the reply answered it
    (`after_account_choice`). Before 23 Nov it was marked done here, so a question never answered counted as answered
    (calls a6545287, de568948). Returns the ids of those items."""
    kept = []
    for item in state.agenda:
        if item["status"] != WAITING:
            continue
        if (item.get("req") or {}).get("kind") == "personal" and not declined:
            kept.append(item["id"])
        else:
            item["status"] = "declined" if declined else "done"
    return kept


def after_account_choice(state, waited: list[int], resumed: bool, outcome: str) -> None:
    """End of the turn after "which account?". Answered (the reply named the account and the question resumed): done.
    Asked back again: still waiting. The caller moved on to something else: open again, so it is offered later
    ("Shall I go back to ...?") and the review counts it if it never gets answered."""
    for item_id in waited:
        item = find(state, item_id)
        if item is None or item["status"] != WAITING:
            continue
        if resumed and outcome in DONE_OUTCOMES:
            item["status"] = "done"
        elif not (resumed and outcome == "clarify"):
            item["status"] = "pending"


def hand_over(state) -> None:
    """A person is taking the call: whatever is still open goes to them (the desk brief lists it)."""
    for item in state.agenda:
        if item["status"] in OPEN + (WAITING,):
            item["status"] = "handed_over"


def stop(state) -> None:
    """'No' / 'that's all' to 'Shall I go on?': the rest are skipped."""
    for item in state.agenda:
        if item["status"] in OPEN:
            item["status"] = "skipped"


def skip(state, item_id: int) -> None:
    item = find(state, item_id)
    if item is not None and item["status"] in OPEN:
        item["status"] = "skipped"


def resume_after_verification(state) -> list[dict]:
    """After the keypad code: the items that waited for it, then the open items asked in the same turn."""
    waiting = [i for i in state.agenda if i["status"] == "auth"]
    if not waiting:
        return []
    turns = {i["batch"] for i in waiting}
    rest = [i for i in state.agenda if i["status"] == "pending" and i["batch"] in turns]
    return waiting + rest


def next_offer(state) -> dict | None:
    """The item to offer next: the latest turn's first, then older ones ("go back to"). One already offered
    `MAX_OFFERS` times without being taken up is let go."""
    for item in sorted(open_items(state), key=lambda i: (-i["batch"], i["id"])):
        if int(item.get("offered") or 0) >= MAX_OFFERS:
            item["status"] = "dropped"
            continue
        return item
    return None


def offer_text(state, item: dict, lang: str, bye: bool = False) -> str:
    if bye:
        return say("bye", lang, a=label(item, lang))
    if item.get("kind") == "followup_unrecognised_debit":
        return say("followup_unrecognised_debit", lang)
    return say("back" if item["batch"] < batch(state) else "next", lang, a=label(item, lang))


def offer_pending(state, item: dict, text: str) -> dict:
    """What the "Shall I go on?" question remembers (`traces.pending`, kind agenda_next): the item offered and the open
    items in the order they are listed to the LLM, so "the third one" maps back to an item in code."""
    item["offered"] = int(item.get("offered") or 0) + 1
    listed = sorted(open_items(state), key=lambda i: (-i["batch"], i["id"]))
    return {"item": item["id"], "order": [i["id"] for i in listed], "labels": [label(i, "en") for i in listed], "text": text}


def describe(p: dict) -> str:
    """The waiting question, for the understanding step (it fills `agenda_reply` / `agenda_pick` from this)."""
    listed = "; ".join(f"{n}) {text}" for n, text in enumerate(p.get("labels") or [], start=1))
    return f"{p.get('text', '')} Still to answer: {listed}" if listed else str(p.get("text", ""))


def picked(state, p: dict, n: int) -> dict | None:
    order = p.get("order") or []
    return find(state, order[n - 1]) if 1 <= n <= len(order) else None


def unresolved(state) -> list[str]:
    """Labels of what the caller asked and nobody answered or let go (for the review and the desk brief)."""
    return [label(i, "en") for i in state.agenda if i["status"] in OPEN and (i.get("req") or {}).get("kind") != "human"]


def label(item: dict, lang: str) -> str:
    if item.get("label"):
        return item["label"]
    r = item.get("req") or {}
    if item.get("kind") == "followup_unrecognised_debit" or "unrecognised_debit" in (item.get("risk") or []):
        key = "unrecognised_debit"
    elif r.get("kind") == "action" and (r.get("action") or {}).get("type") == "block":
        key = "block"
    elif r.get("kind") == "human":
        key = "human"
    else:
        key = (r.get("personal") or {}).get("topic") or "other"
    return _FALLBACK.get(key, _FALLBACK["other"])[_LANG.get(lang, 0)]
