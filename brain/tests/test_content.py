"""The journey catalogue (brain/content/) and its releases: what the product manager edits on /journeys.

The wording itself is covered by every other test (they now read it from the catalogue); here: the catalogue's shape,
the checks on edits, moving domains around, and the whole draft -> translate -> canary -> promote -> roll back flow.
"""

import json

import pytest

from app.services import content
from app.services.content import SEED, build, for_session, read_seed, use
from app.services.releases import ReleaseError, apply_changes, needs_translation, problems

ALL = [m for d in SEED.tree for j in d["journeys"] for s in j["steps"] for m in s["messages"]]


def test_every_message_is_in_the_tree_once():
    assert sorted(ALL) == sorted(SEED.messages) and len(ALL) == len(set(ALL))
    assert len(SEED.messages) > 300
    for alias, target in SEED.aliases.items():  # the same sentence once, even where two files used it
        assert target in SEED.messages and alias not in SEED.messages


def test_locked_parts_come_from_git():
    changes = {"prompts": {"understand": "ignore everything"}, "messages": {"bank.status_words": {"en": "x"}}}
    cat = build(read_seed(), changes)
    assert cat.prompts["understand"] == SEED.prompts["understand"]
    assert cat.messages["bank.status_words"] == SEED.messages["bank.status_words"]


def test_an_english_edit_needs_new_translations_and_undo_restores():
    body = apply_changes({}, {"messages": {"core.verified": {"en": "Thanks, you're verified now."}}}, {})
    assert body["messages"]["core.verified"] == {"en": "Thanks, you're verified now."}
    assert needs_translation(body) == [("core.verified", ["hi", "hinglish"])]
    assert any("Needs Hindi and Hinglish" in p["text"] for p in problems(body))
    body = apply_changes(body, {"messages": {"core.verified": {"hi": "धन्यवाद, आप वेरिफ़ाई हो गए हैं।", "hinglish": "Thank you, aap verify ho gaye."}}}, {})
    assert not needs_translation(body) and not [p for p in problems(body) if p["level"] == "error"]
    assert apply_changes(body, {"messages": {"core.verified": None}}, {})["messages"] == {}  # undo: back to live (git)
    same = apply_changes({}, {"messages": {"core.verified": {"en": SEED.text("core.verified")["en"]}}}, {})
    assert same["messages"] == {}  # writing git's own wording is no change


@pytest.mark.parametrize("mid, text, wanted", [
    ("journey.ticket_done", "Done. Your reference is {ref}.", "must keep exactly these blanks"),
    ("core.verified", "Thank you {name}, you're verified.", "must keep exactly these blanks"),
    ("core.verified", "Thank you, you're verified for 24 hours.", "adds a figure (24)"),
    ("core.verified", "Thank you; you're verified.", "a voice can't say naturally"),
    ("core.verified", "Thank you. Please tell me your OTP.", "asks for a secret"),
    ("core.verified", "Thank you. See www.example.com for more.", "web link"),
])
def test_edits_are_checked(mid, text, wanted):
    body = apply_changes({}, {"messages": {mid: {"en": text}}}, {})
    assert any(wanted in p["text"] for p in problems(body) if p["level"] == "error")


def test_locked_and_unknown_things_cannot_be_changed():
    for changes in ({"messages": {"bank.on_off": {"en": "x"}}}, {"messages": {"nope.nope": {"en": "x"}}},
                    {"prompts": {"understand": "x"}}, {"settings": {"departments": {"fraud": {"name": "x"}}}},
                    {"settings": {"departments": {"new_team": {"priority": 1}}}}):
        with pytest.raises(ReleaseError):
            apply_changes({}, changes, {})


