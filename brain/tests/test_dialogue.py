"""Dialogue decisions (ADR 0024): voice-safe text, caller facts, strategy, guided flows, the "check yours?" offer,
honest transaction counts and the fraud-team transfer. Offline: fake LLM, throwaway Postgres."""

import re

from app.domain import Hit
from app.services.dialogue import describe_facts, merge_facts, next_question, strategy
from app.services.kbmeta import missing_products, named_products, trim_context
from app.services.guard import asks_for_team, risk_flags
from app.services.numbers import clean_markup, speech_text

RAVI = "9000000101"


def _ask(client, sid, text):
    return client.post("/v1/ask", json={"question": text, "session_id": sid}).json()


def _session(client, caller=RAVI):
    return client.post("/v1/sessions", json={"caller_number": caller}).json()["id"]


def test_markdown_never_reaches_the_voice_or_the_screen():
    assert clean_markup("- **Basic Savings (BSBDA)**: koi minimum balance nahi. [3]") == "Basic Savings (BSBDA): koi minimum balance nahi. [3]"
    assert clean_markup("Key benefits include: - 2 points per ₹100 [1].") == "Key benefits include: 2 points per ₹100 [1]."
    assert clean_markup("## Fees") == "Fees" and clean_markup("1. First, apply online.") == "First, apply online."
    # amounts, ranges, rates and account endings are left alone
    kept = "The fee is ₹4,999 + taxes, 23.88% p.a., ₹500 - ₹1,000, on account ending 4521."
    assert clean_markup(kept) == kept
    assert "*" not in speech_text("**Zenith+** costs ₹4,999 [1].", "en")


def test_facts_last_the_call_and_task_facts_end_with_the_task():
    facts = merge_facts({}, {"senior_citizen": "yes", "product": "fixed deposit", "tenure": "2 years"})
    for _ in range(15):  # fifteen turns that say nothing about the caller keep everything
        facts = merge_facts(facts, {"senior_citizen": "unknown", "product": "", "tenure": ""})
    assert facts["senior_citizen"] == "yes" and facts["tenure"] == "2 years"
    assert merge_facts(facts, {"product": "FD"})["tenure"] == "2 years"  # "FD" and "fixed deposit" are one product
    moved = merge_facts(facts, {"product": "Zenith+ credit card", "monthly_spend": "40,000 on shopping"})
    assert moved["senior_citizen"] == "yes" and "tenure" not in moved and moved["monthly_spend"] == "40,000 on shopping"
    assert merge_facts(facts, {"senior_citizen": "no"})["senior_citizen"] == "no"  # a correction replaces it
    # only what matters for the product goes to the answer model
    assert describe_facts(moved, "credit_card") == "senior citizen: yes; monthly spend: 40,000 on shopping"
    assert "monthly spend" not in describe_facts(moved, "fd")


def test_strategy_and_flow_questions_are_decided_in_code():
    assert strategy("general", "choose", "credit_card", "I'm interested in credit cards") == "guided"
    assert strategy("general", "info", "savings_account", "minimum balance for all savings accounts") == "overview"
    assert strategy("general", "info", "fd", "Can I break an FD early?") == "direct"
    assert strategy("human", "info", "", "connect me") == "handoff"
    # progressive disclosure: broad -> short overview; a named detail -> just that; "everything" -> the full breakdown
    assert strategy("general", "info", "credit_card", "Tell me about the Zenith+ credit card") == "product_overview"
    assert strategy("general", "info", "credit_card", "What is the forex markup on the Zenith+ card?") == "direct"
    assert strategy("general", "info", "credit_card", "Give me all the features and charges of Zenith+") == "full"
    key, text = next_question("savings_account", {}, [], "en")
    assert key == "purpose" and text.startswith("Happy to help")
    # a known answer is never asked again: an occupation already says what the account is for
    assert next_question("savings_account", {"occupation": "student"}, [], "en")[0] == "balance_preference"
    assert next_question("fd", {"tenure": "1 year", "senior_citizen": "yes"}, [], "en") is None  # time to recommend


