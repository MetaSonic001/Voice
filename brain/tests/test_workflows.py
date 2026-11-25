"""Phase 4 step 5: the unrecognised-debit and lost-card workflows. The transaction is never guessed."""

import re
from datetime import date

import pytest

from app.ports import BankError
from app.services.personal import PersonalQuery
from app.services.workflows import Candidate, find_candidates

RAVI, IMRAN = "9000000101", "9000000105"


def _ask(client, sid, text):
    return client.post("/api/v1/ask", json={"question": text, "session_id": sid}).json()


def _verified(client, caller, question):
    sid = client.post("/api/v1/sessions", json={"caller_number": caller}).json()["id"]
    first = _ask(client, sid, question)
    assert first["outcome"] == "auth_required"
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    return sid, client.post("/api/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]


def test_nothing_to_match_means_nothing_is_guessed(client, container):
    sid, reply = _verified(client, RAVI, "I don't recognise a payment from my account")
    # Asks which one, reading out the latest debits; it does not pick one, block anything or raise a complaint.
    assert reply["outcome"] == "clarify" and "Is it one of these?" in reply["answer"]
    assert "block" not in reply["answer"].lower() and container.traces.pending(sid)["kind"] == "fraud_pick"
    assert "Let's look into that payment" not in reply["answer"]  # said once, before the code - never twice
    ravi = container.bank.customer(container.bank.find_by_mobile(RAVI).cif)
    assert find_candidates(ravi, PersonalQuery(), date.today()) == []


def test_a_named_card_with_no_matching_payment_is_still_protected(client):
    sid, reply = _verified(client, IMRAN, "I don't recognise a transaction of 1234567 on my credit card")
    assert reply["outcome"] == "confirm" and "couldn't find a transaction like that" in reply["answer"]
    assert "permanently block" in reply["answer"]  # protect first, even though the payment was not found


def test_a_upi_transfer_is_not_a_card_block(client):
    sid, reply = _verified(client, RAVI, "I don't recognise a debit of 10000 from my account")
    assert reply["outcome"] == "confirm" and "Suresh Patel" in reply["answer"]
    assert "block" not in reply["answer"].lower() and "1930" in reply["answer"] and "fraud complaint" in reply["answer"]
    done = _ask(client, sid, "yes please")
    assert done["outcome"] == "handoff" and done["handoff"]["department"] == "fraud" and "AU-CMP-" in done["answer"]


def test_several_matching_payments_are_listed_and_the_caller_chooses(container):
    journeys, auth = container.conversation.journeys, container.auth
    imran = container.bank.customer(container.bank.find_by_mobile(IMRAN).cif)
    two = [Candidate("card", 500, "Cafe A", "2026-10-30", "CARD", "success", card_kind="credit", card_last4=imran.credit_cards[0].last4),
           Candidate("card", 900, "Shop B", "2026-10-31", "CARD", "success", card_kind="credit", card_last4=imran.credit_cards[0].last4)]
    from app.services import journeys as jmod

    assert jmod._pick_candidate("the second one", two) is two[1] and jmod._pick_candidate("it was 500", two) is two[0]
    assert jmod._pick_candidate("hmm not sure", two) is None and jmod._pick_candidate("around 50", two) is None
    assert journeys is not None and auth is not None


def test_several_matches_ask_which_one(client, container, monkeypatch):
    import app.services.journeys as jmod

    cards = [Candidate("card", 500, "Cafe A", "2026-10-30", "CARD", "success", card_kind="credit", card_last4="4410"),
             Candidate("card", 900, "Shop B", "2026-10-31", "CARD", "success", card_kind="credit", card_last4="4410")]
    monkeypatch.setattr(jmod, "find_candidates", lambda *a, **k: cards)
    sid, reply = _verified(client, IMRAN, "I don't recognise a payment of 500 on my account")
    assert reply["outcome"] == "clarify" and "2 payments" in reply["answer"] and "Cafe A" in reply["answer"]
    picked = _ask(client, sid, "the second one")
    assert picked["outcome"] == "confirm" and "Shop B" in picked["answer"] and "permanently block" in picked["answer"]


def test_lost_card_asks_which_then_one_yes_then_checks_recent_payments(client, container):
    sid, asked = _verified(client, RAVI, "my card is lost")
    assert asked["outcome"] == "clarify" and "6712" in asked["answer"] and "2208" in asked["answer"]
    confirm = _ask(client, sid, "the one ending 2208")
    assert confirm["outcome"] == "confirm" and "permanently block" in confirm["answer"]  # lost = permanent, one clear yes
    done = _ask(client, sid, "yes")
    assert done["outcome"] == "action_done" and "permanently blocked" in done["answer"] and "replacement card" in done["answer"]
    assert "recent" in done["answer"] or "don't see any recent payments" in done["answer"]
    with container.db.connection() as conn:
        assert conn.execute("SELECT status FROM corebank.credit_cards WHERE last4 = '2208'").fetchone()["status"] == "blocked"


def test_bank_down_still_gets_a_fraud_report_to_the_fraud_team(client, container, monkeypatch):
    sid, _ = _verified(client, RAVI, "What is my balance in both accounts?")

    def down(_):
        raise BankError("simulated outage")

    monkeypatch.setattr(container.auth, "verified_customer", down)
    reply = _ask(client, sid, "I don't recognise a debit of 2000")
    assert reply["outcome"] == "confirm" and "can't check your account right now" in reply["answer"] and "fraud team" in reply["answer"]
    monkeypatch.undo()
    handed = _ask(client, sid, "yes")
    assert handed["outcome"] == "handoff" and handed["handoff"]["department"] == "fraud"


@pytest.mark.parametrize("risky", ["I don't recognise this", "my card is lost"])
def test_a_risk_is_never_answered_as_a_plain_enquiry(client, risky):
    sid = client.post("/api/v1/sessions", json={"caller_number": RAVI}).json()["id"]
    assert _ask(client, sid, risky)["outcome"] == "auth_required"  # protected path: verify, then the fraud workflow


def test_an_open_request_is_asked_about_only_when_the_caller_gave_no_reason(client, container):
    sid = client.post("/api/v1/sessions", json={"caller_number": "9000000102"}).json()["id"]  # Priya
    first = _ask(client, sid, "What is my balance in all accounts?")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    quiet = client.post("/api/v1/auth/verify", json={"session_id": sid, "code": code}).json()
    assert "open complaint" not in quiet["message"]  # nothing open: nothing said

    cif = container.bank.find_by_mobile("9000000102").cif
    container.service_desk.create_ticket(session_id=None, cif=cif, kind="complaint", category="payment_failed_debited",
                                         department="payments", summary="UPI debited but not credited ₹98765", detail={}, tat_days=5)
    sid2 = client.post("/api/v1/sessions", json={"caller_number": "9000000102"}).json()["id"]
    first = _ask(client, sid2, "What is my balance in all accounts?")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    verified = client.post("/api/v1/auth/verify", json={"session_id": sid2, "code": code}).json()
    # the caller already said why they called (their balance): helped with that, not asked about the old complaint
    assert "open complaint" not in verified["message"]
    customer = container.bank.customer(cif)  # with no question waiting, the open request is asked about, never assumed
    note = container.conversation.journeys.open_request_note(customer, "en")
    assert "open complaint with us about payment failed debited. Is today's call about that?" in note
    assert "98765" not in note and "UPI" not in note  # only the kind and the topic, no details
    with container.db.connection() as conn:  # leave the shared test data as it was
        conn.execute("UPDATE tickets SET status = 'closed' WHERE cif = %s", (cif,))


def test_after_a_system_error_yes_transfers_without_the_llm(client, container, monkeypatch):
    # live call f4d36d3a: the error reply offers "our team"; the caller's "yes" went to the LLM and failed the same way
    async def down(*args, **kwargs):
        raise RuntimeError("LLM HTTP 429: rate limit")

    monkeypatch.setattr(container.conversation, "understand", down)
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    failed = _ask(client, sid, "2 साल की FD पर ब्याज कितना होता है?")
    assert failed["outcome"] == "error"
    handed = _ask(client, sid, "हाँ मुझे किसी team के साथ दे दो।")  # the LLM is still down: read in code
    assert handed["outcome"] == "handoff" and handed["handoff"]["department"] == "general"


def test_a_question_while_a_fraud_offer_waits_keeps_the_offer(client):
    # regression pack 17 Nov: "Will I get the money back?" lost the ₹10,000 payment; "yes, raise a complaint" re-listed debits
    sid, reply = _verified(client, RAVI, "I don't recognise the UPI debit of 10000 from my account")
    assert reply["outcome"] == "confirm" and "reverse" not in reply["answer"].lower()
    side = _ask(client, sid, "Will I get the money back automatically?")
    assert side["outcome"] == "confirm" and "can't confirm" in side["answer"] and "complaint" in side["answer"]
    done = _ask(client, sid, "Yes, raise a fraud complaint.")
    assert done["outcome"] == "handoff" and "AU-" in done["answer"]  # the complaint is raised, with its reference
