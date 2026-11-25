"""Multi-step journeys: card actions, fraud, complaints and disputes, transfers to a person, new-customer leads.

Each journey ends a turn either with an answer or with a question waiting for a yes ("pending"): "Shall I block
card ending 6712?", "Shall I raise a complaint?", "Shall I connect you to our cards team?". The caller's reply to
that question is read in code (services/confirm.py), never guessed by the LLM, and only a clear yes acts.

Returns `Step`s (what to say + what happened); the conversation core turns them into events, traces and audit.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain import Customer
from app.services import actions as card_actions
from app.services import agenda
from app.services.audit import mask_number
from app.services.state import CallState
from app.services.confirm import block_preference, read_reply
from app.services.content import Setting, Table
from app.services.desk import DEPARTMENTS, TAT_DAYS, add_working_days, department_name
from app.services.personal import PersonalQuery, _duplicates, _txn_line, day, fill, money, note_text, period_range
from app.services.workflows import Candidate, find_candidates, recent_card_spend, recent_debits
from app.services.personal import answer as personal_answer

logger = logging.getLogger(__name__)
PENDING_TTL = timedelta(minutes=5)
_CREDIT = re.compile(r"credit|क्रेडिट|zenith|ananta|laksya", re.I)
_DEBIT = re.compile(r"debit|डेबिट|atm card|rupay|visa", re.I)


@dataclass
class Step:
    sentences: list[str]
    outcome: str  # answered | clarify | confirm | action_done | action_failed | cancelled | ticket | handoff | callback
    route: str | None = None
    data: dict = field(default_factory=dict)  # for the trace / audit (action result, ticket, handoff)
    handoff: dict | None = None  # {id, department, name}: the call now waits for a person
    pending: dict | None = None  # the question now waiting for an answer (None: nothing waiting)
    fields: list[str] = field(default_factory=list)  # account data disclosed


J = Table("journey")  # brain/content/catalog.json, "journey.*"


def j(key: str, lang: str, **values) -> str:
    return fill(J, key, lang, **values)


_REPEAT_ASKED = re.compile(r"\b(explain|repeat|again|dobara|phir se)\b|दोबारा|फिर से", re.I)


_PERMANENT = re.compile(r"\bperman\w*|\bfor good\b|\breplace\w*|\bnew card\b|naya card|hamesha|परमानेंट|स्थायी|हमेशा|नया कार्ड", re.I)
_TEMPORARY = re.compile(r"\btempor\w*|\btemp\b|for now|for a while|abhi ke liye|kuch (time|der|din)|टेम्पररी|अस्थायी|अभी के लिए|कुछ (समय|दिन)", re.I)


_REPLACEMENT = re.compile(r"\b(replace\w*|new card|another card)\b|naya card|नया कार्ड", re.I)
_CONNECT_ASKED = re.compile(r"\b(connect|transfer|put me through|speak to|talk to)\b|connect kar|बात करा|जोड़", re.I)


def pending(kind: str, lang: str, /, **data) -> dict:
    return {**data, "kind": kind, "lang": lang, "at": datetime.now().astimezone().isoformat()}


def expired(p: dict) -> bool:
    try:
        return datetime.now().astimezone() - datetime.fromisoformat(p["at"]) > PENDING_TTL
    except (KeyError, ValueError):
        return True


# issue type -> (department, ticket kind, ticket category, keyword to search transactions); the department is editable
ISSUES = Setting("issues", lambda r: (r["department"], r["ticket"], r["category"], r["search"]))


_ORDINALS = (("first", r"\b(first|1st|pehla|pehle|pahla)\b|पहला|पहले"), ("second", r"\b(second|2nd|dusra|doosra|dusre)\b|दूसरा|दूसरे"),
             ("third", r"\b(third|3rd|teesra|tisra)\b|तीसरा|तीसरे"))


def _pick_candidate(raw: str, cands: list[Candidate]) -> Candidate | None:
    """Which of the listed payments the caller means: "the second one", or the amount said. None if it can't tell."""
    low = raw.lower()
    for i, (_, pattern) in enumerate(_ORDINALS):
        if re.search(pattern, low) and i < len(cands):
            return cands[i]
    amounts = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", low) if n.replace(",", "").replace(".", "").isdigit()}
    hits = [c for c in cands if any(abs(c.amount - a) <= max(a * 0.02, 1) for a in amounts)]
    return hits[0] if len(hits) == 1 else None