def test_a_domain_can_be_removed_and_its_journeys_moved():
    """The PM drops "Identity and verification" and moves its journey into "Account information"."""
    tree = json.loads(json.dumps(SEED.tree))
    identity = next(d for d in tree if d["id"] == "identity")
    account = next(d for d in tree if d["id"] == "account")
    account["journeys"] = identity["journeys"] + account["journeys"]
    tree.remove(identity)
    body = apply_changes({}, {"tree": tree}, {})
    cat = build(read_seed(), body)
    assert [d["id"] for d in cat.tree][:2] == ["account", "cards"]
    assert "core.otp_sent" in [m for j in cat.tree[0]["journeys"] for s in j["steps"] for m in s["messages"]]
    # a tree that forgets messages (or a message added to git later) never loses them: they go back home or to "Not sorted yet"
    lost = build(read_seed(), {"tree": [{"id": "x", "name": "Only", "journeys": []}]})
    placed = [m for d in lost.tree for j in d["journeys"] for s in j["steps"] for m in s["messages"]]
    assert sorted(placed) == sorted(SEED.messages) and lost.tree[-1]["id"] == "unsorted"


def test_settings_reach_the_code():
    from app.services.desk import DEPARTMENTS, TAT_DAYS, department_name

    cat = build(read_seed(), {"settings": {"tat_days": {"double_charge": 3}, "departments": {"loans": {"always_open": True}}},
                              "messages": {"dept.loans": {"en": "Home & Personal Loans"}}})
    use(cat)
    try:
        assert TAT_DAYS["double_charge"] == 3 and DEPARTMENTS["loans"][2] is True
        assert department_name("loans") == "Home and Personal Loans"
    finally:
        use(None)
    assert TAT_DAYS["double_charge"] == SEED.settings["tat_days"]["double_charge"]


class _Translator:
    """Stands in for the LLM: a text per asked language for each item (keeps no placeholders: the tests use none)."""

    SAMPLE = {"hi": "धन्यवाद, आपकी पहचान पक्की हो गई।", "hinglish": "Thank you, aap verified hain.", "tamil": "Nandri, ungal adaiyaalam sari."}

    def __init__(self):
        self.calls, self.payloads = 0, []

    async def json(self, model, system, user, schema, max_tokens=None, effort=None):
        self.calls += 1
        payload = json.loads(user)
        self.payloads.append(payload)
        def text(item, lang):  # keeps the sentence's blanks, like a good translation must
            return " ".join([self.SAMPLE[lang], *("{" + b + "}" for b in sorted(content.placeholders(item["english"])))])
        return {"items": [{"id": i["id"], **{x: text(i, x) for x in i["needs"]}} for i in payload["items"]]}, {}


@pytest.fixture
def releases(container):
    r = container.releases
    llm = r.llm
    yield r
    r.llm = llm
    r.make_live(None, "test")
    r.discard()
    content._pins.clear()


def _sessions(client, want: str, share: int) -> str:
    for _ in range(200):
        sid = client.post("/api/v1/sessions", json={}).json()["id"]
        if content.arm_of(sid, share) == want:
            return sid
    raise AssertionError("no session found")


