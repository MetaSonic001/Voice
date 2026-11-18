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
    assert len(lines) == 5 and ans.sentences[0].startswith("Here are your latest 5 transactions across your accounts")
    assert all("account ending" in line for line in lines)  # every line says which account
    assert not ans.offer.get("more")  # asked for five: all five, not "Want the other 2?" (PM review, 17 Nov)
    dates = [datetime_of(s) for s in lines]
    assert dates == sorted(dates, reverse=True)  # newest first, merged across accounts, not 1 + 5 from two accounts


def datetime_of(line: str) -> tuple:
    months = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
    rel = re.search(r"\b(today|yesterday)\b", line, re.I)  # recent days are said "Today, at ..." / "Yesterday, at ..."
    if rel:
        y, mo, d = 9999, 12, 31 if rel.group(1).lower() == "today" else 30
    else:
        d, m, y = re.search(r"(\d+) ([A-Z][a-z]+)(?: (\d{4}))?", line).groups()  # the year only when it isn't obvious
        y, mo, d = int(y or 0), months[m[:3]], int(d)
    t = re.search(r"at (\d{1,2}):(\d\d) (in the morning|in the afternoon|in the evening|at night)", line)
    if not t:
        return y, mo, d, (0, 0)
    h, part = int(t.group(1)) % 12, t.group(3)
    pm = part in ("in the afternoon", "in the evening") or (part == "at night" and h >= 9)  # 9:40 at night = 21:40
    return y, mo, d, (h + 12 if pm else h, int(t.group(2)))


def test_unasked_count_reads_three_then_offers_the_rest(container):
    ans = answer(_ravi(container), PersonalQuery("transactions", account="all"), "en", container.settings.demo_today)
    assert len([s for s in ans.sentences if "₹" in s]) == 3 and ans.sentences[-1] == "Want the other 2?"


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
    ("pending", "Debit", "and it's still processing", ""), ("failed", "Debit", "unsuccessful, so no money was deducted", ""),
    ("reversed", "Debit", "reversed, so the money came back to you", ""), ("mystery", "Debit", "can't confirm whether it went through", ""),
])
def test_each_transaction_is_one_full_sentence_with_its_status(status, kind, must, must_not):
    x = Transaction(date(2026, 11, 5), "UPI Transfer", kind, 10000, "UPI", "Suresh Patel", status, "UTR1", "", "09:15")
    line = _txn_line(x, "en")
    assert line.startswith("On Thursday, 5 November, at 9:15 in the morning, ") and must in line and (not must_not or must_not not in line)
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


# ───────── spoken form (live Sarvam calls, 16 Nov 2026) ─────────

@pytest.mark.parametrize("text,lang,spoken", [
    ("I've sent a 6-digit code.", "en", "I've sent a 6 digit code."),  # was read "six dash digit"
    ("मोबाइल (आख़िरी अंक 0106) पर कोड भेजा है।", "hi", "मोबाइल, आख़िरी अंक 0106, पर कोड भेजा है।"),
    ("₹2,500 is on hold (card authorisation pending (fuel station)), so", "en", "is on hold, card authorisation pending, fuel station, so"),
    ("Hamari Fraud & Risk team.", "hinglish", "Hamari Fraud and Risk team."),
    ("Tenure is 5-10 years (subject to terms).", "en", "Tenure is 5-10 years, subject to terms."),  # a range keeps its hyphen
    ("Check your account(s).", "en", "Check your accounts."),
])
def test_text_sent_to_the_voice_has_no_brackets_or_digit_hyphens(text, lang, spoken):
    from app.services.numbers import speech_text
    assert spoken in speech_text(text, lang)


def test_dates_and_times_are_said_the_way_people_say_them():
    from app.services.personal import _when, day
    recent = date.today()
    x = recent.strftime("%B")
    assert _when(recent, "21:40", "en").endswith(f"{recent.day} {x}, at 9:40 at night")  # not "21:40", no year
    assert _when(recent, "20:15", "en").endswith("at 8:15 in the evening")
    assert _when(recent, "09:05", "hinglish").endswith("ko subah 9:05 baje,")
    assert "शाम 4:30 बजे" in _when(recent, "16:30", "hi") and str(recent.year) not in _when(recent, "16:30", "hi")
    far = date(recent.year + 2, 3, 1)
    assert day(far, "en") == f"1 March {far.year}"  # far away: the year is needed


def test_two_loans_each_emi_says_which_loan(container):
    imran = container.bank.customer(container.bank.find_by_mobile("9000000101").cif)
    ans = answer(imran, PersonalQuery("loan", detail="next_emi"), "en", container.settings.demo_today)
    emis = [s for s in ans.sentences if "EMI" in s]
    assert len(emis) == 2 and all(s.startswith("The EMI on your ") for s in emis)
    # two loans: each says its loan's last four digits, so the caller knows which EMI is which (owner, 17 Nov)
    assert any("home loan ending" in s for s in emis) and any("personal loan ending" in s for s in emis)


@pytest.mark.parametrize("said,keeps", [("Yes.", True), ("Okay sure", True), ("हाँ जी", True),
                                        ("Yes, what is my balance?", False), ("Tell me in English", False)])
