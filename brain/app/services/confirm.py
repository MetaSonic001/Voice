"""Reading a caller's answer to a yes/no question ("Shall I block card ending 6712?"), in code, not by the LLM.

Deterministic on purpose: an action or a transfer happens only on a clear yes, the same way every time, in
English, Hindi and Hinglish. A question back ("which card?", "kaunsa?") or a mixed answer is "unclear", so the
agent explains and asks again instead of guessing.
"""

import re

_WORD = re.compile(r"[\wऀ-ॿ']+", re.UNICODE)
_YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "fine", "go", "ahead", "proceed", "please", "right", "correct",
    "confirm", "confirmed", "absolutely", "definitely", "do", "haan", "han", "haa", "ha", "hanji", "haanji", "ji", "jee",
    "theek", "thik", "thick", "kar", "karo", "kardo", "kariye", "kijiye", "chalega", "bilkul", "zaroor", "jarur", "sahi",
    "हाँ", "हां", "हा", "जी", "ठीक", "करो", "कर", "करिए", "कीजिए", "बिल्कुल", "ज़रूर", "जरूर", "सही", "चलेगा",
    "continue", "more", "aage", "age", "agey", "bataiye", "batao", "bolo", "आगे", "बताइए", "बताओ",
}
_NO = {
    "no", "nope", "nah", "don't", "dont", "not", "never", "cancel", "stop", "wait", "hold", "later", "nahi", "nahin",
    "nai", "mat", "ruko", "rukiye", "ruk", "abhi", "baad", "नहीं", "नही", "मत", "रुको", "रुकिए", "बाद",
}
_QUESTION = {
    "which", "what", "why", "how", "when", "where", "who", "kaun", "kaunsa", "konsa", "kaunsi", "kya", "kyun", "kyon",
    "kab", "kaise", "kis", "kitna", "कौन", "कौनसा", "कौन-सा", "क्या", "क्यों", "कब", "कैसे", "किस", "कितना",
}
_NEGATION = {"don't", "dont", "not", "never", "cancel", "nahi", "nahin", "nai", "mat", "नहीं", "नही", "मत"}
_PERMANENT = re.compile(r"permanent|hamesha|poori tarah|stolen|chori|स्थायी|हमेशा", re.I)
_TEMPORARY = re.compile(r"temporar|thodi der|kuch der|abhi ke liye|अस्थायी|कुछ देर", re.I)


def read_reply(text: str) -> str:
    """'yes' | 'no' | 'question' | 'unclear'."""
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "unclear"
    if any(w in _QUESTION for w in words) or text.strip().endswith("?"):
        return "question"
    if any(w in _NEGATION for w in words):  # "mat karo", "don't do it", "abhi nahi": a no, whatever else is said
        return "no"
    yes = sum(w in _YES for w in words)
    no = sum(w in _NO for w in words)
    if no and not yes:
        return "no"
    if yes and not no:
        return "yes" if len(words) <= 8 else "unclear"  # a long answer is probably something else
    if yes and no:  # "haan, but not now", "no no, go ahead"
        return "unclear"
    return "unclear"


def block_preference(text: str) -> str:
    """'permanent' | 'temporary' | '' when the caller states one while confirming a block."""
    if _PERMANENT.search(text):
        return "permanent"
    if _TEMPORARY.search(text):
        return "temporary"
    return ""