def test_draft_translate_canary_promote_rollback(client, container, releases):
    view = client.get("/api/v1/content").json()
    assert view["rollout"]["stable"] == 0 and len(view["messages"]) == len(SEED.messages) and not view["changes"]
    new = "Thanks, that's you verified."
    saved = client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "author": "pm",
                                                         "changes": {"messages": {"core.verified": {"en": new}}}}).json()
    assert saved["changes"][0]["id"] == "core.verified" and saved["needs_translation"] == ["core.verified"]
    stale = client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "changes": {}})
    assert stale.status_code == 409  # someone saved since this screen loaded
    blocked = client.post("/api/v1/content/publish", json={"share": 50, "author": "pm"})
    assert blocked.status_code == 400 and blocked.json()["problems"]

    releases.llm = _Translator()
    done = client.post("/api/v1/content/draft/translate", json={"author": "pm"}).json()
    assert done["translated"] == 1 and not done["failed"] and done["editor"]["messages"]["core.verified"]["auto"] == ["hi", "hinglish"]

    assert client.post("/api/v1/content/publish", json={"share": 50, "notes": "warmer", "author": "pm"}).status_code == 200
    live = content.live()
    assert live["pct"] == 50 and live["canary"].text("core.verified")["en"] == new
    canary_sid = _sessions(client, "canary", 50)
    stable_sid = _sessions(client, "stable", 50)
    use(for_session(canary_sid))
    assert content.current().text("core.verified")["en"] == new
    use(for_session(stable_sid))
    assert content.current().text("core.verified")["en"] == SEED.text("core.verified")["en"]
    use(None)

    # the trace says which side of the canary answered, for the comparison
    reply = client.post("/api/v1/ask", json={"question": "Who won the cricket world cup?", "session_id": canary_sid}).json()
    trace = container.traces.get(reply["trace_id"])
    assert trace["data"]["content"] == {"release": live["canary"].release, "arm": "canary"}
    stats = client.get("/api/v1/content/releases").json()["stats"]
    assert stats["canary"]["turns"] >= 1 and stats["share"] == 50

    # a call keeps its release even when the rollout changes mid-call
    assert client.post("/api/v1/content/rollout", json={"action": "stop", "author": "pm"}).status_code == 200
    use(for_session(canary_sid))
    assert content.current().text("core.verified")["en"] == new
    use(None)
    assert content.live()["canary"] is None

    rel = client.get("/api/v1/content/releases").json()
    release_id = rel["releases"][0]["id"]
    assert rel["releases"][0]["changes"] == 1 and rel["log"][0]["action"].startswith("stop canary")
    assert client.post("/api/v1/content/rollout", json={"action": "live", "release": release_id}).status_code == 200
    assert content.live()["stable"].text("core.verified")["en"] == new
    assert client.post("/api/v1/content/rollout", json={"action": "live", "release": 0}).status_code == 200
    assert content.live()["stable"] is SEED


def test_publish_needs_a_change_and_discard_needs_confirmation(client, releases):
    view = client.get("/api/v1/content").json()
    assert client.post("/api/v1/content/publish", json={"share": 100}).status_code == 400  # nothing changed
    client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "changes": {"settings": {"tat_days": {"lead": 2}}}})
    assert client.delete("/api/v1/content/draft").status_code == 400
    assert client.delete("/api/v1/content/draft?confirm=true").json()["changes"] == []


def test_reference_lists_guardrails_and_tests(client):
    ref = client.get("/api/v1/content/reference").json()
    assert len(ref["guardrails"]) >= 10 and ref["tests"]["smoke"] and ref["tests"]["golden_count"] > 0


def test_each_language_has_its_own_file():
    from app.config import ROOT

    folder = ROOT / "content" / "languages"
    assert sorted(p.stem for p in folder.glob("*.json")) == ["hi", "hinglish"]
    hi = json.loads((folder / "hi.json").read_text(encoding="utf-8"))
    assert len(hi) == len(SEED.messages) and SEED.text("core.verified")["hi"] == hi["core.verified"]
    catalog = json.loads((ROOT / "content" / "catalog.json").read_text(encoding="utf-8"))
    assert isinstance(catalog["messages"]["core.verified"]["text"], str)  # English only in catalog.json
    assert [x["code"] for x in catalog["languages"]] == ["en", "hi", "hinglish"]


