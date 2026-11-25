"""The call agenda (19 Nov 2026): several questions in one turn, answered part by part, nothing lost.

Pure rules first (services/agenda.py, no database), then the checks on the LLM's list (understanding.read_asks /
coverage_gap), the versioned state save, and whole conversations through the API with the fake LLM.
"""

import re

from app.domain import Request, Understanding
from app.services import agenda
from app.services.review import build_packet, code_checks
from app.services.state import CallState, merge
from app.services.understanding import asked_only, coverage_gap, read_asks

RAVI = "9000000101"


def _state(*requests: Request, extra: list[dict] | None = None, followup: str = "") -> tuple[CallState, list[dict]]:
    s = CallState()
    return s, agenda.add(s, list(requests), extra, followup)


def _balance(account: str = "all") -> Request:
    return Request("personal", "What is my balance?", {"topic": "balance", "account": account})


# ───────── the rules, in code ─────────

def test_one_or_two_short_reads_are_answered_together_more_are_taken_one_by_one():
    _, two = _state(_balance(), Request("personal", "my transactions", {"topic": "transactions"}))
    assert [i["id"] for i in agenda.plan(two)] == [1, 2] and agenda.intro(two, "en") == ""
    _, three = _state(_balance(), Request("personal", "my transactions", {"topic": "transactions"}),
                      Request("personal", "my EMI", {"topic": "loan"}))
    assert [i["id"] for i in agenda.plan(three)] == [1]
    assert agenda.intro(three, "en") == ("You've asked about three things: your balance, your transactions and your loan. "
                                         "Let me take them one by one.")
    _, action = _state(Request("action", "block my card", action={"type": "block"}), _balance())
    assert [i["id"] for i in agenda.plan(action)] == [1]  # a card action is never batched with another
    _, broad = _state(Request("general", "tell me about Zenith+", scope="overview"), _balance())
    assert [i["id"] for i in agenda.plan(broad)] == [1]  # a long answer goes alone


def test_five_questions_beyond_the_three_detailed_are_kept_and_introduced_in_every_language():
    extra = [{"quote": "what is my FD rate", "type": "question", "label": "your FD rate"},
             {"quote": "and my card limit", "type": "question", "label": "your card limit"}]
    s, items = _state(_balance(), Request("personal", "t", {"topic": "transactions"}), Request("personal", "e", {"topic": "loan"}),
                      extra=extra)
    assert len(items) == 5 and items[3]["req"] is None and items[3]["quote"] == "what is my FD rate"
    assert agenda.intro(items, "en").startswith("You've asked about five things. Let me take them one at a time, starting with your balance")
    assert "पाँच" in agenda.intro(items, "hi") and "paanch" in agenda.intro(items, "hinglish")


def test_offers_go_to_the_latest_question_first_then_back_and_let_go_after_two():
    s, first = _state(_balance(), Request("personal", "t", {"topic": "transactions"}), Request("personal", "e", {"topic": "loan"}))
    agenda.record(s, 1, "answered")
    offer = agenda.next_offer(s)
    assert offer["id"] == 2 and agenda.offer_text(s, offer, "en") == "Shall I go on to your transactions?"
    p = agenda.offer_pending(s, offer, "x")
    assert p["order"] == [2, 3] and agenda.picked(s, p, 2)["id"] == 3
    assert agenda.describe(p | {"text": "Shall I go on?"}) == "Shall I go on? Still to answer: 1) your transactions; 2) your loan"
    agenda.add(s, [Request("general", "what is the FD rate?")])  # a new question in the middle
    agenda.record(s, 4, "answered")
    back = agenda.next_offer(s)
    assert back["id"] == 2 and agenda.offer_text(s, back, "en") == "Shall I go back to your transactions?"
    agenda.offer_pending(s, back, "x")  # offered twice now, never taken up
    assert agenda.next_offer(s)["id"] == 3 and agenda.find(s, 2)["status"] == "dropped"


