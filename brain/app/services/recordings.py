"""Call recordings: captured during the call, encoded once at hang-up, encrypted on disk.

- Live call: stereo, caller on the left (exactly what the agent heard), agent on the right (exactly what was sent,
  including the lowered volume during an interruption check). Keypad codes are never in it: they are typed.
- Voice note: the caller's clip followed by the agent's spoken reply (mono).
- Opus in Ogg (~0.5 MB per minute of call). Encrypted with AES-256-GCM: the key comes from RECORDING_KEY
  (base64, 32 bytes; in production from the bank's key vault). If it is empty, a key file is created in the data
  directory for local development (FILE_STORE=s3 refuses to start without RECORDING_KEY: see container.py).
- Where the encrypted files go: the file store (app/adapters/storage.py). FILE_STORE=local (laptop, Docker with a
  volume): data_dir/recordings. FILE_STORE=s3 (AWS): <S3_PREFIX>recordings/ in the bucket, so they survive restarts
  and deploys, and every copy of the brain can play any call's recording. The database row stores only the file name.
- Retention: RECORDING_RETENTION_DAYS (0 = keep). Expired files are deleted; the row stays with deleted_at, so
  the call record still shows that a recording existed and when it was removed.
"""

import base64
import hashlib
import io
import json
import logging
import os
import uuid
from datetime import datetime

import numpy as np

from app.db import Database
from app.logs import log
from app.ports import FileStore

logger = logging.getLogger(__name__)
MAGIC = b"AUREC1"
MAX_CALL_S = 3600  # stop recording after an hour (memory bound); the call itself goes on


class CallRecorder:
    """Collects both sides of a live call in memory, aligned on the caller's audio clock."""

    def __init__(self, rate: int):
        self.rate, self.started_at = rate, datetime.now().astimezone()
        self._caller, self._agent = bytearray(), bytearray()
        self._limit = MAX_CALL_S * rate * 2

    def caller(self, pcm: bytes) -> None:
        if len(self._caller) < self._limit:
            self._caller += pcm

    def agent(self, frame: bytes) -> None:
        """Called for every frame the agent sends: placed at the current point of the caller's timeline."""
        if len(self._agent) >= self._limit:
            return
        gap = len(self._caller) - len(self._agent)
        if gap > 0:
            self._agent += bytes(gap)
        self._agent += frame

    def stereo(self) -> np.ndarray:
        n = max(len(self._caller), len(self._agent)) // 2
        out = np.zeros((n, 2), dtype=np.int16)
        left = np.frombuffer(bytes(self._caller), dtype=np.int16)
        right = np.frombuffer(bytes(self._agent), dtype=np.int16)
        out[: len(left), 0], out[: len(right), 1] = left, right
        return out

    @property
    def duration_ms(self) -> int:
        return round(max(len(self._caller), len(self._agent)) / 2 / self.rate * 1000)


