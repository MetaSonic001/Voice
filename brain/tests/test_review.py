"""Phase 4 step 8: the post-call review (code checks, judge selection, verified evidence, calibration) and the AI handover summary."""

import random
import re

import pytest

from app.services import review as rv
from app.services.summary import Summariser

RAVI = "9000000101"


def _ask(client, sid, text):
    return client.post("/api/v1/ask", json={"question": text, "session_id": sid}).json()


class Judge:
    """A scripted judge: returns what the test says, and remembers it was called."""

    def __init__(self, **over):
        self.calls, self.over = 0, over

    async def json(self, model, system, user, schema, max_tokens=400, effort=None):
        self.calls += 1
        if "handover note" in system:
            return {"summary": self.over.get("summary", "The caller wants their balance."), "mood": "calm"}, {}
        base = {"dimension_scores": {"task_completion": 5, "correctness": 5, "conversation": 4, "compliance": 5, "handoff": 0},
                "evidence": [], "critical_failure": False, "needs_human_review": False, "failure_categories": [],
                "recommended_fix": "none"}
        return base | self.over, {}


def _call(client, container, questions, caller=None):
    sid = client.post("/api/v1/sessions", json={"caller_number": caller} if caller else {}).json()["id"]
    for q in questions:
        _ask(client, sid, q)
    return sid


async def test_free_checks_always_run_and_flag_a_caller_repeating_themselves(client, container, monkeypatch):
    judge = Judge()
    monkeypatch.setattr(container.reviews, "llm", judge)
    monkeypatch.setattr(container.settings, "judge_mode", "selective")
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"] * 2)
    row = await container.reviews.review(sid)
    checks = {c["name"]: c for c in row["checks"]}
    assert checks["caller_not_repeating_themselves"]["ok"] is False and checks["no_personal_data_in_text"]["ok"]
    assert row["needs_review"] is False and row["mode"] == "judged"  # a warn is judged, a person is asked only for a fail or a doubt
    assert judge.calls == 1


async def test_a_clean_call_is_judged_only_when_sampled(client, container, monkeypatch):
    judge = Judge()
    monkeypatch.setattr(container.reviews, "llm", judge)
    monkeypatch.setattr(container.settings, "judge_mode", "selective")
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    monkeypatch.setattr(container.reviews.rng, "random", lambda: 0.99)  # not in the 10% sample
    row = await container.reviews.review(sid)
    assert row["mode"] == "code_only" and judge.calls == 0 and "not sampled" in row["reason"]
    monkeypatch.setattr(container.reviews.rng, "random", lambda: 0.01)
    assert (await container.reviews.review(sid))["mode"] == "judged"


async def test_development_mode_judges_every_call_and_off_judges_none(client, container, monkeypatch):
    judge = Judge()
    monkeypatch.setattr(container.reviews, "llm", judge)
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    monkeypatch.setattr(container.settings, "judge_mode", "all")
    assert (await container.reviews.review(sid))["mode"] == "judged"
    monkeypatch.setattr(container.settings, "judge_mode", "off")
    assert (await container.reviews.review(sid))["mode"] == "code_only"


def test_risky_calls_are_always_judged_in_selective_mode():
    rng = random.Random(1)
    base = {"turns": [{"intent": "general", "outcome": "answered"}], "events": [], "alerts": []}
    assert rv.judge_reason({}, base, [], "selective", random.Random(0)) in (None, "random sample of clean calls")
    risky = base | {"turns": [{"intent": "fraud", "outcome": "confirm"}]}
    assert "fraud" in rv.judge_reason({}, risky, [], "selective", rng)
    acted = base | {"events": [{"kind": "card_action", "detail": {}}]}
    assert "action" in rv.judge_reason({}, acted, [], "selective", rng)
    failed = [rv.Check("replies_fast_enough", False, "warn", "slow")]
    assert "code check" in rv.judge_reason({}, base, failed, "selective", rng)


def test_invented_evidence_is_dropped_and_an_uncheckable_critical_claim_goes_to_a_person():
    packet = {"turns": [{"n": 1, "caller": "what is my balance", "agent": "Your balance is rupees one thousand"}]}
    judge = {"evidence": [{"dimension": "correctness", "turn": 1, "quote": "Your balance is"},
                          {"dimension": "correctness", "turn": 1, "quote": "I promise a refund"},
                          {"dimension": "compliance", "turn": 9, "quote": "balance"}]}
    kept, dropped = rv.verify_evidence(judge, packet)
    assert [e["quote"] for e in kept] == ["Your balance is"] and dropped == 2
    # the same quote for several dimensions is kept once, with all of them
    same = {"evidence": [{"dimension": d, "turn": 1, "quote": "Your balance is"} for d in ("correctness", "conversation", "correctness")]}
    kept, dropped = rv.verify_evidence(same, packet)
    assert len(kept) == 1 and kept[0]["dimensions"] == ["correctness", "conversation"] and dropped == 0