def test_stop_skip_settle_hand_over_and_what_is_left():
    s, _ = _state(_balance(), Request("personal", "t", {"topic": "transactions"}), Request("human", "connect me"))
    agenda.skip(s, 1)
    assert agenda.find(s, 1)["status"] == "skipped" and agenda.unresolved(s) == ["your transactions"]  # a person isn't "left"
    agenda.record(s, 2, "clarify")  # "which account?"
    assert agenda.settle(s) == [2] and agenda.find(s, 2)["status"] == "waiting"  # only this turn shows if it was answered
    agenda.after_account_choice(s, [2], resumed=True, outcome="answered")  # the reply named the account
    assert agenda.find(s, 2)["status"] == "done"
    agenda.record(s, 2, "clarify")
    agenda.after_account_choice(s, agenda.settle(s), resumed=False, outcome="answered")  # moved on (calls a6545287, de568948)
    assert agenda.find(s, 2)["status"] == "pending" and agenda.next_offer(s)["id"] == 2  # offered again, counted if never answered
    s2, _ = _state(_balance(), Request("personal", "t", {"topic": "transactions"}))
    agenda.record(s2, 1, "confirm")
    agenda.settle(s2, declined=True)
    agenda.stop(s2)
    assert [i["status"] for i in s2.agenda] == ["declined", "skipped"]
    s3, _ = _state(_balance(), Request("personal", "t", {"topic": "transactions"}))
    agenda.hand_over(s3)
    assert {i["status"] for i in s3.agenda} == {"handed_over"} and agenda.unresolved(s3) == []


def test_after_the_code_the_waiting_items_resume_with_the_rest_of_their_turn():
    s, _ = _state(Request("action", "block", action={"type": "block"}), _balance())
    agenda.record(s, 1, "auth_required")
    assert [i["id"] for i in agenda.resume_after_verification(s)] == [1, 2]
    agenda.add(s, [Request("general", "fd rate")])  # a later turn's question does not ride along
    assert [i["id"] for i in agenda.resume_after_verification(s)] == [1, 2]


def test_the_same_request_asked_again_is_not_added_twice():
    s, _ = _state(_balance())
    agenda.record(s, 1, "auth_required")
    again = agenda.add(s, [_balance()])
    assert [i["id"] for i in again] == [1] and len(s.agenda) == 1 and again[0]["batch"] == 2
    assert len(agenda.add(s, [_balance("4521")])) == 1 and len(s.agenda) == 2  # another account is another question


def test_the_agenda_stays_small_however_long_the_call():
    s = CallState()
    for n in range(40):
        agenda.add(s, [Request("general", f"question {n}")])
        agenda.record(s, s.agenda[-1]["id"], "answered")
    assert len(s.agenda) == agenda.MAX_KEPT


def test_money_deducted_with_a_balance_asks_about_an_unrecognised_payment_after_the_balance():
    u = Understanding("en", "personal", "Check my account balance", "",
                      requests=[Request("personal", "Check my account balance", {"topic": "balance"})])
    u = asked_only(u, "Money got deducted, check my account balance")
    assert [r.kind for r in u.requests] == ["personal"] and u.followup == "unrecognised_debit"
    s, items = _state(*u.requests, followup=u.followup)
    assert [i["id"] for i in agenda.plan(items)] == [1]  # the balance first
    agenda.record(s, 1, "answered")
    offer = agenda.next_offer(s)
    assert offer["req"]["kind"] == "fraud" and offer["risk"] == ["unrecognised_debit"]
    assert "don't recognise" in agenda.offer_text(s, offer, "en")
    alone = asked_only(Understanding("en", "general", "x", "", requests=[Request("general", "x")]), "Money got deducted")
    assert [r.kind for r in alone.requests][-1] == "issue" and not alone.followup  # without a balance: as before


# ───────── checking the LLM's list in code (any language) ─────────