def encode_ogg(samples: np.ndarray, rate: int) -> bytes:
    """int16 samples, shape (n,) mono or (n, 2) stereo -> Ogg Opus bytes."""
    import av

    stereo = samples.ndim == 2
    layout = "stereo" if stereo else "mono"
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=rate, layout=layout)
        stream.bit_rate = 32000 if stereo else 24000
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples).reshape(1, -1), format="s16", layout=layout)
        frame.sample_rate = rate
        fifo = av.AudioFifo()
        fifo.write(frame)
        for f in fifo.read_many(rate // 50, partial=True):  # 20 ms frames
            for packet in stream.encode(f):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


class RecordingStore:
    def __init__(self, db: Database, settings, files: FileStore):
        self.db, self.s, self.files = db, settings, files
        self._aes = None

    def _cipher(self):
        if self._aes is None:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            key = self.s.recording_key.get_secret_value()
            if key:
                raw = base64.b64decode(key)
            else:  # local development only
                path = self.s.data_dir / "recording.key"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64encode(AESGCM.generate_key(bit_length=256)))
                raw = base64.b64decode(path.read_bytes())
            if len(raw) != 32:
                raise ValueError("RECORDING_KEY must be 32 bytes, base64-encoded")
            self._aes = AESGCM(raw)
        return self._aes

    def save(self, session_id: str, kind: str, samples: np.ndarray, rate: int, started_at: datetime,
             trace_id: str | None = None) -> dict | None:
        """Encode, encrypt and store one recording. Runs in a thread (encoding a 5-minute call takes ~3 s)."""
        if not len(samples):
            return None
        rec_id = str(uuid.uuid4())
        nonce = os.urandom(12)
        blob = MAGIC + nonce + self._cipher().encrypt(nonce, encode_ogg(samples, rate), rec_id.encode())
        name = f"{rec_id}.ogg.enc"
        self.files.put(name, blob)
        row = {"id": rec_id, "session_id": session_id, "trace_id": trace_id, "kind": kind, "started_at": started_at,
               "duration_ms": round(len(samples) / rate * 1000), "format": "audio/ogg", "bytes": len(blob),
               "sha256": hashlib.sha256(blob).hexdigest(), "path": name}
        with self.db.connection() as conn:
            conn.execute("INSERT INTO recordings (id, session_id, trace_id, kind, started_at, duration_ms, format, bytes, "
                         "sha256, path) VALUES (%(id)s, %(session_id)s, %(trace_id)s, %(kind)s, %(started_at)s, "
                         "%(duration_ms)s, %(format)s, %(bytes)s, %(sha256)s, %(path)s)", row)
        log(logger, "recording saved", call=session_id, kind=kind, duration_ms=row["duration_ms"], bytes=len(blob))
        self.purge_expired()
        return row

    def load(self, session_id: str, recording_id: str) -> tuple[bytes, str] | None:
        """Decrypted audio, after checking the file still matches the hash stored when it was saved."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE id = %s AND session_id = %s AND path IS NOT NULL",
                               (recording_id, session_id)).fetchone()
        if not row:
            return None
        with self.files.local_path(row["path"]) as path:
            blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != row["sha256"] or not blob.startswith(MAGIC):
            raise ValueError("recording file does not match its stored hash")
        nonce = blob[len(MAGIC): len(MAGIC) + 12]
        return self._cipher().decrypt(nonce, blob[len(MAGIC) + 12:], str(row["id"]).encode()), row["format"]

    def delete(self, session_id: str, recording_id: str) -> bool:
        """Delete one recording for good: the database row and the encrypted file."""
        with self.db.connection() as conn:
            row = conn.execute("DELETE FROM recordings WHERE id = %s AND session_id = %s RETURNING path",
                               (recording_id, session_id)).fetchone()
        if not row:
            return False
        if row["path"]:
            self.files.delete_prefix(row["path"])
        log(logger, "recording deleted", call=session_id, recording=recording_id)
        return True

    def purge_expired(self) -> int:
        days = self.s.recording_retention_days
        if days <= 0:
            return 0
        with self.db.connection() as conn:
            rows = conn.execute("UPDATE recordings SET path = NULL, deleted_at = now() WHERE path IS NOT NULL AND "
                                "started_at < now() - make_interval(days => %s) RETURNING id, session_id", (days,)).fetchall()
            for r in rows:
                conn.execute("INSERT INTO call_events (session_id, kind, detail) VALUES (%s, 'recording_deleted', %s)",
                             (r["session_id"], json.dumps({"recording": str(r["id"]), "reason": f"retention {days} days"})))
        for r in rows:
            try:
                self.files.delete_prefix(f"{r['id']}.ogg.enc")
            except Exception:  # local disk or S3: keep going, the row already says it was deleted
                logger.exception("could not delete an expired recording")
        if rows:
            log(logger, "recordings deleted by retention policy", count=len(rows), days=days)
        return len(rows)