async def test_a_critical_claim_with_no_real_evidence_needs_a_person(client, container, monkeypatch):
    judge = Judge(critical_failure=True, evidence=[{"dimension": "compliance", "turn": 1, "quote": "never said this"}])
    monkeypatch.setattr(container.reviews, "llm", judge)
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    row = await container.reviews.review(sid, force_judge=True)
    assert row["critical"] and row["needs_review"] and row["judge"]["dropped_evidence"] == 1


def test_review_api_labels_and_calibration(client, container, monkeypatch):
    monkeypatch.setattr(container.reviews, "llm", Judge())
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    review = client.post(f"/api/v1/calls/{sid}/review").json()
    assert review["mode"] == "judged" and review["score"] == 4.8
    assert client.get(f"/api/v1/calls/{sid}").json()["review"]["session_id"] == sid
    row = next(r for r in client.get("/api/v1/calls?limit=200").json() if r["id"] == sid)
    assert row["review_score"] == 4.8 and row["review_mode"] == "judged"
    labelled = client.post(f"/api/v1/calls/{sid}/review/human", json={"scores": {"task_completion": 4, "correctness": 5, "conversation": 3,
                                                                           "compliance": 5}, "critical": False, "note": "ok"}).json()
    assert labelled["human"]["scores"]["conversation"] == 3
    cal = client.get("/api/v1/reviews/calibration").json()
    assert cal["labelled_calls"] >= 1 and cal["agreement_within_one_point"]["conversation"] == 100 and cal["critical_failure_recall"] is None
    # the judge said 4 for conversation, the person 3: not an exact match, and the judge is the more generous one
    assert cal["exact_agreement"]["conversation"] < 100 and cal["judge_minus_human"]["conversation"] > 0
    assert cal["missed_critical_calls"] == []


async def test_a_failed_hard_rule_is_critical_whatever_the_judge_says(client, container, monkeypatch):
    from app.services import review as review_module

    monkeypatch.setattr(container.reviews, "llm", Judge())  # the judge thinks the call was fine (average 4.8)
    real = review_module.code_checks
    monkeypatch.setattr(review_module, "code_checks", lambda call, packet: real(call, packet) + [
        review_module.Check("auth_before_account_data", False, "fail", "balance said before verification")])
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    row = await container.reviews.review(sid, force_judge=True)
    assert row["score"] == 4.8 and row["critical"] and row["needs_review"]  # the good average never hides it


def test_flagged_calls_can_be_listed_on_their_own(client, container, monkeypatch):
    monkeypatch.setattr(container.reviews, "llm", Judge(needs_human_review=True))
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    client.post(f"/api/v1/calls/{sid}/review")
    assert sid in [r["id"] for r in client.get("/api/v1/calls?needs_review=true").json()]


async def test_ai_summary_is_kept_only_when_every_figure_is_in_the_record(client, container):
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    h = container.handoffs.create(sid, "general", "test", {"summary": "brief"}, "chat")
    good = Summariser(Judge(summary="The caller asked about the minimum balance."), "m", container.audit, container.handoffs)
    assert (await good.summarise(sid, h["id"]))["label"] == "AI summary"
    assert container.handoffs.get(h["id"])["note"]["ai_summary"]["text"].startswith("The caller asked")
    bad = Summariser(Judge(summary="The caller lost ₹98,765 yesterday."), "m", container.audit, container.handoffs)
    container.handoffs.add_note(h["id"], {"ai_summary": None})
    assert await bad.summarise(sid, h["id"]) is None  # an invented figure: discarded, the deterministic brief stands alone
    assert container.handoffs.get(h["id"])["note"]["ai_summary"] is None


def test_the_desk_sees_the_transcript_read_only(client, container):
    sid = client.post("/api/v1/sessions", json={"caller_number": "9000000001"}).json()["id"]
    handed = _ask(client, sid, "I want to talk to a human agent")
    detail = client.get(f"/api/v1/desk/handoffs/{handed['handoff']['id']}").json()
    assert detail["transcript"][0]["caller"] == "I want to talk to a human agent" and detail["verification"]["verified"] is False
    assert detail["note"]["headline"]
    assert not re.search(r"\d{9,}", str(detail["transcript"]))


