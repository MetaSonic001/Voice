"""Fixed phrases (greeting, filler, re-prompt, goodbye) per language, synthesised once and cached as PCM."""

import asyncio
import hashlib
import logging
from pathlib import Path

from app.ports import TTS
from app.services.numbers import speech_text

logger = logging.getLogger(__name__)

PHRASES = {
    "greeting": {
        # The first line is the disclosure every call must start with: an AI assistant, and the call is recorded.
        "hi": "नमस्ते! यह AU स्मॉल फाइनेंस बैंक की वर्चुअल असिस्टेंट है। गुणवत्ता और सुरक्षा के लिए यह कॉल रिकॉर्ड की जा रही है। "
              "आप हिंदी या English में पूछ सकते हैं।",
        "en": "Hello! This is AU Small Finance Bank's virtual assistant. This call is recorded for quality and security. "
              "You can ask me in English or Hindi.",
    },
    # Said only when the answer is slow (settings.filler_after_ms); a small pool, never the same line twice in a row.
    "filler": {"en": ["Let me check that.", "Okay, one second.", "Right, let me look.", "Just a moment."],
               "hi": ["एक सेकंड, मैं देख रही हूँ।", "जी, बस एक पल।", "ठीक है, देखती हूँ।", "जी, मैं चेक करती हूँ।"],
               "hinglish": ["Ek second, main dekh rahi hoon.", "Ji, bas ek pal.", "Theek hai, dekhti hoon.", "Ji, main check karti hoon."]},
    "reprompt": {"en": "Are you still there? You can ask me about our accounts, deposits, cards or loans.",
                 "hi": "क्या आप लाइन पर हैं? आप हमारे खातों, जमा, कार्ड या लोन के बारे में पूछ सकते हैं।",
                 "hinglish": "Kya aap line par hain? Aap accounts, deposits, cards ya loans ke baare mein pooch sakte hain."},
    # Silence while a keypad code is awaited: a reminder about the code, not the general "are you still there?".
    "code_reminder": {"en": "Please type the six-digit code sent to your mobile on the keypad. Take your time.",
                      "hi": "कृपया आपके मोबाइल पर भेजा गया छह अंकों का कोड कीपैड पर टाइप कीजिए। आराम से कीजिए।",
                      "hinglish": "Please aapke mobile par bheja gaya chhe digit ka code keypad par type kijiye. Aaram se kijiye."},
    "goodbye": {"en": "I haven't heard anything, so I'll end the call now. Thank you for calling AU Bank.",
                "hi": "मुझे कुछ सुनाई नहीं दिया, इसलिए मैं कॉल समाप्त कर रही हूँ। AU बैंक को कॉल करने के लिए धन्यवाद।",
                "hinglish": "Mujhe kuch sunai nahi diya, isliye main call end kar rahi hoon. AU Bank ko call karne ke liye dhanyavaad."},
    "hold": {"en": "Thank you for waiting. Someone from our team will be with you shortly.",
             "hi": "इंतज़ार के लिए धन्यवाद। हमारी टीम का कोई सदस्य जल्द ही आपसे बात करेगा।",
             "hinglish": "Wait karne ke liye thank you. Hamari team se koi jaldi hi aapse baat karega."},
    "unclear_twice": {"en": "I'm sorry, I'm still having trouble hearing you. You can say \"agent\" and I'll connect you to a colleague, or try again from a quieter place.",
                      "hi": "माफ़ कीजिए, मुझे अब भी सुनने में दिक्कत हो रही है। आप \"एजेंट\" बोलिए, मैं आपको हमारी टीम से जोड़ दूँगी, या किसी शांत जगह से फिर कोशिश कीजिए।",
                      "hinglish": "Sorry, mujhe ab bhi sunne mein dikkat ho rahi hai. Aap \"agent\" boliye, main aapko team se connect kar dungi, ya kisi shant jagah se phir try kijiye."},
    "unclear": {"en": "Sorry, I didn't catch that. Could you say it again?",
                "hi": "माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप फिर से बोल सकते हैं?",
                "hinglish": "Maaf kijiye, main samajh nahi paayi. Kya aap phir se bol sakte hain?"},
}


def variants(kind: str, language: str) -> list[str]:
    options = PHRASES[kind]
    text = options.get(language) or options["en"]
    return text if isinstance(text, list) else [text]


def phrase_text(kind: str, language: str, variant: int = 0) -> str:
    texts = variants(kind, language)
    return texts[variant % len(texts)]


class PhraseCache:
    def __init__(self, tts: TTS, disk_dir: Path | None = None):
        self.tts, self.disk_dir = tts, disk_dir
        self._cache: dict[tuple[str, str], bytes] = {}
        self._lock = asyncio.Lock()

    async def get(self, kind: str, language: str, variant: int = 0) -> bytes:
        language = language if language in PHRASES[kind] else "en"
        variant %= len(variants(kind, language))
        key = (kind, language, variant)
        if key not in self._cache:
            async with self._lock:
                if key not in self._cache:
                    spoken = speech_text(phrase_text(kind, language, variant), language)  # with the pronunciation list
                    self._cache[key] = await self._synthesise(spoken, language)
        return self._cache[key]

    async def _synthesise(self, spoken: str, language: str) -> bytes:
        """Paid voices (they have a `voice_tag`) keep their phrases on disk, so a restart doesn't buy them again
        (about 1,900 characters, Rs 5-6 with Sarvam). The file name covers voice, settings, language and exact text."""
        tag = getattr(self.tts, "voice_tag", None)
        path = None
        if self.disk_dir and tag:
            name = hashlib.sha256(f"{tag}|{language}|{spoken}".encode()).hexdigest()[:32]
            path = self.disk_dir / f"{name}.pcm"
            if path.is_file():
                return path.read_bytes()
        pcm = b"".join([c async for c in self.tts.stream(spoken, language)])
        if path and pcm and getattr(self.tts, "voice_tag", None) == tag:  # not if a fallback voice made it meanwhile
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pcm)
        return pcm

    def clear(self) -> None:
        """Forget the synthesised phrases (e.g. after the pronunciation choice changed) so they are made again."""
        self._cache.clear()

    async def warm(self) -> None:
        """Pre-synthesise everything so the greeting and fillers play instantly."""
        jobs = [self.get(kind, lang, i) for kind, options in PHRASES.items() for lang in options
                for i in range(len(variants(kind, lang)))]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            logger.warning("phrase warm-up incomplete", extra={"data": {"failed": len(failed), "error": str(failed[0])}})
