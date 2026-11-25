"""Fixed phrases (greeting + disclosure, re-prompt, code reminder, hold, unclear, goodbye) per language, synthesised once
and cached as PCM. The timed "one moment" filler was removed on 22 Nov 2026: product answers now start with the
understanding call's own opener (services/conversation.opener_for)."""

import asyncio
import hashlib
import logging
from pathlib import Path

from app.ports import TTS
from app.services.content import Table, use
from app.services.numbers import speech_text

logger = logging.getLogger(__name__)

PHRASES = Table("voice")  # brain/content/catalog.json, "voice.*"


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
        self._cache: dict[tuple[str, str], bytes] = {}  # (language, spoken text) -> audio
        self._lock = asyncio.Lock()

    async def get(self, kind: str, language: str, variant: int = 0) -> bytes:
        language = language if language in PHRASES[kind] else "en"
        variant %= len(variants(kind, language))
        spoken = speech_text(phrase_text(kind, language, variant), language)  # with the pronunciation list
        key = (language, spoken)  # by text: a release that rewords a phrase gets new audio, the old stays for other calls
        if key not in self._cache:
            async with self._lock:
                if key not in self._cache:
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

    async def warm(self, catalog=None) -> None:
        """Pre-synthesise everything so the greeting and the other fixed lines play instantly (`catalog`: a release that
        just went live; run this in its own task, so the release is set for this task only)."""
        if catalog is not None:
            use(catalog)
        jobs = [self.get(kind, lang, i) for kind, options in PHRASES.items() for lang in options
                for i in range(len(variants(kind, lang)))]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            logger.warning("phrase warm-up incomplete", extra={"data": {"failed": len(failed), "error": str(failed[0])}})