def test_quotes_must_be_the_callers_words_and_labels_carry_no_new_digits():
    said = "Block my card ending 6712 and tell me my balance"
    asks = read_asks([{"quote": "Block my card ending 6712", "type": "question", "label": "blocking your card ending 6712"},
                      {"quote": "what is my FD rate", "type": "question", "label": "your FD rate"},  # never said
                      {"quote": "tell me my balance", "type": "question", "label": "your balance of 99,000"}], said)
    assert [a["quote"] for a in asks] == ["Block my card ending 6712", "tell me my balance"]
    assert asks[0]["label"] == "blocking your card ending 6712" and asks[1]["label"] == ""  # 99,000 was never said


def test_coverage_finds_missed_words_in_hindi_and_english_alike():
    hindi = "मेरा बैलेंस बताइए और मेरी पिछली पाँच ट्रांज़ैक्शन भी दिखाइए और मेरी अगली किस्त कब है"
    only_first = [{"quote": "मेरा बैलेंस बताइए", "type": "question", "label": ""}]
    assert coverage_gap(hindi, only_first)  # two questions' words are in no quote
    all_three = only_first + [{"quote": "मेरी पिछली पाँच ट्रांज़ैक्शन भी दिखाइए", "type": "question", "label": ""},
                              {"quote": "और मेरी अगली किस्त कब है", "type": "question", "label": ""}]
    assert coverage_gap(hindi, all_three) == ""
    assert coverage_gap("hi, what is my balance please", [{"quote": "what is my balance", "type": "question", "label": ""}]) == ""
    assert coverage_gap("what is my balance in both accounts", []) == ""  # no list: nothing to check against


# ───────── the versioned save ─────────

def test_two_turns_saving_at_once_keep_both_turns_work(client, container):
    sid = client.post("/api/v1/sessions", json={"caller_number": RAVI}).json()["id"]
    store = container.conversation.states
    a, b = store.load(sid), store.load(sid)
    agenda.add(a, [_balance()])
    assert store.save(sid, a) and a.v == 1
    agenda.add(b, [Request("general", "fd rate")])  # b read version 0: its save clashes, is merged, then saved
    b.agenda[-1]["id"] = 2
    agenda.record(b, 2, "answered")
    assert store.save(sid, b) and b.v == 2
    saved = store.load(sid)
    assert [(i["id"], i["status"]) for i in saved.agenda] == [(1, "pending"), (2, "done")] and saved.v == 2
    late = CallState(agenda=[{**saved.agenda[0], "status": "pending"}], v=0)
    settled = CallState(agenda=[{**saved.agenda[0], "status": "done"}], v=5)
    assert merge(late, settled).agenda[0]["status"] == "done" and late.v == 5  # a settled item is never reopened


def test_review_flags_a_question_left_unanswered():
    call = {"turns": [], "events": [], "alerts": [], "auth_events": [],
            "agenda": [{"label": "your FD", "status": "pending"}, {"label": "your loan", "status": "skipped"}]}
    check = next(c for c in code_checks(call, build_packet(call)) if c.name == "every_request_answered")
    assert not check.ok and check.detail.endswith("your FD")


# ───────── whole conversations (fake LLM) ─────────

def _ask(client, sid, text):
    return client.post("/api/v1/ask", json={"question": text, "session_id": sid}).json()


