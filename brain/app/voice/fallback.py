"""A paid speech provider with a free backup behind it (SPEECH_FALLBACK, on by default).

Used while testing Sarvam on a small credit balance: if one Sarvam request fails, that request is served by the
backup (Groq Whisper / Edge), so the call carries on. If the failure means "no credits left" or "bad key"
(SpeechError.out_of_credit), every later request goes straight to the backup until the brain restarts. Switching
back for good is still the owner's choice: STT_PROVIDER / TTS_PROVIDER in .env.
"""

import logging
from collections.abc import AsyncIterator

from app.domain import Transcript
from app.ports import STT, TTS

logger = logging.getLogger(__name__)


class _Fallback:
    def __init__(self, primary, backup, kind: str):
        self.primary, self.backup, self.kind = primary, backup, kind
        self.switched = False  # True once the primary is out of credit: the backup serves everything after that

    @property
    def name(self) -> str:
        return self.backup.name if self.switched else self.primary.name

    def _failed(self, error: Exception) -> None:
        if getattr(error, "out_of_credit", False) and not self.switched:
            self.switched = True
            logger.warning(f"{self.kind}: {self.primary.name} is out of credit or the key is wrong; using "
                           f"{self.backup.name} until restart", extra={"data": {"error": str(error)[:300]}})
        else:
            logger.warning(f"{self.kind}: {self.primary.name} failed; this request uses {self.backup.name}",
                           extra={"data": {"error": f"{type(error).__name__}: {str(error)[:300]}"}})

    async def warm(self) -> None:
        active = self.backup if self.switched else self.primary
        if hasattr(active, "warm"):
            await active.warm()

    async def close(self) -> None:
        for p in (self.primary, self.backup):
            if hasattr(p, "close"):
                await p.close()


class FallbackSTT(_Fallback):
    def __init__(self, primary: STT, backup: STT):
        super().__init__(primary, backup, "speech-to-text")

    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript:
        if not self.switched:
            try:
                return await self.primary.transcribe(pcm16, rate)
            except Exception as e:
                self._failed(e)
        return await self.backup.transcribe(pcm16, rate)


class FallbackTTS(_Fallback):
    def __init__(self, primary: TTS, backup: TTS):
        super().__init__(primary, backup, "text-to-speech")

    @property
    def voice_tag(self) -> str | None:
        """The primary's tag for the phrase cache on disk; None once switched, so backup audio is never stored as
        Sarvam audio."""
        return None if self.switched else getattr(self.primary, "voice_tag", None)

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        if not self.switched:
            started = False
            try:
                async for pcm in self.primary.stream(text, language):
                    started = True
                    yield pcm
                return
            except Exception as e:
                if started:  # part of the sentence was already spoken: don't say it again in another voice
                    raise
                self._failed(e)
        async for pcm in self.backup.stream(text, language):
            yield pcm
