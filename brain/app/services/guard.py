"""Checks that run in code on the caller's own words, before and after the LLM.

1. `injection(text)`: an attempt to change the assistant's rules or read its instructions. Caught here with no LLM call
   (cheaper, and it cannot be talked out of it); the LLM's own `manipulation` label is no longer needed.
2. `risk_flags(text)`: safety triggers (unrecognised debit, lost card, shared OTP). Either this or the LLM's own risk
   flags is enough: a credible risk is never answered as a plain enquiry.
3. `instruction_like(text)`: text in an uploaded document that tries to instruct the assistant (flagged at ingest).
"""

import re

_INJECTION = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier)? ?(instructions|rules|prompt)|"
    r"(reveal|show|print|tell me|repeat|share) (me )?(your|the) (system |hidden |secret )?(prompt|instructions|rules)|"
    r"system prompt|developer mode|jailbreak|\bDAN\b|"
    r"you are (now|no longer) (a|an|the)|act as (a|an|the) (different|new|unrestricted|admin|bank|hacker|developer)|pretend (to be|you are)|"
    r"disregard (your|the|all) (rules|instructions|guidelines)|forget (your|all|the) (rules|instructions)|"
    r"i am (a |an |the )?(bank )?(staff|employee|manager|admin|developer)|\bsudo\b|override (the )?(security|rules)",
    re.I)

# Safety triggers on the caller's own words. Each maps to a risk flag.
_RISK = {
    "unrecognised_debit": re.compile(
        r"(don'?t|do not|didn'?t|did not|never) (recogni[sz]e|make|made|do|authori[sz]e)|not (done|made) by me|"
        r"unauthori[sz]ed|someone (took|withdrew|used)|money (was )?(taken|withdrawn) without|"
        r"pehchaan nahi|pehchan nahi|maine nahi kiya|पहचान नहीं|मैंने नहीं किया", re.I),
    "lost_card": re.compile(
        r"card (is |was |has been )?(lost|missing|stolen|chori)|(lost|stolen|misplaced) (my )?(card|wallet|phone)|"
        r"\bstolen\b|chori ho|चोरी|कार्ड (खो|गुम)", re.I),
    "otp_shared": re.compile(
        r"shared (my |the )?(otp|pin|cvv)|\b(otp|pin|cvv)\b.{0,25}\b(share|bata|de|bol)\w* ?(kar )?(diya|di|diye|dia)\b|"
        r"(gave|told|sent) (\w+ ){0,3}(my |the )?(otp|pin|cvv)|(ओटीपी|पिन|otp).{0,25}(बता|शेयर|दे) ?(दिया|दी|दिए)", re.I),
    "fraud_other": re.compile(r"\bfraud|\bscam|\bhacked|धोखाधड़ी|फ्रॉड|phishing|suspicious (call|link|message)", re.I),
}
_DOC_INSTRUCTION = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above)|you must (now )?(say|answer|reply|tell)|"
    r"as an ai|system prompt|do not (tell|reveal)|new instructions", re.I)


_ASKING = re.compile(
    r"\b(how (do|can|should|to)|what (is|are|should)|what happens|process|procedure|steps?|if i (lose|lost)|kaise|kya karna|"
    r"kya hota|kaise karte)\b|कैसे|क्या करना", re.I)
_REPORTING = re.compile(
    r"\bi (have |had )?(lost|been)|i'?ve (lost|been)|my .{0,25}(is|was|has been|got) (lost|stolen|missing)|"
    r"(was|got|been) stolen|someone (took|stole|withdrew|used)|\bi (don'?t|did ?n'?t|never)\b|maine|mere .{0,25}(chori|kho|gum)|"
    r"मैंने|मेरा .{0,25}(खो|चोरी|गुम)", re.I)
_PERSON = re.compile(r"\b(connect|transfer|talk to|speak to|put me through)\b|बात करा|जोड़", re.I)