def test_fraud_team_request_is_a_transfer_and_otp_sharing_is_caught():
    assert asks_for_team("Please connect me to your fraud team", ["human"])
    assert not asks_for_team("Someone stole my card, connect me to the fraud team", ["human"])
    for said in ("maine apna OTP kisi ko bata diya", "I told him my OTP", "मैंने अपना ओटीपी किसी को बता दिया"):
        assert risk_flags(said) == ["otp_shared"], said
    assert risk_flags("what is an OTP") == []


def test_fraud_team_request_transfers_without_verification(client):
    reply = _ask(client, _session(client), "Please connect me to your fraud team")
    assert reply["outcome"] == "handoff" and "code" not in reply["answer"].lower()


def test_fewer_transactions_than_asked_is_said_honestly(client):
    sid = _session(client)
    first = _ask(client, sid, "Show my last 5 transactions on account 4512")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    reply = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]
    assert reply["answer"].startswith("There's only one transaction on your account ending 4512")
    assert "inactive" in reply["answer"]


def test_interest_in_credit_cards_asks_needs_first_without_verification(client, container):
    sid = _session(client)
    first = _ask(client, sid, "I'm interested in credit cards")
    assert first["outcome"] == "clarify" and "spend" in first["answer"] and not first.get("auth")
    second = _ask(client, sid, "About 30,000 a month, mostly shopping")
    assert second["outcome"] == "clarify" and "travel" in second["answer"]  # one question at a time
    assert "30,000" in container.traces.facts(sid)["monthly_spend"]  # the answer is kept for the recommendation


def test_general_fd_rule_then_offer_to_check_their_own(client, container):
    client.post("/v1/documents", files={"file": ("fd-rules-kb.md", b"# AU Fixed Deposit Rules\n## Premature withdrawal\n"
                                                 b"- A fixed deposit can be withdrawn before maturity with a penalty on the interest rate.\n",
                                                 "text/markdown")})
    container.worker.run_pending()
    sid = _session(client)
    reply = _ask(client, sid, "Can I withdraw a fixed deposit before maturity?")
    assert reply["outcome"] == "answered", reply
    assert not reply.get("auth")  # the general rule needs no verification
    assert "check this for your own FD" in reply["answer"]
    assert container.traces.pending(sid)["kind"] == "check_own"
    yes = _ask(client, sid, "yes please")
    assert yes["outcome"] == "auth_required" and yes["auth"]["status"] == "otp_sent"  # only now: verification


# ───────── answer scope: the router's view, sanity-checked in code; code patterns when the router gives none ─────────

def test_scope_from_the_router_is_used_only_when_it_makes_sense():
    q = "Compare Zenith+ and Altura"
    assert strategy("general", "info", "credit_card", q, "compare", ["Zenith+", "Altura"]) == "compare"
    assert strategy("general", "info", "credit_card", q, "compare", ["Zenith+"]) != "compare"  # one product is no comparison
    assert strategy("general", "info", "credit_card", "What is the Zenith+ annual fee?", "full", ["Zenith+"]) != "full"
    assert strategy("general", "info", "credit_card", "Zenith+ ki poori jaankari dijiye", "full", ["Zenith+"]) == "full"
    assert strategy("general", "info", "credit_card", "Zenith+ ke baare mein batao", "overview", ["Zenith+"]) == "product_overview"
    assert strategy("general", "info", "savings_account", "Minimum balance of all savings accounts?", "overview", []) == "overview"
    assert strategy("general", "info", "credit_card", "And what about its forex charges?", "specific", ["Zenith+"]) == "direct"
    assert strategy("general", "info", "credit_card", "Which card suits me?", "choose", []) == "guided"


def test_scope_patterns_without_the_router_in_three_languages():
    s = lambda q: strategy("general", "info", "credit_card", q)  # noqa: E731
    assert s("What are all the charges on Zenith+?") == "full"
    assert s("ज़ेनिथ+ के सभी शुल्क बताइए") == "full"
    assert s("Zenith+ ke baare mein batao") == "product_overview"
    assert s("Tell me about the international lounge benefits") == "direct"  # a named detail: just that detail
    assert s("And what about its forex charges?") == "direct"


