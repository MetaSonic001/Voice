"""Phase 4 step 6: bank-data answers (merged lists, one status table, three at a time), small talk, repeat, transfers."""

import re
from datetime import date

import pytest

from app.domain import Transaction
from app.services.personal import PersonalQuery, _txn_line, answer
from app.services.smalltalk import is_fragment, reply_for

RAVI = "9000000101"


def _ask(client, sid, text):
    return client.post("/v1/ask", json={"question": text, "session_id": sid}).json()


def _ravi(container):
    return container.bank.customer(container.bank.find_by_mobile(RAVI).cif)


def test_last_five_across_accounts_is_exactly_five_newest_first_each_with_its_account(container):
    ans = answer(_ravi(container), PersonalQuery("transactions", account="all", count=5), "en", container.settings.demo_today)
    lines = [s for s in ans.sentences if "₹" in s]
    assert len(lines) == 3 and ans.sentences[0].startswith("Here are your latest 5 transactions across your accounts")
    assert all("account ending" in line for line in lines)  # every line says which account
    assert ans.sentences[-1] == "Want the other 2?" and len(ans.offer["more"]) == 2  # three at a time, then the rest
    shown = lines + ans.offer["more"]
    dates = [datetime_of(s) for s in shown]
    assert dates == sorted(dates, reverse=True)  # newest first, merged across accounts, not 1 + 5 from two accounts


def datetime_of(line: str) -> tuple:
    months = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
    d, m, y = re.search(r"(\d+) (\w+) (\d{4})", line).groups()
    t = re.search(r"at (\d{1,2}):(\d\d)", line)
    return int(y), months[m[:3]], int(d), (int(t.group(1)), int(t.group(2))) if t else (0, 0)


def test_saying_go_on_reads_the_rest(client):
    sid = client.post("/v1/sessions", json={"caller_number": RAVI}).json()["id"]
    first = _ask(client, sid, "show my last 5 transactions")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    client.post("/v1/auth/verify", json={"session_id": sid, "code": code})
    reply = _ask(client, sid, "What is my transaction in both accounts")
    if "Want the other" in reply["answer"]:
        more = _ask(client, sid, "go on")
        assert more["outcome"] == "answered" and "₹" in more["answer"] and "Want the other" not in more["answer"]


@pytest.mark.parametrize("status,kind,must,must_not", [
    ("success", "Debit", "there was a UPI transfer of ₹10,000 to Suresh Patel.", "processing"),
    ("success", "Credit", "there was a UPI transfer of ₹10,000 from Suresh Patel.", "didn't"),
    ("pending", "Debit", "and it's still processing", ""), ("failed", "Debit", "didn't go through, so no money left your account", ""),
    ("reversed", "Debit", "reversed, so the money came back to you", ""), ("mystery", "Debit", "can't confirm whether it went through", ""),
])
def test_each_transaction_is_one_full_sentence_with_its_status(status, kind, must, must_not):
    x = Transaction(date(2026, 11, 5), "UPI Transfer", kind, 10000, "UPI", "Suresh Patel", status, "UTR1", "", "09:15")
    line = _txn_line(x, "en")
    assert line.startswith("On Thursday, 5 November 2026, at 9:15, ") and must in line and (not must_not or must_not not in line)
    for lang in ("hi", "hinglish"):
        assert "₹10,000" in _txn_line(x, lang) and "(failed)" not in _txn_line(x, lang)


def test_status_words_in_hindi_and_hinglish_are_not_english_paid():
    x = Transaction(date(2026, 11, 5), "UPI Transfer", "Debit", 500, "UPI", "Asha", "pending", "UTR2", "", "10:00")
    assert "paid" not in _txn_line(x, "hinglish") and "processing" not in _txn_line(x, "hi")


@pytest.mark.parametrize("text,fragment", [
    ("so, you know,", True), ("hmm", True), ("uh um", True), ("matlab", True), ("what is my balance", False), ("thank you", False),
])
def test_fillers_are_fragments_that_get_no_reply(text, fragment):
    assert is_fragment(text) is fragment


def test_small_talk_is_short_and_fits_the_words_used():
    assert reply_for("thank you so much", "en") == "You're welcome!"
    assert reply_for("oh dear", "en").startswith("Is there anything else")
    assert reply_for("Hello? I'm waiting", "en").startswith("Yes, I'm here")
    assert reply_for("bye", "en").startswith("Thank you for calling")
    assert reply_for("shukriya", "hinglish").startswith("Aapka swagat")


def test_greeting_is_said_once_and_a_fragment_gets_silence(client, container):
    sid = client.post("/v1/sessions", json={}).json()["id"]
    greeted = _ask(client, sid, "hi")
    again = _ask(client, sid, "hello")
    assert greeted["answer"].startswith("Hi") and not again["answer"].startswith("Hi! I'm AU")  # not the full greeting twice
    quiet = _ask(client, sid, "so, you know,")
    assert quiet["answer"] == "" and quiet["outcome"] == "smalltalk"
    assert container.conversation.states.load(sid).greeted is True


def test_repeat_that_says_the_last_answer_again_without_the_llm(client):
    sid = client.post("/v1/sessions", json={}).json()["id"]
    first = _ask(client, sid, "What is the minimum balance requirement for the savings account?")
    again = _ask(client, sid, "can you repeat that")
    assert again["answer"] == first["answer"] and again["outcome"] == "answered"
    assert _ask(client, sid, "phir se bolo")["answer"] == first["answer"]


def test_asking_for_a_person_transfers_without_a_second_question(client):
    sid = client.post("/v1/sessions", json={"caller_number": "9000000001"}).json()["id"]
    handed = _ask(client, sid, "I want to talk to a human agent")
    assert handed["outcome"] == "handoff" and "Is that okay?" not in handed["answer"] and handed["handoff"]


def test_a_charge_in_the_bank_data_is_explained_from_the_charges_document():
    from app.services.personal import CHARGE_KB

    assert set(CHARGE_KB) >= {"amb_shortfall", "sms_alerts", "emi_bounce"}
