"""Small talk, fragments and "say that again", decided in code with no LLM call (PHASE-4-PLAN §4.4, §6.2, §4.1 rule 8).

- A fragment ("so, you know,", "hmm", "matlab") is not a turn: the agent says nothing and keeps listening.
- Thanks, goodbye, "hello? I'm waiting", "oh dear" and "ok" each get one short, fitting line.
- A greeting is said once per call (the voice line already played the disclosure and greeting).
- "Can you repeat that" / "phir se bolo" says the last answer again.
"""

import re

_WORD = re.compile(r"[\wऀ-ॿ']+", re.UNICODE)
_FILLERS = {"so", "you", "know", "hmm", "hm", "um", "uh", "umm", "er", "well", "like", "actually", "basically", "matlab", "toh",
            "woh", "wo", "arey", "are", "yaar", "to", "ok", "okay", "haan", "han", "achha", "acha", "मतलब", "तो", "हम्म"}
_REPEAT = re.compile(
    r"^(can you |could you |please )*(repeat|say (that|it) again|come again|pardon)( that| it)?( please)?\W*$|"
    r"phir se bol|dobara bol|ek baar aur bol|दोबारा बोल|फिर से बोल|^what\??$|^sorry\??$", re.I)
_THANKS = re.compile(r"thank|thanks|shukriya|dhanyavad|धन्यवाद|शुक्रिया", re.I)
_BYE = re.compile(r"\b(bye|goodbye|good bye|that'?s all|that is all|nothing else|alvida)\b|बस इतना|अलविदा", re.I)
_WAITING = re.compile(r"(are you there|can you hear|hello\??.*waiting|i'?m waiting|waiting)|सुन रहे|कोई है", re.I)
_SYMPATHY = re.compile(r"\b(oh dear|oh no|alas|uff|oh my)\b|ओह|अरे", re.I)
_HELLO = re.compile(r"^(hi|hello|hey|namaste|namaskar|good (morning|afternoon|evening))\b|नमस्ते", re.I)

REPLIES = {
    "thanks": {"en": "You're welcome!", "hi": "आपका स्वागत है!", "hinglish": "Aapka swagat hai!"},
    "bye": {"en": "Thank you for calling AU Bank. Have a good day!", "hi": "AU बैंक को कॉल करने के लिए धन्यवाद। आपका दिन शुभ हो!",
            "hinglish": "AU Bank ko call karne ke liye thank you. Aapka din accha rahe!"},
    "waiting": {"en": "Yes, I'm here. Go ahead.", "hi": "जी, मैं यहीं हूँ। बताइए।", "hinglish": "Haan, main yahin hoon. Boliye."},
    "ack": {"en": "Is there anything else I can check for you?", "hi": "क्या मैं और कुछ देख सकती हूँ?",
            "hinglish": "Kya main aur kuch check kar sakti hoon?"},
    "hello_again": {"en": "I'm here. How can I help?", "hi": "जी, बताइए, मैं कैसे मदद करूँ?", "hinglish": "Ji, bataiye, main kaise madad karun?"},
}


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def is_fragment(text: str) -> bool:
    """Only filler words: not a request, and not worth a reply."""
    w = words(text)
    return bool(w) and all(x in _FILLERS for x in w) and not _THANKS.search(text)


def is_repeat(text: str) -> bool:
    return bool(_REPEAT.search(text.strip()))


def is_greeting(text: str) -> bool:
    return bool(_HELLO.search(text.strip())) and len(words(text)) <= 4


def is_courtesy(text: str) -> bool:
    """A short thanks or goodbye with nothing else in it ("thanks", "धन्यवाद", "ok bye"): no request to understand."""
    w = words(text)
    return 0 < len(w) <= 5 and "?" not in text and bool(_THANKS.search(text) or _BYE.search(text))


def reply_for(text: str, language: str) -> str:
    """One short line that fits what was said ("" if there is nothing to say)."""
    kind = ("thanks" if _THANKS.search(text) else "bye" if _BYE.search(text) else "waiting" if _WAITING.search(text)
            else "ack" if (_SYMPATHY.search(text) or words(text) in (["ok"], ["okay"], ["achha"], ["acha"], ["theek", "hai"], ["yes"],
                                                                      ["haan"], ["ji"], ["हाँ"], ["जी"], ["हां"])) else "")
    return REPLIES[kind].get(language, REPLIES[kind]["en"]) if kind else ""
