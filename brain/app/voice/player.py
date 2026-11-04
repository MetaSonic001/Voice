"""Paced audio output: sends agent audio at real-time speed, only `lead_ms` ahead of playback.

Keeping the client's buffer tiny is what makes interruptions instant: on barge-in we stop sending and tell the
client to drop at most `lead_ms` of already-sent audio. `pause()` / `resume()` hold the audio while we check
whether a sound was a real interruption, so a false alarm resumes exactly where it stopped.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.voice.audio import BYTES_PER_SAMPLE

FRAME_MS = 20


class Player:
    def __init__(self, send: Callable[[bytes], Awaitable[None]], rate: int, lead_ms: int = 200):
        self._send, self.rate, self.lead_s = send, rate, lead_ms / 1000
        self._frame_bytes = rate * FRAME_MS // 1000 * BYTES_PER_SAMPLE
        self._buf = bytearray()
        self._wake = asyncio.Event()
        self._paused = False
        self._play_until = 0.0  # wall time when everything sent so far finishes playing on the client
        self._sent = 0  # bytes sent so far (absolute)
        self._marks: list[tuple[int, Callable[[], None]]] = []  # (absolute byte position, callback when it is sent)
        self._task = asyncio.create_task(self._run(), name="player")

    # ---- state ----
    @property
    def has_pending(self) -> bool:
        return bool(self._buf)

    @property
    def playing(self) -> bool:
        """Audio is queued or still playing on the client."""
        return bool(self._buf) or time.monotonic() < self._play_until

    def remaining_s(self) -> float:
        queued = len(self._buf) / BYTES_PER_SAMPLE / self.rate
        return max(0.0, self._play_until - time.monotonic()) + queued

    # ---- control ----
    def enqueue(self, pcm: bytes, on_start: Callable[[], None] | None = None) -> None:
        """Queue audio; `on_start` fires when the first byte of this audio is actually sent."""
        if not pcm:
            return
        if on_start:
            self._marks.append((self._sent + len(self._buf), on_start))
        self._buf += pcm
        self._wake.set()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._play_until = max(self._play_until, time.monotonic())
            self._wake.set()

    def clear(self) -> None:
        """Drop everything not yet sent (the caller must also tell the client to flush its buffer)."""
        self._buf.clear()
        self._marks.clear()
        self._paused = False
        self._play_until = time.monotonic()

    async def wait_done(self) -> None:
        while self.playing or self._paused:
            await asyncio.sleep(0.02)

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    # ---- loop ----
    async def _run(self) -> None:
        while True:
            if not self._buf or self._paused:
                self._wake.clear()
                await self._wake.wait()
                continue
            now = time.monotonic()
            if self._play_until < now:
                self._play_until = now
            ahead = self._play_until - now
            if ahead > self.lead_s:
                await asyncio.sleep(ahead - self.lead_s)
                continue
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            while self._marks and self._marks[0][0] < self._sent + len(frame):
                self._marks.pop(0)[1]()
            self._sent += len(frame)
            self._play_until += len(frame) / BYTES_PER_SAMPLE / self.rate
            await self._send(frame)
