"""Sentence splitting, redaction and turn-taking rules (pure functions)."""

import pytest

from app.services.privacy import redact
from app.services.sentences import SentenceSplitter
from app.voice.turntaking import classify_barge, incomplete, is_noise


def split_stream(chunks: list[str]) -> list[str]:
    s = SentenceSplitter()
    out = [x for c in chunks for x in s.feed(c)]
    return out + s.flush()


def test_citation_marker_stays_with_its_sentence_across_chunks():
    assert split_stream(["The fee is Rs 4,999.", " ", "[1]", " Waived on 8 lakh spend [2]."]) == [
        "The fee is Rs 4,999. [1]", "Waived on 8 lakh spend [2]."]


def test_decimals_and_abbreviations_do_not_split():
    assert split_stream(["Rate is 7.10% p.a. for 2 years [1]. Seniors get 7.60% [1]."]) == [
        "Rate is 7.10% p.a. for 2 years [1].", "Seniors get 7.60% [1]."]


def test_hindi_danda_splits():
    assert split_stream(["दर 7.10% है [1]। वरिष्ठ नागरिकों को 7.60% [1]।"]) == ["दर 7.10% है [1]।", "वरिष्ठ नागरिकों को 7.60% [1]।"]


@pytest.mark.parametrize("text,expected", [
    ("my account 10020030001 balance", "my account [number] balance"),
    ("card 4111 1111 1111 1111", "card [card]"),  # Luhn-valid
    ("card 4111 1111 1111 1112", "card [number]"),  # long digits that fail Luhn are still masked
    ("my aadhaar 2345 6789 0124", "my aadhaar [aadhaar]"),  # Verhoeff-valid
    ("call me on 9876543210", "call me on [phone]"),
    ("call +91 98765 43210", "call [phone]"),
    ("account number 4521 8890 12 please", "account number [number] please"),
    ("I paid ₹1,50,000 on 12 Nov, ending 4521", "I paid ₹1,50,000 on 12 Nov, ending 4521"),  # financial facts stay
    ("OTP is 482913", "OTP is [secret]"),
    ("PAN ABCDE1234F", "PAN [pan]"),
    ("mail me at a.b@example.com", "mail me at [email]"),
    ("FD of 500000 for 2 years", "FD of 500000 for 2 years"),  # amounts stay
    ("number saat ek shunya saat chaar do shunya teen chhah nau", "number [number]"),  # spoken digits
    ("नंबर सात एक शून्य सात चार दो", "नंबर [number]"),
    ("pay anjali@okaxis", "pay [upi]"),
    ("ek do teen baar", "ek do teen baar"),  # ordinary speech stays
])
def test_redaction(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("I want to know the FD rate and", True), ("mujhe FD ke baare mein, matlab", True), ("FD rate kya hai aur", True),
    ("What is the FD rate?", False), ("FD rate batao", False), ("", False),
    ("so, you know", True), ("hmm", True), ("matlab", True), ("balance batao", False),
])
def test_incomplete(text, expected):
    assert incomplete(text) is expected


@pytest.mark.parametrize("text,no_speech,verdict", [
    ("haan", 0.0, "backchannel"), ("hmm ok", 0.0, "backchannel"), ("ruko", 0.0, "interrupt"),
    ("wait, what about seniors?", 0.0, "interrupt"), ("", 0.0, "noise"), ("Thank you.", 0.0, "noise"),
    ("anything", 0.9, "noise"), ("the minimum balance is ten thousand", 0.0, "echo"),
    ("haan but what about the senior citizen rate", 0.0, "interrupt"),
])
def test_barge_classification(text, no_speech, verdict):
    agent = "The minimum balance is ten thousand rupees for urban branches."
    assert classify_barge(text, no_speech, agent) == verdict


def test_noise():
    assert is_noise("  ", 0.0) and is_noise("धन्यवाद", 0.0) and not is_noise("FD rate", 0.1)


def test_au_is_said_the_chosen_way_only_in_speech():
    from app.services import pronunciation as p

    # Fixed by the owner on 6 Nov 2026: English / Hinglish "A.U.", Hindi "एयू". No listening page any more.
    assert p.say("AU 0101 app", "hi") == "एयू शून्य एक शून्य एक app"
    assert p.say("This is AU Bank", "en") == "This is A.U. Bank" and p.say("This is AU Bank", "hinglish") == "This is A.U. Bank"
    assert p.say("AUDIT", "en") == "AUDIT"  # only the word AU


def test_reply_language_follows_the_callers_words_not_one_misheard_filler():
    from app.services.conversation import reply_language
    # live call 7fa9b902: an English "hey" heard as "हो" turned the whole English sentence into a Hindi reply
    assert reply_language("हो. Okay, so I want to know when was my last home loan EMI?", "hi", "en") == "en"
    assert reply_language("हो.", None, "en") == "en"  # only a filler: the call's language
    assert reply_language("Yes.", "en", "hi") == "hi"
    assert reply_language("मुझे मेरा account balance बताइए।", "hi", "en") == "hi"  # mostly Devanagari
    assert reply_language("मेरा balance", "hi", "en") == "hi"  # half and half: the LLM's label
    assert reply_language("Mera balance kya hai?", "hi", "en") == "hinglish"  # Latin letters are never Hindi
    assert reply_language("Pichhli EMI kab kati thi?", "hinglish", "en") == "hinglish"
    assert reply_language("Okay, and how much is available?", "hi", "hi") == "en"
    assert reply_language("नमस्ते", None, "en") == "en"  # a greeting alone sets nothing
    assert reply_language("Can you speak in Hindi please?", "en", "en") == "hi"  # an explicit choice, in Latin letters
    assert reply_language("English mein bolo", "hinglish", "hi") == "en"
    assert reply_language("हिंदी में बात कीजिए", "hi", "en") == "hi"


# ───────── the opener (22 Nov 2026: replaces the timed filler) ─────────

def _u(opener, kind="general", lang="en", **kw):
    from app.domain import Request, Understanding

    req = Request(kind, "What is the FD rate?", scope=kw.pop("scope", ""), goal=kw.pop("goal", "info"))
    return Understanding(lang, kind, "What is the FD rate?", "", requests=[req], opener=opener, **kw)


def test_opener_is_said_only_for_a_checked_kb_question():
    from app.services.conversation import opener_for

    assert opener_for(_u("Sure, let me check the FD rates for you"), "en", guided=False) == \
        "Sure, let me check the FD rates for you."
    assert opener_for(_u("जी, मैं FD की जानकारी देखती हूँ।", lang="hi"), "hi", guided=False).endswith("।")
    for u, why in [(_u("Let me check that.", kind="personal"), "bank data is a template, ready at once"),
                   (_u("Let me check that.", risk=["lost_card"]), "fraud has its own approved opening"),
                   (_u("Let me check that.", scope="choose"), "a guided flow asks its own question"),
                   (_u("The rate is 7.75%, let me check."), "never a figure"),
                   (_u("Which tenure do you want?"), "never a question"),
                   (_u("I've blocked it for you."), "never an outcome"),
                   (_u("Okay"), "too short"),
                   (_u(""), "nothing written")]:
        assert opener_for(u, "en", guided=False) == "", why
    assert opener_for(_u("Let me check that."), "en", guided=True) == ""  # a guided flow is waiting
    assert opener_for(_u("Let me check that."), "hi", guided=False) == ""  # reply language changed by code
