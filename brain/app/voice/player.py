"""Paced audio output: sends agent audio at real-time speed, only `lead_ms` ahead of playback.

Keeping the client's buffer tiny is what makes interruptions instant: on barge-in we stop sending and tell the
client to drop at most `lead_ms` of already-sent audio. While we check whether a sound was a real interruption,
`duck()` lowers the volume instead of stopping, so a false alarm never leaves a gap in the middle of a sentence.
`on_frame` sees every frame exactly as sent (used for the call recording).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import numpy as np

from app.voice.audio import BYTES_PER_SAMPLE

FRAME_MS = 20


class Player:
    def __init__(self, send: Callable[[bytes], Awaitable[None]], rate: int, lead_ms: int = 200,
                 on_frame: Callable[[bytes], None] | None = None):
        self._send, self.rate, self.lead_s, self._on_frame = send, rate, lead_ms / 1000, on_frame
        self._frame_bytes = rate * FRAME_MS // 1000 * BYTES_PER_SAMPLE
        self._buf = bytearray()
        self._wake = asyncio.Event()
        self._gain = 1.0
        self.speaking_since = 0.0  # wall time when the current stretch of agent audio started playing
        self._play_until = 0.0  # wall time when everything sent so far finishes playing on the client
        self._sent = 0  # bytes sent so far (absolute)
        self._marks: list[tuple[int, Callable[[], None]]] = []  # (absolute byte position, callback when it is sent)
        self._task = asyncio.create_task(self._run(), name="player")

    # ---- state ----
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

    def duck(self, gain: float = 0.3) -> None:
        """Play the following audio at `gain` (1.0 = normal volume again)."""
        self._gain = gain

    def clear(self) -> None:
        """Drop everything not yet sent (the caller must also tell the client to flush its buffer)."""
        self._buf.clear()
        self._marks.clear()
        self._gain = 1.0
        self._play_until = time.monotonic()

    async def wait_done(self) -> None:
        while self.playing:
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
            if not self._buf:
                self._wake.clear()
                await self._wake.wait()
                continue
            now = time.monotonic()
            if self._play_until < now:  # nothing was playing: a new stretch of agent speech starts now
                self._play_until = self.speaking_since = now
            ahead = self._play_until - now
            if ahead > self.lead_s:
                await asyncio.sleep(ahead - self.lead_s)
                continue
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            while self._marks and self._marks[0][0] < self._sent + len(frame):
                self._marks.pop(0)[1]()
            if self._gain != 1.0:
                frame = (np.frombuffer(frame, dtype=np.int16) * self._gain).astype(np.int16).tobytes()
            self._sent += len(frame)
            self._play_until += len(frame) / BYTES_PER_SAMPLE / self.rate
            if self._on_frame:
                self._on_frame(frame)
            await self._send(frame)