def _hit(section: str, text: str, score: float) -> Hit:
    return Hit(chunk_id=section, text=text, section_path=section, document_id="d", document_title="KB", version_id="v",
               version_no=1, rerank_score=score)


ZENITH = _hit("CC KB › AU Zenith+ Credit Card › Fees", "Annual fee Rs 4,999. Forex markup 0.99%.", 0.98)
RATES = _hit("rates-at-a-glance › rates-at-a-glance", "Zenith+ card annual fee 4,999. Altura card 199.", 0.82)
OTHER = _hit("CC KB › AU Laksya Credit Card › Forex", "Forex markup 1.99% on international spends.", 0.40)
WEAK = _hit("CC KB › AU Zenith+ Credit Card › Golf", "Golf games at partner courses.", 0.03)


def test_passages_about_another_product_or_too_weak_are_not_sent():
    kept, notes = trim_context([ZENITH, RATES, OTHER, WEAK], named_products("Tell me about Zenith+", [], [ZENITH, OTHER]), "direct")
    assert kept == [ZENITH, RATES]  # the Laksya passage (1.99%) and the weak one are dropped; the rate card stays
    assert any("different product" in n for n in notes) and any("weak" in n for n in notes)
    # "Zenith" is a different card from "Zenith+"
    assert named_products("What is the Zenith card fee?", [], [_hit("CC KB › AU Zenith Credit Card › Fees", "x", 0.9), ZENITH]) == ["Zenith"]
    # for "which card suits me" every product may be used
    assert OTHER in trim_context([ZENITH, OTHER], ["Zenith+"], "recommend")[0]
    # a near-copy of a kept passage is dropped
    copy = _hit("CC KB › AU Zenith+ Credit Card › Fees (v2)", "Annual fee Rs 4,999. Forex markup 0.99%.", 0.97)
    assert trim_context([ZENITH, copy], ["Zenith+"], "direct")[0] == [ZENITH]


def test_a_comparison_never_describes_a_side_it_cannot_see():
    assert missing_products([ZENITH, OTHER], ["Zenith+", "Laksya"]) == []
    assert missing_products([ZENITH], ["Zenith+", "Altura"]) == ["Altura"]
    assert missing_products([ZENITH, RATES], ["Zenith+", "Altura"]) == []  # the rate card mentions Altura


# ───────── a router reply that breaks the strict schema is repaired in code when that is safe ─────────

def test_rejected_router_output_is_repaired_without_a_second_call():
    import json

    from app.services.understanding import kind_of, repair

    gen = {"language": "en", "requests": [{"scope": "compare", "kind": "compare", "question": "Zenith+ vs Altura",
                                           "personal": None, "action": None, "issue": "none", "product": "credit_card",
                                           "named": ["Zenith+", "Altura"], "own_check": False}]}
    body = json.dumps({"error": {"code": "json_validate_failed", "failed_generation": json.dumps(gen)}})
    data, note = repair(body)
    assert data is not None and note == "kind=compare"
    assert repair(json.dumps({"error": {"failed_generation": '{"language": "en", "requ'}}))[0] is None  # cut off: retry
    # an unknown kind never hides a personal lookup, an action or a request for a person
    assert kind_of({"kind": "compare"}) == "general"
    assert kind_of({"kind": "balance", "personal": {"topic": "balance"}}) == "personal"
    assert kind_of({"kind": "card_block", "action": {"type": "block"}}) == "action"
    assert kind_of({"kind": "agent"}) == "human" and kind_of({"kind": "complaint"}) == "issue"


def test_a_passage_that_only_mentions_the_product_is_labelled():
    from app.services.kbmeta import mention_note

    kids = _hit("CASA+TD KB › AU Kids Savings Account › Comparison", "Kids: special benefits. Digital: RuPay Platinum.", 0.7)
    assert mention_note(kids, ["Digital"]) == " [about Kids; use only what it says about Digital]"
    assert mention_note(ZENITH, ["Zenith+"]) == "" and mention_note(RATES, ["Zenith+"]) == ""