def _verified(client, question):
    sid = client.post("/api/v1/sessions", json={"caller_number": RAVI}).json()["id"]
    first = _ask(client, sid, question)
    assert first["outcome"] == "auth_required"
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    return sid, client.post("/api/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]


FIVE = ("what is my balance in both accounts and then show my transactions of account 4521 and then when is my next emi "
        "and then what is my balance in account 4512 and then show my transactions of account 4512")


def test_verification_in_the_middle_keeps_both_requests(client, container):
    """Smoke A3 (17 Nov): the second request was lost when the text was read again after the code."""
    sid, reply = _verified(client, "block my credit card ending 6712 temporarily and then what is my balance in both accounts")
    assert reply["outcome"] == "confirm" and "6712" in reply["answer"]
    trace = client.get(f"/api/v1/traces/{reply['trace_id']}").json()
    assert [r["kind"] for r in trace["data"]["understanding"]["requests"]] == ["action", "personal"]
    assert [i["status"] for i in container.conversation.states.load(sid).agenda] == ["waiting", "pending"]
    declined = _ask(client, sid, "no")
    assert "Shall I go on to your balance?" in declined["answer"]
    assert "4521" in _ask(client, sid, "yes")["answer"]


def test_five_questions_are_answered_one_by_one_and_the_caller_can_stop(client, container):
    sid, _ = _verified(client, "what is my balance in both accounts")
    first = _ask(client, sid, FIVE)
    assert first["answer"].startswith("You've asked about five things. Let me take them one at a time, starting with your balance")
    assert "4521" in first["answer"] and first["answer"].endswith("Shall I go on to your transactions?")
    second = _ask(client, sid, "yes")
    # The transactions answer ends in its own question ("Want the other 2?"), so the next item waits (never two questions)
    assert second["answer"].endswith("Want the other 2?") and "Shall I go on" not in second["answer"]
    rest = _ask(client, sid, "yes")
    assert "Shall I go on to your loan?" in rest["answer"]
    stopped = _ask(client, sid, "no")
    assert stopped["answer"] == "Okay, I'll leave those. Is there anything else I can help with?"
    statuses = [i["status"] for i in container.conversation.states.load(sid).agenda][-5:]
    assert statuses == ["done", "done", "skipped", "skipped", "skipped"]


def test_skip_choose_and_a_question_in_the_middle(client, container):
    sid, _ = _verified(client, "what is my balance in both accounts")
    _ask(client, sid, FIVE)  # balance answered, "Shall I go on to your transactions?"

    def item(state, text):
        return next(i for i in reversed(state.agenda) if text in ((i.get("req") or {}).get("question") or i.get("quote") or ""))

    _ask(client, sid, "skip that one")  # the transactions are skipped; the EMI question follows
    state = container.conversation.states.load(sid)
    assert item(state, "transactions of account 4521")["status"] == "skipped"
    assert item(state, "next emi")["status"] in ("done", "waiting")
    container.conversation.traces.set_pending(sid, None)  # (if the EMI answer asked "which loan?", leave it)
    container.conversation.traces.set_pending(sid, {"kind": "agenda_next", "lang": "en", "at": __import__("datetime").datetime.now().astimezone().isoformat(),
                                                    **agenda.offer_pending(state, item(state, "balance in account 4512"), "Shall I go on?")})
    container.conversation.states.save(sid, state)
    picked = _ask(client, sid, "the second one")  # listed: 1) balance of 4512, 2) transactions of 4512
    assert "4512" in picked["answer"]
    assert item(container.conversation.states.load(sid), "transactions of account 4512")["status"] in ("done", "waiting")
    # Something new in the middle (the skipped one, asked again): answered. The balance of 4512 has now been offered
    # twice without being taken up, so it is let go rather than offered a third time.
    middle = _ask(client, sid, "show my transactions of account 4521")
    assert middle["outcome"] == "answered" and "Shall I go" not in middle["answer"]
    assert item(container.conversation.states.load(sid), "balance in account 4512")["status"] == "dropped"


def test_a_missed_question_is_found_by_the_coverage_check(client):
    sid, _ = _verified(client, "what is my balance in both accounts")
    reply = _ask(client, sid, "what is my balance in both accounts and then FAKE_MISS show my transactions of account 4521 please right now")
    trace = client.get(f"/api/v1/traces/{reply['trace_id']}").json()
    assert trace["data"]["coverage_retry"]["added"] >= 1
    assert "4512" in reply["answer"] and len(re.findall(r"₹", reply["answer"])) >= 3  # both answered


def test_goodbye_with_questions_left_gets_one_reminder(client, container):
    sid, _ = _verified(client, "what is my balance in both accounts")
    _ask(client, sid, FIVE)
    container.conversation.traces.set_pending(sid, None)  # the offer was left unanswered and has gone
    reminder = _ask(client, sid, "ok bye")
    assert reminder["answer"].startswith("Before you go, you'd also asked about your transactions.")
    done = _ask(client, sid, "no, bye")
    assert "Thank you for calling" in done["answer"]
    assert "pending" not in {i["status"] for i in container.conversation.states.load(sid).agenda}