def test_a_one_word_reply_does_not_change_the_call_language(said, keeps):
    from app.services.conversation import _carries_no_language
    assert _carries_no_language(said) is keeps


# ───────── PM review of calls d48a5afe (Pooja) and f4d36d3a (Sunita), 17 Nov ─────────

def _by_mobile(container, mobile):
    return container.bank.customer(container.bank.find_by_mobile(mobile).cif)


def test_pooja_upi_total_and_rajesh_today_answer_the_question(container):
    pooja, today = _by_mobile(container, "9000000106"), container.settings.demo_today
    spent = answer(pooja, PersonalQuery("transactions", total=True, period="last_7_days", keyword="UPI", direction="debit"),
                   "hi", today).sentences
    assert spent[0].startswith("पिछले 7 दिनों में आपने UPI से कुल ₹") and "पेमेंट्स में" in spent[0]  # a total, said with what it covers
    rajesh = answer(pooja, PersonalQuery("transactions", period="today", keyword="Rajesh", direction="credit"), "hi", today)
    assert rajesh.sentences[0].startswith("जी हाँ।") and "Rajesh Sharma" in rajesh.sentences[0] and "₹5,000" in rajesh.sentences[0]
    nobody = answer(pooja, PersonalQuery("transactions", period="today", keyword="Suresh", direction="credit"), "en", today)
    assert nobody.sentences == ["No, I don't see any money from Suresh today."]  # says what was searched, not "no transaction"
    salary = answer(pooja, PersonalQuery("transactions", period="this_month", keyword="salary"), "en", today)
    assert salary.sentences[0].startswith("Yes. ")


def test_how_many_loans_counts_loans_and_does_not_repeat_the_emi(container):
    from app.domain import Understanding
    from app.services.understanding import asked_only
    from app.services.conversation import personal_query

    sunita, today = _by_mobile(container, "9000000107"), container.settings.demo_today
    u = Understanding("hi", "personal", "", "", requests=[_req("loan", "outstanding", item="gold loan")])
    q = personal_query(asked_only(u, "अच्छा, और मेरे कितने loan बाकी हैं?").requests[0].personal)
    assert q.detail == "loan_count_left"
    said = answer(sunita, q, "hi", today).sentences
    assert said[0] == "आपके नाम पर अभी एक लोन चल रहा है, आपका 0107 वाला गोल्ड लोन।" and "बाकी" in said[1] and "अगली EMI" not in " ".join(said)
    owed = answer(sunita, PersonalQuery("loan", detail="outstanding"), "hi", today).sentences
    assert len(owed) == 1 and "बकाया" in owed[0]  # the amount owed alone, not the next EMI again
    nxt = answer(sunita, PersonalQuery("loan", detail="next_emi"), "hi", today).sentences
    assert nxt[0].startswith("आपके 0107 वाले गोल्ड लोन की अगली EMI")  # names the loan and its digits


def _req(topic, detail, item=""):
    from app.domain import Request
    return Request("personal", "q", {"topic": topic, "detail": detail, "item": item})


def test_stale_mock_bank_is_reseeded_for_today(container):
    from datetime import timedelta

    from app.adapters.mock_corebank import ensure_seeded
    db, today = container.db, container.settings.demo_today
    with db.connection() as conn:
        conn.execute("UPDATE corebank.seed_info SET seeded_for = %s", (today - timedelta(days=10),))
    ensure_seeded(db, container.settings.bank_data_file or None, today)  # same data as the session started with
    with db.connection() as conn:
        assert conn.execute("SELECT seeded_for FROM corebank.seed_info").fetchone()["seeded_for"] == today


@pytest.mark.parametrize("text,lang,spoken", [
    ("Your reference number is AU261116001210, and", "en", "A.U., 2 6 1 1, 1 6 0 0, 1 2 1 0, and"),  # not one huge number
    ("Your complaint number is AU-CMP-20261117-0003.", "en", "A.U. C M P, 2 0 2 6, 1 1 1 7, 0 0 0 3."),
    ("Your card ending 6712 and mobile 9000000101.", "en", "ending 6712 and mobile 9000000101."),  # endings left alone
])
def test_reference_numbers_are_read_digit_by_digit(text, lang, spoken):
    from app.services.numbers import speech_text
    assert spoken in speech_text(text, lang)


def test_fraud_lines_never_reassure_or_predict():
    # PM review, 17 Nov: "Good news: no payments" after a shared OTP, "may still be reversed by itself" for a pending
    # payment the caller doesn't recognise, and "nothing to change" for a stolen card that was already blocked
    from app.services.actions import w
    from app.services.journeys import j
    for lang in ("en", "hi", "hinglish"):
        assert "good news" not in j("otp_none", lang).lower() and "achhi baat" not in j("otp_none", lang).lower()
        assert "reverse" not in j("fraud_pending", lang).lower() and "वापस" not in j("fraud_pending", lang)
    assert "can't be used" in w("already_blocked", "en", card="Ananta credit card ending 4410", state="permanently blocked")
    assert "fraud team" in j("fraud_no_recent", "en")