class Journeys:
    def __init__(self, bank, auth, service_desk, handoffs, audit, settings):
        self.bank, self.auth, self.desk, self.handoffs, self.audit, self.s = bank, auth, service_desk, handoffs, audit, settings

    # ───────── answers to a waiting question ─────────

    def resolve(self, session_id: str, p: dict, raw: str, lang: str, channel: str) -> Step | None:
        """The caller's reply to a pending question. None = not an answer to it (carry on normally)."""
        reply = read_reply(raw)
        kind = p["kind"]
        if kind == "action" and (pref := block_preference(raw)) and p["plan"]["action"] in ("temp_block", "block"):
            customer = self.auth.verified_customer(session_id)
            if customer:  # "make it permanent" while confirming a temporary block: re-plan and confirm again
                plan, text, _ = card_actions.plan(customer, {"type": "block", "card": p["plan"]["kind"],
                                                             "card_last4": p["plan"]["last4"], "block": pref}, lang)
                if plan:
                    return Step([text], "confirm", pending={**p, "plan": plan.to_dict(), "question": text,
                                                            "at": datetime.now().astimezone().isoformat()})
        if kind == "fraud_pick":  # the answer to "which one is it?": an ordinal or the amount, read in code
            pick = _pick_candidate(raw, [Candidate(**c) for c in p["cands"]])
            customer = self.auth.verified_customer(session_id) if pick else None
            if pick and customer:
                return self._fraud_one(session_id, customer, pick, p["req"], lang)
            if reply == "question" and len(raw.split()) <= 15:  # "so my account is safe?": no promise, the fraud team offered
                return Step([j("fraud_safe_q", lang)], "confirm",
                            pending=pending("handoff", lang, department="fraud", reason="caller unsure about an unrecognised payment"))
            if not p.get("asked_more") and reply in ("unclear", "no") and len(raw.split()) <= 15:  # "not sure, maybe last week"
                return Step([j("fraud_pick_more", lang)], "clarify", pending={**p, "asked_more": True})
            return None
        if kind == "block_type":  # the answer to "temporarily, or permanently?", read in code
            block = "permanent" if _PERMANENT.search(raw) else "temporary" if _TEMPORARY.search(raw) else ""
            digits = re.search(r"(?<!\d)(\d{4})(?!\d)", raw)
            card = "credit" if _CREDIT.search(raw) else "debit" if _DEBIT.search(raw) else ""
            req = p["req"] | ({"card_last4": digits.group(1)} if digits else {}) | ({"card": card} if card else {})
            customer = self.auth.verified_customer(session_id) if block else None
            if customer:
                return self.action(session_id, customer, req | {"block": block}, lang, then=p.get("then") or None)
            if reply == "no" and len(raw.split()) <= 5:
                return Step([j("cancelled", lang)], "cancelled", data={"declined": kind})
            if not p.get("asked_again") and (digits or card or reply in ("unclear", "yes")) and len(raw.split()) <= 8:
                # "the credit card ending 6712": the card is kept, the choice is asked once more
                return Step([j("block_type_again", lang)], "clarify", pending={**p, "req": req, "asked_again": True})
            return None
        if kind == "action_target":  # the answer to "which card?": last digits or debit / credit, read in code
            digits = re.search(r"(?<!\d)(\d{4})(?!\d)", raw)
            card = "credit" if _CREDIT.search(raw) else "debit" if _DEBIT.search(raw) else ""
            if not digits and reply in ("yes", "no") and len(raw.split()) <= 3:  # "yes" names no card: ask again, never pick one
                customer = self.auth.verified_customer(session_id)
                return self.action(session_id, customer, p["req"], lang, then=p.get("then") or None) if customer else None
            customer = self.auth.verified_customer(session_id) if (digits or card) else None
            if customer:
                req = p["req"] | {"card_last4": digits.group(1) if digits else p["req"].get("card_last4", ""),
                                  "card": card or p["req"].get("card", "")}
                return self.action(session_id, customer, req, lang, then=p.get("then") or None)
            return None
        if reply == "no":
            if len(raw.split()) > 5:  # a "no" with a new request after it: drop the question, answer the request
                return None
            return Step([j("cancelled", lang)], "cancelled", data={"declined": kind})
        if reply == "question":
            if kind == "fraud_followup" and len(raw.split()) <= 15:  # "will I get the money back?": the offer stays
                again = "fraud_restate_ticket" if p.get("ticket") else "fraud_restate"
                said = "fraud_context" if _REPEAT_ASKED.search(raw) else "fraud_side_q"  # "do I need to explain it all again?"
                return Step([j(said, lang), j(again, lang)], "confirm", pending=p)
            if len(raw.split()) > 6:  # a new question, not "which card?": answer it instead
                return None
            if kind == "handoff":
                dept = p["department"]
                return Step([j("restate_transfer", lang, dept=department_name(dept, lang), what=DEPARTMENTS[dept][3])],
                            "confirm", pending=p)
            if kind == "action":
                said = []
                if _REPLACEMENT.search(raw) and p["plan"]["action"] in ("block", "temp_block"):  # "will I get a new card?"
                    said = [j("replacement_yes" if p["plan"]["action"] == "block" else "replacement_temp", lang)]
                return Step(said + [p["question"]], "confirm", pending=p)  # restate: it names the exact card
            return None
        if kind == "fraud_followup" and reply == "unclear" and not p.get("noted") and len(raw.split()) <= 25:
            # a detail ("someone called saying they were from the bank"): noted for the team, the offer asked again once
            again = "fraud_restate_ticket" if p.get("ticket") else "fraud_restate"
            return Step([j("fraud_noted", lang), j(again, lang)], "confirm", pending={**p, "noted": True})
        if reply != "yes":
            return None
        if kind == "more":
            return Step(p["lines"], "answered")
        customer = self.auth.verified_customer(session_id) if self.auth else None
        if kind == "action":
            done = self._do_action(session_id, customer, p, lang)
            if _CONNECT_ASKED.search(raw) and done.outcome in ("action_done", "confirm") and not done.handoff:
                dept = "credit_cards" if p["plan"].get("kind") == "credit" else "debit_cards"
                then = self.start_handoff(session_id, customer, dept, "caller asked for a person after a card action", lang, channel)
                return Step(done.sentences + then.sentences, then.outcome, route=then.route, data={**done.data, **then.data},
                            handoff=then.handoff)
            return done
        if kind == "ticket":
            return self._raise(session_id, customer, p, lang)
        if kind == "fraud_followup":
            ticket = self._raise(session_id, customer, p, lang) if p.get("ticket") else None
            step = self.start_handoff(session_id, customer, "fraud", p.get("reason", "suspected fraud"), lang, channel)
            if ticket:
                step.sentences = ticket.sentences[:1] + step.sentences
                step.data["ticket"] = ticket.data.get("ticket")
            return step
        if kind == "handoff":
            return self.start_handoff(session_id, customer, p["department"], p.get("reason", "caller asked"), lang, channel)
        if kind == "lead":
            return self._lead(session_id, p, lang)
        return None

    def open_request_note(self, customer: Customer, lang: str) -> str:
        """Long-term memory, kept small: an open request from the last 30 days is only ASKED about, naming the kind and the
        topic and nothing else, until the caller says it is what they are calling about. Never assumed."""
        try:
            rows = self.desk.open_requests(customer.cif, 30)
        except Exception:  # a memory problem must never get in the way of verification
            logger.exception("open requests lookup failed")
            return ""
        if not rows:
            return ""
        kinds = {"complaint": ("complaint", "शिकायत", "complaint"), "dispute": ("dispute", "विवाद", "dispute"),
                 "service_request": ("request", "अनुरोध", "request")}
        i = {"en": 0, "hi": 1, "hinglish": 2}.get(lang, 0)
        topic = rows[0]["category"].replace("_", " ")
        return j("open_request", lang, kind=kinds.get(rows[0]["kind"], kinds["service_request"])[i], topic=topic)

    def offer_pending(self, session_id: str, customer: Customer, offer: dict, lang: str) -> dict | None:
        """The waiting question behind an offer made at the end of an answer."""
        if "handoff" in offer:
            return pending("handoff", lang, department=offer["handoff"], reason="offered after an answer")
        if "ticket" in offer:
            dept, kind, category, _ = {"charge_review": ISSUES["charge_review"],
                                       "double_charge": ISSUES["double_charge"]}.get(offer["ticket"], ISSUES["other"])
            return pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                           summary=f"Caller asked for a {category.replace('_', ' ')} after seeing their account")
        if "more" in offer:  # the rest of a list read three at a time
            return pending("more", lang, lines=offer["more"])
        if "action" in offer:
            plan, text, _ = card_actions.plan(customer, offer["action"], lang)
            return pending("action", lang, plan=plan.to_dict(), question=text, then={}) if plan else None
        return None

    # ───────── card actions ─────────

    def action(self, session_id: str, customer: Customer, req: dict, lang: str, then: dict | None = None) -> Step:
        if req.get("ask_only"):  # "can I unblock it tomorrow?": answered, nothing is offered for now
            return Step([j("action_later_unblock" if req.get("type") == "unblock" else "action_later", lang)], "answered")
        if req.get("no_block") and req.get("type") == "block":  # "don't block it yet, just connect me to fraud": no block
            return Step([j("lost_no_block", lang)], "confirm",
                        pending=pending("handoff", lang, department="fraud", reason="card out of the caller's hands; caller asked not to block it yet"))
        if req.get("later"):  # "unblock it tomorrow at 10 AM": not something we can book; the caller decides about now
            step = self.action(session_id, customer, {k: v for k, v in req.items() if k != "later"}, lang, then)
            if step.outcome == "confirm" and step.pending:
                return Step([j("action_scheduled_no", lang)], "confirm", pending=step.pending)
            return step
        if req.get("type") in ("other", "", "none"):
            dept = req.get("department") or "general"
            return self.offer_transfer(dept, lang, "a request the agent can't do on a call", text_key="not_allowed")
        if req.get("type") == "block" and not req.get("block"):
            # "I want to block my card": temporary or permanent is the caller's choice, asked before anything is offered
            # (PM review, 17 Nov). A lost or stolen card goes through the fraud journey instead, which blocks for good.
            return Step([j("block_type_q", lang)], "clarify", pending=pending("block_type", lang, req=req, then=then or {}))
        plan, text, needs_card = card_actions.plan(customer, req, lang)
        if plan:
            if plan.action == "unblock" and req.get("type") == "controls" and not then:
                then = {"kind": "controls_after_unblock", "req": req}  # the settings change the caller asked for comes next
            return Step([text], "confirm", pending=pending("action", lang, plan=plan.to_dict(), question=text, then=then or {}))
        if needs_card and text:
            return Step([text], "clarify", pending=pending("action_target", lang, req=req, then=then or {}))
        if not text:  # never an empty reply: nothing to change was understood, so ask what to change
            return Step([j("which_setting", lang)], "clarify")
        return Step([text], "answered")

    def _do_action(self, session_id: str, customer: Customer | None, p: dict, lang: str) -> Step:
        if customer is None:  # verification expired while waiting
            return Step([j("cancelled", lang)], "cancelled", data={"reason": "verification expired"})
        plan = card_actions.CardAction(**p["plan"])
        done, text, record = card_actions.execute(self.bank, customer, plan, lang)
        self.auth.invalidate(session_id)  # the next read must see the change
        data = {"action": plan.to_dict(), "result": record, "done": done}
        self.audit.event(session_id, "card_action", action=plan.action, card=f"{plan.kind} ending {plan.last4}",
                         changes=plan.changes, done=done, reference=record.get("reference"), error=record.get("error"))
        if not done:
            dept = "credit_cards" if plan.kind == "credit" else "debit_cards"
            return Step([text], "action_failed", data=data, pending=pending("handoff", lang, department=dept,
                                                                            reason=f"card {plan.action} failed in the system"))
        then = p.get("then") or {}
        if done and then.get("kind") == "controls_after_unblock":  # found by the judge: the unblock alone left the request undone
            fresh = self.auth.verified_customer(session_id)
            nxt = self.action(session_id, fresh, then["req"], lang) if fresh else None
            if nxt and nxt.pending:
                return Step([text] + nxt.sentences, "confirm", data=data, pending=nxt.pending)
        if then.get("kind") == "fraud_followup":
            if then.get("lost"):  # a lost card: what was spent on it lately?
                recent = recent_card_spend(customer, plan.kind, plan.last4, self.s.demo_today)
                follow = j("lost_recent", lang, n=len(recent), amt=money(recent[0].amount), who=recent[0].who) if recent \
                    else j("lost_none", lang)
            else:
                follow = j("fraud_followup", lang, what=then["what"]) if then.get("what") else j("fraud_followup_generic", lang)
            return Step([text, j("fraud_advice", lang), follow], "action_done", data=data,
                        pending=pending("fraud_followup", lang, **{k: v for k, v in then.items() if k != "kind"}))
        if plan.kind == "debit" and not then:  # the problem the caller called about is not left behind (live test cfdda8da)
            fresh = self.auth.verified_customer(session_id)
            card = next((d for d in (fresh.debit_cards if fresh else []) if d.last4 == plan.last4), None)
            if card and card.status == "active" and not card.pos and self.auth.was_read(session_id, "debit_card:declines"):
                nxt = self.action(session_id, fresh, {"type": "controls", "card": "debit", "card_last4": card.last4, "block": "",
                                                      "pos": "on", "atm": "", "ecom": "", "contactless": "", "international": ""}, lang)
                if nxt.pending:
                    return Step([text, j("pos_still_off", lang)] + nxt.sentences, "confirm", data=data, pending=nxt.pending)
        return Step([text], "action_done", data=data)

    # ───────── fraud ─────────

    def fraud(self, session_id: str, customer: Customer, req: dict, q: PersonalQuery, lang: str) -> Step:
        """Protect first. A lost or stolen card is blocked (after one yes); an unrecognised debit is looked up from what the
        caller said and never guessed; then a dispute or fraud complaint and the fraud team."""
        risk = req.get("risk") or []
        opening = self._opening(req, lang)
        if "lost_card" in risk and not q.amount:
            if req.get("no_block"):  # "stolen, but don't block it": nothing is blocked or offered for blocking
                return Step(opening + [j("lost_no_block", lang)], "confirm",
                            pending=pending("fraud_followup", lang, what="", reason="stolen card; caller declined a block", ticket=None))
            return self._lost_card(session_id, customer, req, lang)
        if "otp_shared" in risk and not (q.amount or q.keyword or q.period):
            return self._otp_shared(customer, req, lang, opening)
        found = find_candidates(customer, q, self.s.demo_today)
        found = [c for c in found if c.status != "failed"]
        if len(found) > 1 and not (req.get("card") or req.get("card_last4")):
            shown = found[:3]
            listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
            return Step(opening + [j("fraud_many", lang, n=len(shown), list=listing)], "clarify",
                        pending=pending("fraud_pick", lang, cands=[c.to_dict() for c in shown], req=req | {"opened": True}))
        if found:
            return self._fraud_one(session_id, customer, found[0], req, lang)
        if req.get("card") or req.get("card_last4"):  # no matching payment, but they named the card: protect it anyway
            return self._protect_card(session_id, customer, req, lang, opening + [j("txn_not_found", lang)], "", None)
        if not (q.amount or q.keyword or q.period):  # nothing to match on: show the latest debits and ask (never guess)
            shown = recent_debits(customer, self.s.demo_today, days=7)[:3]
            if shown:
                listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
                return Step(opening + [j("fraud_recent", lang, list=listing)], "clarify", fields=["account:transactions"],
                            pending=pending("fraud_pick", lang, cands=[c.to_dict() for c in shown], req=req | {"opened": True}))
            return Step(opening + [j("fraud_no_recent", lang)], "clarify", fields=["account:transactions"])
        return Step(opening + [j("fraud_none", lang)], "clarify")

    def _opening(self, req: dict, lang: str) -> list[str]:
        """The first line of a fraud report, unless it was already said before verification (never twice)."""
        if req.get("opened"):
            return []
        risk = req.get("risk") or []
        key = next((f"fraud_open_{r}" for r in ("otp_shared", "lost_card", "unrecognised_debit") if r in risk), "fraud_sorry")
        return [j(key, lang)]

    def _otp_shared(self, customer: Customer, req: dict, lang: str, opening: list[str]) -> Step:
        """"I shared my OTP": do not hunt for one payment. Say what went out lately, then get the fraud team to secure
        everything (net banking, UPI, cards) - the caller can still say no."""
        shown = recent_debits(customer, self.s.demo_today, days=2)[:3]
        listing = "; ".join(f"₹{money(c.amount)} {c.who}, {day(c.date, lang)}" for c in shown)
        seen = j("otp_recent", lang, list=listing) if shown else j("otp_none", lang)
        return Step(opening + [seen, j("otp_next", lang)], "confirm", fields=["account:transactions"],
                    pending=pending("fraud_followup", lang, what="", reason="caller shared an OTP / PIN", ticket=None))

    def fraud_bank_down(self, lang: str) -> Step:
        """The bank's systems cannot be read: say so, and still get the caller to the fraud team."""
        return Step([j("fraud_bank_down", lang)], "confirm",
                    pending=pending("handoff", lang, department="fraud", reason="suspected fraud; bank systems unavailable"))

    def _fraud_one(self, session_id: str, customer: Customer, cand: Candidate, req: dict, lang: str) -> Step:
        what = f"₹{money(cand.amount)} ({cand.who})"
        card = card_actions.card_label(cand.card_kind, "", cand.card_last4, lang) if cand.card_kind else ""
        seen = j("fraud_seen", lang, what=j("fraud_txn", lang, amt=money(cand.amount), who=cand.who, date=day(cand.date, lang), card=card)) \
            if card else j("fraud_seen", lang, what=f"₹{money(cand.amount)} {cand.who}, {day(cand.date, lang)}")
        lines = self._opening(req, lang) + [seen]
        if cand.status == "reversed":
            return Step(lines + [j("fraud_reversed", lang)], "answered", fields=["account:transactions"])
        if cand.status == "pending":
            lines.append(j("fraud_pending", lang))
        if cand.is_card_payment:  # a card payment: block the card, then dispute + fraud team
            then = {"kind": "fraud_followup", "what": what, "reason": f"suspected fraud: {what}",
                    "ticket": {"kind": "dispute", "category": "unrecognised_transaction", "department": "fraud",
                               "summary": f"Caller reports an unrecognised card transaction {what}"}}
            return self._protect_card(session_id, customer, req | {"card": cand.card_kind, "card_last4": cand.card_last4}, lang,
                                      lines, what, then)
        # UPI / IMPS / NEFT: the card is not involved, so nothing is blocked; a fraud complaint, 1930 and the fraud team
        ticket = {"kind": "complaint", "category": "unrecognised_transaction", "department": "fraud",
                  "summary": f"Caller reports an unrecognised {cand.channel} transfer {what}, ref {cand.reference}",
                  "detail": {"reference": cand.reference, "amount": cand.amount, "status": cand.status}}
        return Step(lines + [j("fraud_upi_followup", lang)], "confirm", fields=["account:transactions"],
                    pending=pending("fraud_followup", lang, what=what, reason=f"suspected fraud: {what}", ticket=ticket))

    def _protect_card(self, session_id: str, customer: Customer, req: dict, lang: str, lines: list[str], what: str,
                      then: dict | None) -> Step:
        then = then or {"kind": "fraud_followup", "what": what, "reason": "suspected fraud", "ticket": None}
        step = self.action(session_id, customer, {"type": "block", "card": req.get("card") or "", "card_last4": req.get("card_last4") or "",
                                                  "block": req.get("block") or "permanent"}, lang, then=then)
        if step.outcome == "answered":  # already blocked: go straight to the dispute / fraud team
            follow = j("fraud_followup", lang, what=what) if what else j("fraud_followup_generic", lang)
            return Step(lines + step.sentences + [follow], "confirm",
                        pending=pending("fraud_followup", lang, **{k: v for k, v in then.items() if k != "kind"}))
        step.sentences = lines + step.sentences
        return step

    def _lost_card(self, session_id: str, customer: Customer, req: dict, lang: str) -> Step:
        """"I lost my card": one clear yes to the block (lost or stolen = permanent, misplaced = temporary), then what was
        spent on it lately, a dispute for anything unrecognised, and the replacement that comes with a permanent block."""
        then = {"kind": "fraud_followup", "what": "", "reason": "lost or stolen card", "ticket": None, "lost": True}
        return self._protect_card(session_id, customer, req, lang, self._opening(req, lang), "", then)

    def _find_account_txn(self, c: Customer, q: PersonalQuery, keyword: str = ""):
        """The newest account debit matching what the caller said, as ("debit", transaction, debit card). None if they gave
        nothing to match (never the latest by default)."""
        span = period_range(q.period, self.s.demo_today)
        key = (keyword or q.keyword or "").lower()
        if not (q.amount or span or key):
            return None
        best = None
        for a in c.accounts:
            for x in a.transactions:
                if x.kind != "Debit" or (q.amount and abs(x.amount - q.amount) > max(q.amount * 0.1, 1)):
                    continue
                if span and not span[0] <= x.date <= span[1]:
                    continue
                if key and key not in f"{x.description} {x.counterparty} {x.channel}".lower():
                    continue
                if best is None or x.date > best[1].date:
                    best = ("debit", x, a.debit_card)
        return best

    # ───────── complaints and disputes ─────────

    def issue(self, session_id: str, customer: Customer, issue_type: str, q: PersonalQuery, lang: str) -> Step:
        dept, kind, category, keyword = ISSUES.get(issue_type, ISSUES["other"])
        due = day(self._due(category), lang)
        offer_key = {"dispute": "kind_dispute", "service_request": "kind_review"}.get(kind, "kind_complaint")
        offer = j("offer_ticket", lang, kind=j(offer_key, lang), dept=department_name(dept, lang), date=due)
        if issue_type == "double_charge":
            found = _duplicates(customer, lang, self.s.demo_today)
            lines = found.sentences[:1]
            summary = "Caller reports a double charge" + (f": {lines[0]}" if found.outcome == "answered" else "")
            return Step(lines + [offer], "confirm", fields=found.fields,
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept, summary=summary))
        if issue_type == "charge_review":
            charges = personal_answer(customer, PersonalQuery(topic="charges", detail="charges", account=q.account or "all"),
                                      lang, self.s.demo_today)
            lines = [s for s in charges.sentences if "review" not in s and "जाँच" not in s][:3]
            return Step(lines + [offer], "confirm", fields=charges.fields,
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary="Caller asks for a charge to be reviewed"))
        if issue_type == "service_complaint" or issue_type == "other":
            return Step([j("sorry", lang), offer], "confirm",
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary=f"Service complaint: {q.keyword or 'see call record'}"))
        found = self._find_account_txn(customer, q, keyword=keyword)
        if not found and not (q.amount or q.period):
            return Step([j("issue_details", lang)], "clarify")
        if not found:
            return Step([j("txn_not_found", lang), offer], "confirm",
                        pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                        summary=f"{category.replace('_', ' ')}: transaction not found in recent activity"))
        _, x, _card = found
        lines = [_txn_line(x, lang)]
        if note := note_text(x, lang):
            lines.append(note)
        return Step(lines + [offer], "confirm", fields=["account:transactions"],
                    pending=pending("ticket", lang, ticket_kind=kind, category=category, department=dept,
                                    summary=f"{category.replace('_', ' ')}: ₹{money(x.amount)} {x.channel} on {x.date} "
                                            f"({x.counterparty or x.description}), status {x.status}, ref {x.reference}",
                                    detail={"reference": x.reference, "amount": x.amount, "status": x.status}))

    def _due(self, category: str):
        return add_working_days(self.s.demo_today, TAT_DAYS.get(category, 7))

    def _raise(self, session_id: str, customer: Customer | None, p: dict, lang: str) -> Step:
        spec = p.get("ticket") or p
        category = spec.get("category", "service_complaint")
        kind = spec.get("ticket_kind") or spec.get("kind", "complaint")
        ticket = self.desk.create_ticket(session_id=session_id, cif=customer.cif if customer else None, kind=kind,
                                         category=category, department=spec.get("department", "general"),
                                         summary=spec.get("summary", ""), detail=spec.get("detail") or {},
                                         tat_days=TAT_DAYS.get(category, 7))
        self.audit.event(session_id, "ticket", id=ticket["id"], kind=kind, category=category,
                         department=ticket["department"], due_by=str(ticket["due_by"]))
        text = j("ticket_done", lang, ref=ticket["id"], dept=department_name(ticket["department"], lang), date=day(ticket["due_by"], lang))
        return Step([text], "ticket", data={"ticket": ticket})

    # ───────── transfers to a person ─────────

    def offer_transfer(self, department: str, lang: str, reason: str, text_key: str = "offer_transfer",
                       customer: Customer | None = None) -> Step:
        department = department if department in DEPARTMENTS else "general"
        if customer and customer.segment == "priority" and text_key == "ask_transfer" and customer.rm_name:
            return Step([j("ask_transfer_rm", lang, rm=customer.rm_name)], "confirm",
                        pending=pending("handoff", lang, department="priority", reason=reason))
        return Step([j(text_key, lang, dept=department_name(department, lang))], "confirm",
                    pending=pending("handoff", lang, department=department, reason=reason))

    def start_handoff(self, session_id: str, customer: Customer | None, department: str, reason: str, lang: str,
                      channel: str) -> Step:
        department = department if department in DEPARTMENTS else "general"
        name = department_name(department, lang)
        if not self.handoffs.is_open(department):
            ticket = self.desk.create_ticket(session_id=session_id, cif=customer.cif if customer else None, kind="callback",
                                             category="callback", department=department, summary=f"Callback: {reason}",
                                             detail={}, tat_days=1)
            self.audit.event(session_id, "ticket", id=ticket["id"], kind="callback", department=department)
            return Step([j("after_hours", lang, dept=name, hours=self.s.desk_hours.replace("-", " to "), ref=ticket["id"])],
                        "callback", data={"ticket": ticket})
        note = self.handoff_note(session_id, customer, department, reason, lang)
        h = self.handoffs.create(session_id, department, reason, note, channel)
        self.audit.event(session_id, "handoff_requested", department=department, reason=reason, handoff=h["id"],
                         priority=h["priority"])
        return Step([j("connecting", lang, dept=name)], "handoff", route="handoff",
                    data={"handoff": h}, handoff={"id": h["id"], "department": department, "name": DEPARTMENTS[department][0]})

    def handoff_note(self, session_id: str, customer: Customer | None, department: str, reason: str, lang: str) -> dict:
        """The brief the person sees before picking up: built from the record, not written by the AI."""
        with self.audit.db.connection() as conn:
            s = conn.execute("SELECT caller_number, verified_at, facts, state FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
            turns = conn.execute("SELECT question, answer, intent, outcome FROM traces WHERE session_id = %s "
                                 "ORDER BY created_at DESC LIMIT 4", (session_id,)).fetchall()
            done = conn.execute("SELECT kind, detail FROM call_events WHERE session_id = %s AND kind IN ('card_action', 'ticket') "
                                "ORDER BY created_at", (session_id,)).fetchall()
            alerts = conn.execute("SELECT message FROM alerts WHERE session_id = %s", (session_id,)).fetchall()
        actions = []
        for e in done:
            d = e["detail"]
            actions.append(f"{d.get('action', '').replace('_', ' ')} {d.get('card', '')}: "
                           f"{'done, ref ' + str(d.get('reference')) if d.get('done') else 'failed'}" if e["kind"] == "card_action"
                           else f"ticket {d.get('id')} ({d.get('category', d.get('kind'))}), due {d.get('due_by', '')}")
        asked = [t["question"] for t in reversed(turns)]
        left = agenda.unresolved(CallState.from_dict(s.get("state")))  # asked on this call and not answered yet
        who = f"{customer.name} ({customer.segment})" if customer else "Caller (not verified)"
        summary = (f"{who} needs the {DEPARTMENTS[department][0]} team: {reason}. "
                   + (f"Last asked: \"{asked[-1]}\". " if asked else "")
                   + (f"Already done: {'; '.join(actions)}. " if actions else "Nothing has been changed on the account. ")
                   + (f"Still to answer: {'; '.join(left)}. " if left else ""))
        headline = f"{reason[:1].upper()}{reason[1:]}" + (f"; done: {actions[-1]}" if actions else "; nothing changed on the account yet")
        return {"headline": headline, "summary": summary, "reason": reason, "department": DEPARTMENTS[department][0], "language": lang,
                "verified": bool(customer), "verified_at": s.get("verified_at"), "customer": customer.name if customer else None,
                "cif": customer.cif if customer else None, "segment": customer.segment if customer else None,
                "rm": customer.rm_name if customer else None, "caller": mask_number(s.get("caller_number")),
                "asked": asked[-3:], "done": actions, "still_to_answer": left, "facts": s.get("facts") or {},
                "alerts": [a["message"] for a in alerts]}

    def handoff_timeout(self, session_id: str, handoff_id: str, lang: str) -> str:
        """Nobody picked up in time: book a callback instead, and say so."""
        h = self.handoffs.get(handoff_id)
        department = h["department"] if h else "general"
        with self.audit.db.connection() as conn:
            row = conn.execute("SELECT customer_id FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
        ticket = self.desk.create_ticket(session_id=session_id, cif=row.get("customer_id"), kind="callback", category="callback",
                                         department=department, summary=f"Callback: nobody free ({h['reason'] if h else ''})",
                                         detail={"handoff": handoff_id}, tat_days=1)
        self.handoffs.set_status(handoff_id, "callback", disposition=f"callback {ticket['id']}")
        self.audit.event(session_id, "ticket", id=ticket["id"], kind="callback", department=department)
        return j("busy_callback", lang, dept=department_name(department, lang), ref=ticket["id"], date=day(ticket["due_by"], lang))

    # ───────── new customers ─────────

    def lead_offer(self, lang: str, apply: bool = False) -> Step:
        """A callback from the sales team: to open an account, or (apply=True) to apply for a card or a loan."""
        return Step([j("lead_offer_apply" if apply else "lead_offer", lang)], "confirm", pending=pending("lead", lang))

    def _lead(self, session_id: str, p: dict, lang: str) -> Step:
        with self.audit.db.connection() as conn:
            row = conn.execute("SELECT caller_number, facts FROM sessions WHERE id = %s", (session_id,)).fetchone() or {}
        if not row.get("caller_number"):
            return Step([j("lead_no_number", lang)], "answered")
        ticket = self.desk.create_ticket(session_id=session_id, cif=None, kind="lead", category="lead", department="sales",
                                         summary="New customer would like help opening an account",
                                         detail={"interest": (row.get("facts") or {}).get("product", ""), "consent": "callback"},
                                         tat_days=1)
        self.audit.event(session_id, "ticket", id=ticket["id"], kind="lead", department="sales")
        return Step([j("lead_done", lang, date=day(ticket["due_by"], lang), ref=ticket["id"])], "ticket", data={"ticket": ticket})
