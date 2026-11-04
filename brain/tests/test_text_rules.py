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
    ("card 4111 1111 1111 1111", "card [number]"),
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
