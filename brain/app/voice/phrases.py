"""Fixed phrases (greeting, filler, re-prompt, goodbye) per language, synthesised once and cached as PCM."""

import asyncio
import logging

from app.ports import TTS

logger = logging.getLogger(__name__)

PHRASES = {
    "greeting": {
        "hi": "नमस्ते! AU स्मॉल फाइनेंस बैंक में आपका स्वागत है। मैं AU बैंक की AI सहायक हूँ और यह कॉल रिकॉर्ड की जा सकती है। "
              "आप हिंदी या English में पूछ सकते हैं।",
        "en": "Hello! Welcome to AU Small Finance Bank. I'm the bank's AI assistant, and this call may be recorded. "
              "You can ask me in English or Hindi.",
    },
    "filler": {"en": "One moment, please.", "hi": "एक सेकंड, मैं देख रही हूँ।", "hinglish": "Ek second, main dekh rahi hoon."},
    "reprompt": {"en": "Are you still there? You can ask me about our accounts, deposits, cards or loans.",
                 "hi": "क्या आप लाइन पर हैं? आप हमारे खातों, जमा, कार्ड या लोन के बारे में पूछ सकते हैं।",
                 "hinglish": "Kya aap line par hain? Aap accounts, deposits, cards ya loans ke baare mein pooch sakte hain."},
    "goodbye": {"en": "I haven't heard anything, so I'll end the call now. Thank you for calling AU Bank.",
                "hi": "मुझे कुछ सुनाई नहीं दिया, इसलिए मैं कॉल समाप्त कर रही हूँ। AU बैंक को कॉल करने के लिए धन्यवाद।",
                "hinglish": "Mujhe kuch sunai nahi diya, isliye main call end kar rahi hoon. AU Bank ko call karne ke liye dhanyavaad."},
    "unclear": {"en": "Sorry, I didn't catch that. Could you say it again?",
                "hi": "माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप फिर से बोल सकते हैं?",
                "hinglish": "Maaf kijiye, main samajh nahi paayi. Kya aap phir se bol sakte hain?"},
}


def phrase_text(kind: str, language: str) -> str:
    options = PHRASES[kind]
    return options.get(language) or options["en"]


class PhraseCache:
    def __init__(self, tts: TTS):
        self.tts = tts
        self._cache: dict[tuple[str, str], bytes] = {}
        self._lock = asyncio.Lock()

    async def get(self, kind: str, language: str) -> bytes:
        language = language if language in PHRASES[kind] else "en"
        key = (kind, language)
        if key not in self._cache:
            async with self._lock:
                if key not in self._cache:
                    self._cache[key] = b"".join([c async for c in self.tts.stream(phrase_text(kind, language), language)])
        return self._cache[key]

    async def warm(self) -> None:
        """Pre-synthesise everything so the greeting and fillers play instantly."""
        jobs = [self.get(kind, lang) for kind, options in PHRASES.items() for lang in options]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            logger.warning("phrase warm-up incomplete", extra={"data": {"failed": len(failed), "error": str(failed[0])}})