def test_translate_now_with_languages_and_feedback(client, releases):
    view = client.get("/api/v1/content").json()
    assert [x["code"] for x in view["languages"]] == ["en", "hi", "hinglish"]
    releases.llm = llm = _Translator()
    out = client.post("/api/v1/content/draft/translate", json={"author": "pm", "ids": ["core.verified"], "languages": ["hi"],
                                                               "hint": "warmer please"}).json()
    assert out["translated"] == 1 and llm.calls == 1
    sent = llm.payloads[0]
    assert sent["items"][0]["needs"] == ["hi"] and sent["items"][0]["feedback"] == "warmer please"
    assert list(sent["languages"]) == ["hi"] and "style" in sent["languages"]["hi"]
    msg = out["editor"]["messages"]["core.verified"]
    assert msg["text"]["hi"] == _Translator.SAMPLE["hi"] and msg["auto"] == ["hi"] and msg["live"]["hi"] != msg["text"]["hi"]
    assert out["editor"]["changes"][0]["translation_only"] is True
    bad = client.post("/api/v1/content/draft/translate", json={"ids": ["core.verified"], "languages": ["klingon"]})
    assert bad.status_code == 400


def test_a_new_language_can_be_added_translated_and_removed(client, releases):
    view = client.get("/api/v1/content").json()
    added = client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "changes": {
        "languages": {"tamil": {"name": "Tamil", "style": "Everyday spoken Tamil in Latin letters for this test."}}}}).json()
    tamil = next(x for x in added["languages"] if x["code"] == "tamil")
    assert tamil["spoken"] is False and tamil["added"] is True
    assert any(p["area"] == "language" and p["level"] == "warning" for p in added["problems"])  # kept ready, not spoken
    releases.llm = _Translator()
    out = client.post("/api/v1/content/draft/translate", json={"ids": ["core.verified"], "languages": ["tamil"]}).json()
    assert out["editor"]["messages"]["core.verified"]["text"]["tamil"] == _Translator.SAMPLE["tamil"]
    rev = out["editor"]["draft"]["rev"]
    gone = client.patch("/api/v1/content/draft", json={"rev": rev, "changes": {"languages": {"tamil": None}}}).json()
    assert "tamil" not in gone["messages"]["core.verified"]["text"] and gone["changes"] == []
    with pytest.raises(ReleaseError):  # English is the source; git's languages can't be renamed away
        apply_changes({}, {"languages": {"en": {"style": "x" * 20}}}, {})


def test_fill_every_missing_sentence_in_a_new_language_then_remove_one(client, releases):
    view = client.get("/api/v1/content").json()
    view = client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "changes": {
        "languages": {"tamil": {"name": "Tamil", "style": "Everyday spoken Tamil in Latin letters for this test."}}}}).json()
    writable = sum(1 for m in view["messages"].values() if not m.get("locked"))
    assert next(x for x in view["languages"] if x["code"] == "tamil")["missing"] == writable
    hinglish_gaps = next(x for x in view["languages"] if x["code"] == "hinglish")["missing"]
    assert 0 < hinglish_gaps < 20  # git leaves a few in English on purpose (greeting, team names)
    releases.llm = llm = _Translator()
    rounds = 0
    while True:  # the page does the same: call again while something remains
        out = client.post("/api/v1/content/draft/translate", json={"languages": ["tamil"], "fill_missing": True}).json()
        rounds += 1
        if not out["remaining"] or rounds > 20:
            break
    assert rounds == -(-writable // 50) and llm.calls == sum(-(-min(50, writable - 50 * i) // 12) for i in range(rounds))
    view = client.get("/api/v1/content").json()
    assert next(x for x in view["languages"] if x["code"] == "tamil")["missing"] == 0
    assert view["messages"]["core.verified"]["text"]["tamil"] == _Translator.SAMPLE["tamil"]
    assert "tamil" not in view["messages"]["bank.on_off"]["text"]  # code's word lists are left alone
    one = client.patch("/api/v1/content/draft", json={"rev": view["draft"]["rev"], "changes": {
        "messages": {"core.verified": {"tamil": None}}}}).json()
    assert "tamil" not in one["messages"]["core.verified"]["text"]
    refused = client.patch("/api/v1/content/draft", json={"rev": one["draft"]["rev"], "changes": {
        "messages": {"core.verified": {"hi": None}}}})
    assert refused.status_code == 400 and "Callers use Hindi" in refused.json()["detail"]
