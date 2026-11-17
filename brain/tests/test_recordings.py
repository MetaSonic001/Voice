"""Call recordings on the file store (B2.2): S3 in production, local disk otherwise. Plus the startup lock."""

import base64
import os
import threading
import time
from datetime import datetime

import numpy as np
import pytest
from pydantic import SecretStr

from app.adapters.storage import S3FileStore
from app.config import Settings
from app.container import build_recording_files
from app.services import maintenance
from app.services.recordings import MAGIC, RecordingStore
from tests.test_adapters import StubS3

RATE = 16000


def _tone(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)


@pytest.fixture
def s3_recordings(container):
    """A RecordingStore writing to a stubbed S3 bucket, like FILE_STORE=s3 on AWS."""
    s3 = StubS3()
    settings = container.settings.model_copy(update={"recording_key": SecretStr(base64.b64encode(os.urandom(32)).decode())})
    store = RecordingStore(container.db, settings, S3FileStore("bucket", "brain/recordings/", client=s3))
    return store, s3


def test_recording_saved_to_s3_encrypted_and_played_back(container, s3_recordings):
    store, s3 = s3_recordings
    sid = str(container.traces.create_session("en", None)["id"])
    row = store.save(sid, "call", _tone(), RATE, datetime.now().astimezone())
    key = f"brain/recordings/{row['path']}"
    body, extra = s3.objects[key]
    assert row["path"] == f"{row['id']}.ogg.enc" and body.startswith(MAGIC)
    assert extra == {"ServerSideEncryption": "AES256"}  # bucket-side encryption on top of our own AES-GCM
    assert b"OggS" not in body  # the audio itself is never stored readable
    audio, media = store.load(sid, row["id"])
    assert media == "audio/ogg" and audio.startswith(b"OggS")

    s3.objects[key] = (body[:-1] + bytes([body[-1] ^ 1]), extra)  # tampered file: refused, not played
    with pytest.raises(ValueError):
        store.load(sid, row["id"])

    assert store.delete(sid, row["id"]) and key not in s3.objects


def test_delete_call_and_clean_slate_remove_s3_recordings(container, s3_recordings):
    store, s3 = s3_recordings
    sid = str(container.traces.create_session("en", None)["id"])
    store.save(sid, "call", _tone(0.5), RATE, datetime.now().astimezone())
    other = str(container.traces.create_session("en", None)["id"])
    store.save(other, "voice_note", _tone(0.5), RATE, datetime.now().astimezone())
    assert len(s3.objects) == 2
    removed = maintenance.delete_call(container.db, store.files, sid)
    assert removed["recording_files"] == 1 and len(s3.objects) == 1
    s3.objects["brain/other-folder/keep.bin"] = (b"x", {})  # outside the recordings prefix: never touched
    store.files.delete_prefix("")  # what clean_slate does to the recording files
    assert list(s3.objects) == ["brain/other-folder/keep.bin"]


def test_s3_recordings_need_a_real_key(tmp_path):
    base = dict(data_dir=tmp_path, file_store="s3", s3_bucket="b")
    with pytest.raises(ValueError, match="RECORDING_KEY"):  # a key file on the container's disk dies with it
        build_recording_files(Settings(**base))
    build_recording_files(Settings(**base, record_calls=False))  # nothing recorded, nothing to lock away
    local = build_recording_files(Settings(data_dir=tmp_path))
    assert local.root == tmp_path / "recordings"  # same folder as before: existing local recordings still play


def test_startup_work_runs_one_copy_at_a_time(container):
    """Two copies starting together: the second waits for the first (migrations, vector index, seeding)."""
    done = threading.Event()
    with container.db.startup_lock():
        t = threading.Thread(target=lambda: (container.db.ensure_vector_index(container.embedder.name, container.embedder.dim), done.set()))
        t.start()
        time.sleep(0.4)
        assert not done.is_set()  # blocked behind the lock
    t.join(10)
    assert done.is_set()