# ───────── bugs found by the live text test on 7 Nov (one test each) ─────────

def test_live_bugs_router_safety_nets():
    from app.domain import Request, Understanding
    from app.services.understanding import amount_said, correct

    # "Connect me to my relationship manager" was taken as an answer to the previous question
    u = correct(Understanding("en", "personal", "fee?", "", requests=[Request("personal", "fee?", {"topic": "charges"})]),
                "Connect me to my relationship manager")
    assert u.intent == "human" and [r.kind for r in u.requests] == ["human"]
    # "switch on international payments on my debit card" was taken as a general question
    u = correct(Understanding("en", "general", "x", "", requests=[Request("general", "x")]),
                "Please switch on international payments on my debit card")
    assert u.intent == "action" and u.action["type"] == "controls" and u.action["international"] == "on" and u.action["card"] == "debit"
    # the router left out the amount of a reported card payment; a card ending is never taken for an amount
    assert amount_said("I don't recognise a transaction of 8499 on my credit card") == 8499
    assert amount_said("a debit of 10,000 on my card ending 6712") == 10000


def test_live_bugs_thanks_generic_card_and_fd_wording():
    from app.services.conversation import _specific_item
    from app.services.smalltalk import is_courtesy

    assert is_courtesy("धन्यवाद") and is_courtesy("Thanks, that's all") and not is_courtesy("thanks, what is my balance?")
    assert _specific_item("credit") == "" and _specific_item("6712") == "6712" and _specific_item("zenith") == "zenith"


def test_live_bug_rate_question_keeps_the_domestic_rate_table():
    rates = dict(document_id="d", document_title="rates-at-a-glance", version_id="v", version_no=1)
    fcnr = Hit(chunk_id="a", text="Rate of Interest FCNR (B): USD 1 year: 5.30%", section_path="rates-at-a-glance", rerank_score=0.42, **rates)
    domestic = Hit(chunk_id="b", text="Domestic term deposits 24 months senior 7.60%", section_path="rates-at-a-glance › Term Deposits",
                   rerank_score=0.059, **rates)
    kept, notes = trim_context([fcnr, domestic], [], "direct", "What is the FD interest rate for 2 years for a senior citizen?")
    assert kept == [domestic] and any("foreign-currency" in n for n in notes)
    assert fcnr in trim_context([fcnr, domestic], [], "direct", "What is the FCNR rate for USD deposits?")[0]


def test_card_not_working_checks_the_card_and_offers_the_fix(client, container):
    sid = _session(client, "9000000103")  # Arjun: debit card temporarily blocked, shop payments off
    first = _ask(client, sid, "My debit card is not working at shops")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    reply = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]
    assert "blocked" in reply["answer"] and "unblock your card ending" in reply["answer"]  # the cause, then the fix offered
    assert "how much" not in reply["answer"].lower()  # not treated as a payment dispute
    assert container.traces.pending(sid)["kind"] == "action"  # a yes unblocks it (read back first)


def test_blocked_card_unblock_then_carries_on_with_the_requested_setting(client, container):
    sid = _session(client, "9000000103")  # Arjun: debit card ending 5521 temporarily blocked, international off
    first = _ask(client, sid, "Please switch on international payments on my debit card")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    reply = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"]
    assert reply["outcome"] == "confirm" and "unblock" in reply["answer"]
    after = _ask(client, sid, "yes")  # unblocked, and the request is not forgotten: the next step is asked straight away
    assert after["outcome"] == "confirm" and "international" in after["answer"].lower() and "AU" in after["answer"]
    done = _ask(client, sid, "yes")
    assert done["outcome"] == "action_done"