def test_findings_keep_only_real_quotes_and_give_the_call_its_labels():
    packet = {"turns": [{"n": 1, "caller": "can I break my FD", "agent": "Yes, you can close it early, but a penalty applies"}]}
    judge = {"findings": [
        {"category": "poor_voice_response", "source": "agent", "turn": 1, "quote": "you can close it early",
         "what_happened": "x", "why_it_matters": "y", "fix": "z", "fix_area": "answer_prompt"},
        {"category": "missing_information", "source": "knowledge_gap", "turn": 1, "quote": "an invented line",
         "what_happened": "x", "why_it_matters": "y", "fix": "z", "fix_area": "knowledge_base"}]}
    found, unverified = rv.verify_findings(judge, packet)
    assert unverified == 1 and found[0]["quote_verified"] and found[1]["quote"] == "" and not found[1]["quote_verified"]
    assert rv.categories_of(found) == ["poor_voice_response", "missing_information"]  # the agent's own problems first


def test_findings_missing_schema_fields_do_not_break_the_review():
    """The judge model sometimes leaves out required fields (seen live: no "category"); the review must still work."""
    packet = {"turns": [{"n": 1, "caller": "hi", "agent": "hello"}]}
    judge = {"findings": [{"turn": 1, "quote": "hello", "what_happened": "x", "fix": "z"},
                          {"category": "made_up", "source": "??", "what_happened": "y"},
                          {"turn": 1, "quote": "hello"}, "not a dict"]}
    found, _ = rv.verify_findings(judge, packet)
    assert len(found) == 2 and [f["category"] for f in found] == ["other", "other"]
    assert found[1]["source"] == "agent" and found[1]["fix"] == "" and found[1]["fix_area"] == "none"
    assert rv.categories_of(found) == ["other"]


async def test_summary_counts_recurring_problems_across_calls(client, container, monkeypatch):
    finding = {"category": "poor_voice_response", "source": "agent", "turn": 1, "quote": "", "what_happened": "one long sentence",
               "why_it_matters": "hard to follow", "fix": "split it", "fix_area": "answer_prompt"}
    monkeypatch.setattr(container.reviews, "llm", Judge(findings=[finding]))
    for _ in range(2):
        sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
        await container.reviews.review(sid, force_judge=True)
    summary = client.get("/api/v1/reviews/summary").json()
    top = next(i for i in summary["recurring_issues"] if i["category"] == "poor_voice_response")
    assert top["calls"] >= 2 and top["agent_caused"] >= 2 and top["example"]["fix_area"] == "answer_prompt"
    assert summary["agent_issues_by_fix_area"]["answer_prompt"] >= 2 and summary["judged_calls"] >= 2


async def test_findings_become_a_fix_list_that_keeps_what_was_marked_done(client, container, monkeypatch):
    finding = {"category": "lost_context", "source": "agent", "turn": 1, "quote": "", "what_happened": "forgot the account",
               "why_it_matters": "caller repeats", "fix": "keep the account in state", "fix_area": "router"}
    monkeypatch.setattr(container.reviews, "llm", Judge(findings=[finding]))
    sid = _call(client, container, ["What is the minimum balance requirement for the savings account?"])
    await container.reviews.review(sid, force_judge=True)
    mine = [f for f in client.get("/api/v1/fixes").json() if str(f["session_id"]) == sid]
    assert len(mine) == 1 and mine[0]["turn"] == 1 and mine[0]["fix_area"] == "router"
    assert client.patch(f"/api/v1/fixes/{mine[0]['id']}", json={"status": "done", "note": "fixed in prompt"}).json()["status"] == "done"
    await container.reviews.review(sid, force_judge=True)  # a new review keeps the done to-do and does not add it again
    rows = [f for f in client.get("/api/v1/fixes?status=all").json() if str(f["session_id"]) == sid]
    assert [r["status"] for r in rows] == ["done"]


def test_the_systems_re_ask_after_verification_is_not_the_caller_repeating():
    base = {"intent": "personal", "answer": "x", "total_ms": 10, "bank": None, "sources": [], "citations": [], "timings": {}}
    old = {"turns": [base | {"question": "Why was the annual fee charged?", "outcome": "auth_required", "input": "typed"},
                     base | {"question": "Why was the annual fee charged?", "outcome": "answered", "input": "typed"}],
           "events": [], "alerts": [], "auth_events": []}
    packet = rv.build_packet(old)  # an older call: recognised by the same question right after "verify first"
    assert packet["turns"][1]["input"] == rv.REASK
    assert next(c for c in rv.code_checks(old, packet) if c.name == "caller_not_repeating_themselves").ok
    new = {**old, "turns": [old["turns"][0], old["turns"][1] | {"input": "after_verification_chat"}]}
    assert rv.build_packet(new)["turns"][1]["input"] == rv.REASK