def doubt_llm_risk(text: str, kinds: list[str]) -> bool:
    """True when a risk flag that only the LLM raised should not be believed: the caller is asking how something works
    ("how do I block a card if I lose it?") or asking for a person ("connect me to your fraud team") without reporting
    anything happening to them. The code screens (risk_flags) are never doubted."""
    if "human" in kinds and not _REPORTING.search(text):
        return True
    return bool(_ASKING.search(text)) and not _REPORTING.search(text)


def asks_for_team(text: str, kinds: list[str]) -> bool:
    """True when the caller only asks to be put through to a person or team ("please connect me to your fraud team")
    and reports nothing happening to them. Such a turn is a transfer: no fraud workflow, no verification first."""
    return "human" in kinds and bool(_PERSON.search(text)) and not _REPORTING.search(text) and not _RISK["otp_shared"].search(text)


# ───────── what the agent itself may never say (the last check before speech) ─────────
# Even a fully jailbroken answer model must not ask the caller for a secret or send them to a link it made up: both are
# exactly what fraudsters do. Warnings ("never share your OTP") are allowed; requests to tell, share or read one out are not.
_SECRET = (r"(?<![a-z])(?:otp|one[- ]time (?:password|passcode|code)|pin|cvv|cvc|password|passcode|card number|"
           r"ओटीपी|पिन|पासवर्ड)(?![a-z])")
# English request: an imperative verb, then the secret, addressed to us ("tell me your OTP", "read out your card number").
# Statements like "we'll send the OTP to your mobile" or "if you shared your PIN" are not requests.
_EN_ASK = re.compile(rf"\b(?:tell|share|give|say|read(?: out)?|provide|repeat|spell)\b(?:\W+\w+){{0,4}}?\W+{_SECRET}", re.I)
_TO_US = re.compile(r"\b(?:me|us|your)\b", re.I)
# Hindi / Hinglish request: the secret, then an imperative ending ("apna OTP bataiye", "ओटीपी बताइए"), not a past form.
_HI_ASK = re.compile(
    rf"{_SECRET}(?:\W+\w+){{0,3}}?\W+(?:bata(?:iye|iyega|yein|yen|en|o| do| dijiye)|bol(?:iye|o| dijiye)|"
    r"share (?:kijiye|karein|karen|karo|kar dijiye)|de dijiye|dijiye|बताइए|बताइये|बताएं|बताएँ|बताओ|बता दीजिए|बोलिए|बोलो|"
    r"दे दीजिए|दीजिए|शेयर (?:कीजिए|करें|करो))", re.I)
# A warning, not a request: "never share your OTP", "kisi ko mat bataiye", "if you share your PIN...".
_WARNING = re.compile(r"\b(?:never|not|don'?t|do not|no one|nobody|anyone|if|kabhi|mat|na|nahi|nahin|agar|kisi)\b|"
                      r"कभी|मत|नहीं|किसी|अगर|(?<!\S)न(?!\S)", re.I)
_LINK = re.compile(r"\b(?:https?://\S+|www\.\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|net|org|co|io|app|link|ly)\b\S*)", re.I)


def unsafe_output(sentence: str, sources: str = "") -> str | None:
    """Why the agent may not say this sentence ("asks_for_secret" / "unknown_link"), or None. Pure regex, microseconds.
    A link or web address is allowed only when it appears in the cited sources (the bank's own documents)."""
    for pattern, needs_us in ((_EN_ASK, True), (_HI_ASK, False)):
        for m in pattern.finditer(sentence):
            if needs_us and not _TO_US.search(m.group(0)):
                continue
            if not _WARNING.search(sentence[max(0, m.start() - 12):m.end()]):
                return "asks_for_secret"
    known = sources.lower()
    for m in _LINK.finditer(sentence):
        link = m.group(0).rstrip(".,;:!?)").lower()
        bare = re.sub(r"^(https?://)?(www\.)?", "", link).split("/")[0]
        if bare not in known:
            return "unknown_link"
    return None


def injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def risk_flags(text: str) -> list[str]:
    return [name for name, pattern in _RISK.items() if pattern.search(text)]


def instruction_like(text: str) -> bool:
    return bool(_DOC_INSTRUCTION.search(text))