def test_card_call_cfdda8da_keeps_the_shop_problem_after_another_request(client, container):
    """The live call: card not working at shops -> "can I use it abroad?" -> "switch on international" -> yes."""
    def arjun_card(status: str, on: bool) -> None:  # the mock bank is shared by the test session: start from his seeded card
        with container.db.connection() as conn:
            conn.execute("UPDATE corebank.debit_cards SET status = %s, pos = %s, international = %s WHERE last4 = '5521'",
                         (status, on, on))
    arjun_card("temp_blocked", False)
    sid = _session(client, "9000000103")  # Arjun: debit card 5521 temporarily blocked, shop payments and international off
    first = _ask(client, sid, "My debit card is not working at shops")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    client.post("/v1/auth/verify", json={"session_id": sid, "code": code})
    abroad = _ask(client, sid, "Can I use it abroad? I'm travelling next week")
    assert "complaint" not in abroad["answer"].lower() and "how much" not in abroad["answer"].lower()
    switch = _ask(client, sid, "Please switch on international payments on my debit card")
    assert switch["outcome"] == "confirm" and "unblock" in switch["answer"]  # an action, not a general answer
    assert "international" in _ask(client, sid, "yes")["answer"].lower()
    done = _ask(client, sid, "yes")  # international switched on; the shop problem is brought back, not forgotten
    assert done["outcome"] == "confirm" and "still switched off" in done["answer"] and "switch on shop payments" in done["answer"]
    assert _ask(client, sid, "yes")["outcome"] == "action_done"
    arjun_card("temp_blocked", False)


def test_each_turn_records_the_bank_systems_it_called(client, container):
    sid = _session(client)
    first = _ask(client, sid, "What is my balance in both accounts?")
    code = re.search(r"(\d{6})", first["auth"]["simulated_sms"]).group(1)
    client.post("/v1/auth/verify", json={"session_id": sid, "code": code})
    turns = client.get(f"/v1/calls/{sid}").json()["turns"]
    calls = [c for t in turns for c in t["bank_calls"]]
    systems = {c["system"] for c in calls}
    assert {"cif", "cbs"} <= systems and all(c["status"] == 200 for c in calls if c["system"] == "cbs")
    assert not any(re.search(r"\d{6,}", c["path"]) for c in calls)  # account numbers masked to the last 4 digits


# ───────── bugs found by the long mixed-language test calls (7 Nov) ─────────

def test_long_call_bugs_routing_and_topics():
    from app.domain import Request, Understanding
    from app.services.conversation import personal_query
    from app.services.understanding import own_data

    def routed(text):
        return own_data(Understanding("en", "general", "x", "", requests=[Request("general", "x")]), text)

    assert routed("Aur personal loan ki EMI kyun bounce hui?").personal == {"topic": "loan", "detail": "emi_status", "item": "personal"}
    assert routed("Did they charge me anything for that bounce?").personal["detail"] == "bounce_charges"
    assert routed("mujhe apne account ke charges ke baare mein jaanna hai").personal["topic"] == "charges"
    assert routed("How do I avoid EMI bounce charges?").intent == "general"  # a how-to question stays general
    # "was my UPI payment to Suresh completed?" stays a transaction search (payment_status is not only cards)
    assert personal_query({"topic": "transactions", "detail": "payment_status", "keyword": "suresh"}).topic == "transactions"


def test_long_call_bug_card_dues_are_not_a_transaction_search(container):
    from app.services.personal import PersonalQuery, answer

    ravi = container.bank.customer(container.bank.find_by_mobile(RAVI).cif)
    out = " ".join(answer(ravi, PersonalQuery(topic="credit_card", detail="amount_due", keyword="balance"), "en",
                          container.settings.demo_today).sentences)
    assert "matching transaction" not in out and "₹" in out


def test_long_call_bug_rate_card_rows_survive_trimming():
    rates = dict(document_id="d", document_title="rates-at-a-glance", version_id="v", version_no=1)
    overview = Hit(chunk_id="a", text="Interest rates up to 7.35% p.a. for general customers.",
                   section_path="CASA+TD KB › AU Term Deposits › Key Features", rerank_score=0.61, **rates | {"document_title": "CASA+TD KB"})
    table = Hit(chunk_id="b", text="Domestic term deposits 12 months: 7.10%", section_path="rates-at-a-glance › Term Deposits",
                rerank_score=0.049, **rates)
    kept, _ = trim_context([overview, table], [], "direct", "What is the FD interest rate for 1 year for a regular customer?")
    assert table in kept
