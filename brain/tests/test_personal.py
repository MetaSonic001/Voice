"""MVP 2: caller verification (registered mobile + one-time code) and answers from the caller's own data."""

import json
import re
from datetime import date

from app.domain import Account, Customer, Transaction
from app.services.personal import PersonalQuery, answer, money

ANJALI = "7107420369"  # dummy data: two Digital accounts (0001, 0002), FD, loan, cards


def _code(reply: dict) -> str:
    return re.search(r"\b(\d{6})\b", reply["auth"]["simulated_sms"]).group(1)


def _session(client, caller=ANJALI):
    return client.post("/v1/sessions", json={"caller_number": caller}).json()["id"]


def test_code_is_required_then_waiting_question_is_answered(client, container):
    sid = _session(client)
    first = client.post("/v1/ask", json={"question": "What is my balance in both accounts?", "session_id": sid}).json()
    assert first["outcome"] == "auth_required" and first["auth"]["masked_mobile"] == "******0369"
    assert "0369" in first["answer"] and "9,46,213" not in first["answer"]  # nothing disclosed before verification
    code = _code(first)

    wrong = client.post("/v1/auth/verify", json={"session_id": sid, "code": "000000" if code != "000000" else "111111"}).json()
    assert wrong["auth"]["status"] == "invalid" and wrong["auth"]["attempts_left"] == 2

    ok = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()
    assert ok["auth"]["status"] == "verified"
    assert "₹9,46,213" in ok["reply"]["answer"] and "₹4,39,895" in ok["reply"]["answer"]

    # the code itself is never stored: not in traces, not in the audit trail, not in the session record
    with container.db.connection() as conn:
        stored = json.dumps([conn.execute("SELECT data, answer FROM traces WHERE session_id = %s", (sid,)).fetchall(),
                             conn.execute("SELECT detail FROM auth_events WHERE session_id = %s", (sid,)).fetchall(),
                             conn.execute("SELECT auth FROM sessions WHERE id = %s", (sid,)).fetchall()], default=str)
        events = [r["event"] for r in conn.execute("SELECT event FROM auth_events WHERE session_id = %s ORDER BY id",
                                                   (sid,)).fetchall()]
    assert code not in stored
    assert events[:3] == ["otp_sent", "otp_failed", "otp_verified"] and "data_read" in events


def test_three_wrong_codes_lock_verification(client):
    sid = _session(client)
    client.post("/v1/ask", json={"question": "my last transaction please", "session_id": sid})
    statuses = [client.post("/v1/auth/verify", json={"session_id": sid, "code": "123450"}).json()["auth"]["status"]
                for _ in range(4)]
    assert statuses == ["invalid", "invalid", "locked", "locked"]
    again = client.post("/v1/ask", json={"question": "my balance please", "session_id": sid}).json()
    assert again["outcome"] == "handoff" and again["auth"]["status"] == "locked"


def test_unknown_number_must_give_registered_mobile(client):
    sid = _session(client, "9000000001")
    asked = client.post("/v1/ask", json={"question": "What is my EMI?", "session_id": sid}).json()
    assert asked["auth"]["status"] == "need_mobile"
    bad = client.post("/v1/ask", json={"question": "it is 9123456780", "session_id": sid}).json()
    assert bad["auth"]["status"] == "not_registered"
    good = client.post("/v1/ask", json={"question": f"mera number +91 {ANJALI} hai", "session_id": sid}).json()
    assert good["auth"]["status"] == "otp_sent" and good["auth"]["masked_mobile"] == "******0369"


def _customer() -> Customer:
    salary = Account("10020030009", "Value", 52000.5, [Transaction(date(2026, 12, 1), "Salary Credit", "Credit", 45000),
                                                       Transaction(date(2026, 11, 3), "UPI Transfer", "Debit", 799)])
    other = Account("10020030010", "Digital", 1250, [Transaction(date(2026, 12, 30), "UPI Transfer", "Debit", 300)])
    return Customer("CIF1", "Test", "9999999999", [salary, other])


def test_account_choice_salary_inference_and_indian_grouping():
    c, today = _customer(), date(2026, 12, 31)
    ask = answer(c, PersonalQuery("balance"), "en", today)
    assert ask.outcome == "clarify" and "Value ending 0009" in ask.sentences[0]
    salary = answer(c, PersonalQuery("balance", account="salary"), "hinglish", today)
    assert salary.sentences == ["Aapke Value savings account (ending 0009) mein ₹52,000.50 balance hai."]
    assert money(1234567) == "12,34,567"


def test_transaction_filters_search_all_accounts_and_say_what_is_not_visible():
    c, today = _customer(), date(2026, 12, 31)
    upi = answer(c, PersonalQuery("transactions", keyword="upi", total=True), "en", today)
    assert "₹799" in upi.sentences[0] and "₹300" in upi.sentences[1]  # both accounts, no "which account?"
    rajesh = answer(c, PersonalQuery("transactions", direction="credit", keyword="rajesh"), "en", today)
    assert any("not the names" in s for s in rajesh.sentences)
    renew = answer(Customer("C", "T", "1", [c.accounts[0]], fd={"balance": 1e5, "rate": "7%", "maturity": date(2027, 1, 1)}),
                   PersonalQuery("fd", keyword="renew"), "en", today)
    assert "matures on 1 Jan 2027" in renew.sentences[0] and "can't see that detail" in renew.sentences[-1]


def test_which_account_reply_keeps_the_question(client):
    # the reported bug: "what is my balance?" -> "which one?" -> "second one" must give the balance, not "can't see"
    sid = _session(client)
    code = _code(client.post("/v1/ask", json={"question": "what is my balance?", "session_id": sid}).json())
    asked = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]
    assert asked["outcome"] == "clarify" and "Which one" in asked["answer"]
    second = client.post("/v1/ask", json={"question": "second one", "session_id": sid}).json()
    assert second["outcome"] == "answered" and "₹4,39,895" in second["answer"] and "0002" in second["answer"]
