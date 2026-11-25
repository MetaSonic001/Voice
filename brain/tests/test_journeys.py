"""Phase 3 journeys end to end (fake LLM, mock core bank): card actions need a clear yes and are read back,
fraud protects first then disputes and transfers, complaints raise tickets, transfers ask first and reach the desk."""

import re
from datetime import date

from app.services.confirm import read_reply
from app.services.personal import PersonalQuery, answer

RAVI, IMRAN = "9000000101", "9000000105"  # demo customers in the mock core bank


def _code(reply: dict) -> str:
    return re.search(r"\b(\d{6})\b", reply["auth"]["simulated_sms"]).group(1)


def _ask(client, sid, text):
    return client.post("/api/v1/ask", json={"question": text, "session_id": sid}).json()


def _verified(client, caller, question):
    sid = client.post("/api/v1/sessions", json={"caller_number": caller}).json()["id"]
    first = _ask(client, sid, question)
    assert first["outcome"] == "auth_required"
    ok = client.post("/api/v1/auth/verify", json={"session_id": sid, "code": _code(first)}).json()
    return sid, ok["reply"]


def _card(container, last4):
    with container.db.connection() as conn:
        return conn.execute("SELECT status, international FROM corebank.credit_cards WHERE last4 = %s", (last4,)).fetchone()


def test_yes_no_reader():
    assert [read_reply(x) for x in ("haan kar do", "yes please", "ji", "theek hai", "हाँ")] == ["yes"] * 5
    assert [read_reply(x) for x in ("no", "mat karo", "abhi nahi", "नहीं")] == ["no"] * 4
    assert read_reply("wait, which card?") == "question" and read_reply("hmm") == "unclear"


def test_card_block_needs_a_clear_yes_and_is_read_back(client, container):
    sid, asked = _verified(client, RAVI, "Please block my credit card ending 6712 temporarily")
    assert asked["outcome"] == "confirm" and "6712" in asked["answer"] and "Shall I go ahead?" in asked["answer"]
    assert _ask(client, sid, "no, don't")["outcome"] == "cancelled"
    assert _card(container, "6712")["status"] == "active"  # nothing changed without a yes

    _ask(client, sid, "block my credit card ending 6712 temporarily")
    done = _ask(client, sid, "haan kar do")
    assert done["outcome"] == "action_done" and "temporarily blocked" in done["answer"]
    assert re.search(r"reference number is AU\d{12}", done["answer"])
    assert _card(container, "6712")["status"] == "temp_blocked"

    _ask(client, sid, "unblock my credit card ending 6712")
    assert _ask(client, sid, "yes")["outcome"] == "action_done"
    assert _card(container, "6712")["status"] == "active"
    events = client.get(f"/api/v1/calls/{sid}").json()["events"]
    assert [e["detail"]["done"] for e in events if e["kind"] == "card_action"] == [True, True]


def test_which_card_is_asked_then_confirmed(client):
    sid, asked = _verified(client, RAVI, "block my card")  # temporary or permanent is asked first (PM review, 17 Nov)
    assert asked["outcome"] == "clarify" and "temporarily" in asked["answer"] and "permanently" in asked["answer"]
    which = _ask(client, sid, "temporarily")
    assert which["outcome"] == "clarify" and "6712" in which["answer"] and "2208" in which["answer"]
    picked = _ask(client, sid, "the one ending 2208")
    assert picked["outcome"] == "confirm" and "2208" in picked["answer"]


def test_fraud_blocks_first_then_disputes_and_transfers(client, container):
    sid, asked = _verified(client, IMRAN, "I don't recognise a transaction of 8499 on my credit card")
    assert asked["outcome"] == "confirm" and "GLOBALTECH" in asked["answer"] and "permanently block" in asked["answer"]
    done = _ask(client, sid, "yes")
    assert done["outcome"] == "action_done" and "1930" in done["answer"] and "fraud team" in done["answer"]
    assert _card(container, "4410")["status"] == "blocked"
    handed = _ask(client, sid, "yes please")
    assert handed["outcome"] == "handoff" and handed["handoff"]["department"] == "fraud"
    assert "AU-DSP-" in handed["answer"]
    board = client.get("/api/v1/desk/handoffs").json()["handoffs"]
    mine = next(h for h in board if h["session_id"] == sid)
    assert mine["priority"] == 1 and mine["status"] == "waiting" and "Imran Khan" in mine["note"]["summary"]
    assert any("blocked" in d or "block" in d for d in mine["note"]["done"])


def test_atm_complaint_explains_then_raises_a_ticket(client):
    sid, asked = _verified(client, RAVI, "The ATM did not give cash but 10000 was debited")
    assert asked["outcome"] == "confirm" and "come back to your account automatically" in asked["answer"]
    raised = _ask(client, sid, "yes")
    assert raised["outcome"] == "ticket" and re.search(r"AU-CMP-\d{8}-\d{4}", raised["answer"])


def test_transfer_asks_first_then_reaches_the_desk(client):
    sid = client.post("/api/v1/sessions", json={"caller_number": "9000000001"}).json()["id"]
    handed = _ask(client, sid, "I want to talk to a human agent")  # an explicit request is the consent: no "Is that okay?"
    assert handed["outcome"] == "handoff" and "Is that okay?" not in handed["answer"]
    status = client.get(f"/api/v1/handoffs/{sid}").json()
    assert status["status"] == "waiting" and status["position"] >= 1
    hid = handed["handoff"]["id"]
    client.post(f"/api/v1/desk/handoffs/{hid}/accept", json={"agent_name": "Sana (Customer Care)"})
    client.post(f"/api/v1/desk/handoffs/{hid}/messages", json={"text": "Hi, I'm Sana. How can I help?"})
    seen = client.get(f"/api/v1/handoffs/{sid}").json()
    assert seen["status"] == "active" and seen["messages"][0]["text"].startswith("Hi, I'm Sana")
    client.post(f"/api/v1/desk/handoffs/{hid}/complete", json={"disposition": "resolved", "notes": "explained charges"})
    kinds = [e["kind"] for e in client.get(f"/api/v1/calls/{sid}").json()["events"]]
    assert {"handoff_requested", "handoff_accepted", "handoff_completed"} <= set(kinds)


def test_an_offer_to_connect_is_a_real_question(client):
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    refused = _ask(client, sid, "zxq qwv plorb")
    assert refused["outcome"] == "refused" and "Shall I?" in refused["answer"]
    assert _ask(client, sid, "yes please")["outcome"] == "handoff"


def test_minimum_balance_comes_from_the_callers_own_account(container):
    ravi = container.bank.customer(container.bank.find_by_mobile(RAVI).cif)
    rule = answer(ravi, PersonalQuery("charges", detail="minimum_balance", account="4521"), "en", date(2026, 12, 31))
    assert rule.sentences == ["Your Value account ending 4521 needs an average monthly balance of ₹10,000 at an urban branch.",
                              "This month your average so far is ₹61,200, so you're above it."]
    digital = answer(ravi, PersonalQuery("charges", detail="minimum_balance", account="4512"), "en", date(2026, 12, 31))
    assert digital.sentences[0].startswith("Good news: your Digital account ending 4512 has no minimum balance")


def test_a_no_with_a_new_request_answers_the_request(client):
    # from a real test call: "No, I don't want to do that. Show me ..." used to just cancel
    sid, asked = _verified(client, RAVI, "Please block my credit card ending 6712 temporarily")
    assert asked["outcome"] == "confirm"
    reply = _ask(client, sid, "No, leave it. What is my balance in both accounts?")
    assert reply["outcome"] == "answered" and "4521" in reply["answer"] and "4512" in reply["answer"]
