"""Pure rules for natural turn-taking (no I/O, fully unit-tested).

- incomplete():   does this transcript sound unfinished? ("...and", "aur", "matlab", trailing comma) -> wait longer
- classify_barge(): what the caller said while the agent was talking: noise / echo / backchannel / interrupt
- is_noise():     empty text, very likely no speech, or a typical Whisper hallucination on noise
"""

import re

_WORDS = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)

# Utterance endings that mean "I haven't finished" (English, Hinglish, Hindi).
_TRAILING = {
    "and", "or", "but", "so", "because", "the", "a", "an", "to", "for", "of", "with", "in", "on", "is", "my", "your",
    "if", "then", "like", "um", "uh", "umm", "hmm", "er", "also", "which", "what", "about",
    "aur", "ya", "lekin", "par", "ki", "ke", "ka", "mein", "me", "se", "ko", "toh", "matlab", "jaise", "kya",
    "और", "या", "लेकिन", "पर", "कि", "के", "का", "की", "में", "से", "को", "तो", "मतलब", "जैसे", "क्या",
}
# Short acknowledgements that are not meant to interrupt.
_BACKCHANNEL = {
    "haan", "han", "ha", "haa", "hmm", "hm", "mm", "mhm", "ok", "okay", "achha", "acha", "accha", "ji", "jee", "right",
    "yes", "yeah", "yep", "sure", "theek", "thik", "hai", "uh", "huh", "oh", "nice", "great", "fine", "got", "it",
    "हाँ", "हां", "हम्म", "अच्छा", "ठीक", "है", "जी", "ओके",
}
# Words that always mean "stop talking", even alone.
_STOP = {
    "stop", "wait", "hold", "pause", "no", "nahi", "nahin", "ruko", "rukiye", "ruk", "bas", "sorry", "excuse", "sunno",
    "suniye", "listen", "रुको", "रुकिए", "बस", "नहीं", "सुनो", "सुनिए",
}
# Text Whisper tends to invent from silence, breath or background noise.
_HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye", "subtitles by", "please subscribe",
    "धन्यवाद", "शुक्रिया", "music", "applause", ".", "...", "हम्म",
}


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def incomplete(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith((",", "-", "...", "…")):
        return True
    w = words(stripped)
    return bool(w) and w[-1] in _TRAILING and not stripped.endswith(("?", "!", ".", "।"))


def is_noise(text: str, no_speech: float, threshold: float = 0.6) -> bool:
    t = text.strip().lower().strip(".!?। ")
    return not t or no_speech >= threshold or t in _HALLUCINATIONS or not words(t)


def is_echo(text: str, agent_text: str) -> bool:
    """True if what we heard is mostly the agent's own words (speaker leaking into the microphone)."""
    heard, said = words(text), set(words(agent_text))
    if not heard or not said:
        return False
    return sum(w in said for w in heard) / len(heard) >= 0.7


def classify_barge(text: str, no_speech: float, agent_text: str) -> str:
    """'noise' | 'echo' | 'backchannel' | 'interrupt' for speech heard while the agent was talking."""
    if is_noise(text, no_speech):
        return "noise"
    w = words(text)
    if any(x in _STOP for x in w):
        return "interrupt"
    if is_echo(text, agent_text):
        return "echo"
    if len(w) <= 3 and all(x in _BACKCHANNEL for x in w):
        return "backchannel"
    return "interrupt"
