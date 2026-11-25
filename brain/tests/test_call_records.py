"""The call record file (services/callrecords.py): written after the call, rebuilt the same after the rows are removed."""

import json
import re
import time

import pytest

from app.adapters.storage import S3FileStore
from app.services import maintenance
from app.services.audit import render_transcript
from app.services.callrecords import CallRecords
from tests.test_adapters import StubS3

KB_Q = "What is the minimum balance requirement for the savings account?"


def _chat(client, caller="9000000101", verify=True):
    """A text chat with a knowledge-base answer and, when `verify`, a keypad verification and a card request."""
    sid = client.post("/api/v1/sessions", json={"caller_number": caller}).json()["id"]
    if verify:
        first = client.post("/api/v1/ask", json={"question": "Please block my credit card ending 6712 temporarily",
                                             "session_id": sid}).json()
        code = re.search(r"\b(\d{6})\b", first["auth"]["simulated_sms"]).group(1)
        client.post("/api/v1/auth/verify", json={"session_id": sid, "code": code})
        client.post("/api/v1/ask", json={"question": "no, don't", "session_id": sid})
    else:
        client.post("/api/v1/ask", json={"question": KB_Q, "session_id": sid})
    return sid


@pytest.fixture
def s3_records(container):
    """CallRecords on a stubbed S3 bucket (FILE_STORE=s3), removing rows right after export (CALL_ROWS_DAYS tiny)."""
    s3 = StubS3()
    settings = container.settings.model_copy(update={"call_rows_days": 1e-6, "call_idle_min": 0})
    records = CallRecords(container.db, container.audit, S3FileStore("bucket", "brain/call_records/", client=s3), settings)
    old = container.audit.records
    container.audit.records = records
    yield records, s3
    container.audit.records = old


def test_record_has_every_turn_with_hashes_and_never_the_code(client, container):
    sid = _chat(client)
    key = container.call_records.export(sid)
    assert key and key.endswith(f"{sid}.json")
    with container.call_records.files.local_path(key) as path:
        record = json.loads(path.read_text(encoding="utf-8"))
    assert record["format"] == 1 and record["id"] == sid
    assert len(record["turns"]) == 3 and all(t["hash"] for t in record["turns"])
    assert [e["event"] for e in record["auth_events"]][:2] == ["otp_sent", "otp_verified"]
    assert "auth" not in record  # the keypad code's hash stays in Postgres only
    rebuilt = container.call_records.load(key)
    assert rebuilt["turns"][0]["created_at"].tzinfo is not None
    with container.db.connection() as conn:
        s = conn.execute("SELECT record_key, exported_at, summary FROM sessions WHERE id = %s", (sid,)).fetchone()
    assert s["record_key"] == key and s["summary"]["turns"] == 3
    assert s["exported_at"] == max(t["created_at"] for t in rebuilt["turns"] + rebuilt["auth_events"] + rebuilt["events"])


def test_after_rows_are_removed_the_call_page_is_the_same(client, container, s3_records):
    records, s3 = s3_records
    sid = _chat(client)
    before = container.audit.call(sid)
    listed_before = next(r for r in container.audit.calls(200) if str(r["id"]) == sid)
    records.export(sid)
    time.sleep(0.2)
    assert records.sweep()["rows_removed"] >= 1
    with container.db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM traces WHERE session_id = %s", (sid,)).fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM auth_events WHERE session_id = %s", (sid,)).fetchone()["n"] == 0
        s = conn.execute("SELECT facts, state, pending, auth, purged_at, chain_len FROM sessions WHERE id = %s",
                         (sid,)).fetchone()
    assert s["purged_at"] and s["facts"] == {} and s["state"] == {} and s["pending"] is None and s["auth"] == {}
    assert s["chain_len"] == 3  # the index row keeps the chain head, so the record can still be checked
    after = container.audit.call(sid)
    assert after["integrity"]["status"] == "intact" and after["integrity"] == before["integrity"]
    assert [t["answer"] for t in after["turns"]] == [t["answer"] for t in before["turns"]]
    assert [t["why"] for t in after["turns"]] == [t["why"] for t in before["turns"]]
    assert after["agenda"] == before["agenda"] and len(after["auth_events"]) == len(before["auth_events"])
    assert render_transcript(after) == render_transcript(before)
    listed = next(r for r in container.audit.calls(200) if str(r["id"]) == sid)
    assert listed["rows_removed"] and listed["turns"] == listed_before["turns"] == 3
    assert listed["answered"] == listed_before["answered"]


def test_a_quiet_chat_is_exported_by_the_sweep_and_new_turns_export_again(client, container, s3_records):
    records, s3 = s3_records
    records.s = records.s.model_copy(update={"call_rows_days": 0})  # keep the rows for this one
    sid = _chat(client, verify=False)
    assert records.sweep()["exported"] >= 1
    key = next(k for k in s3.objects if sid in k)
    client.post("/api/v1/ask", json={"question": KB_Q, "session_id": sid})  # the chat goes on after the export
    assert records.sweep()["exported"] >= 1
    with records.files.local_path(key.removeprefix("brain/call_records/")) as path:
        assert len(json.loads(path.read_text(encoding="utf-8"))["turns"]) == 2


def test_rows_newer_than_the_record_are_never_removed(client, container, s3_records):
    records, _ = s3_records
    sid = _chat(client, verify=False)
    records.export(sid)
    client.post("/api/v1/ask", json={"question": KB_Q, "session_id": sid})  # after the export, before the sweep
    records.s = records.s.model_copy(update={"call_idle_min": 60})  # still active: not exported again yet
    time.sleep(0.2)
    records.sweep()
    with container.db.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM traces WHERE session_id = %s", (sid,)).fetchone()["n"]
    assert n == 2


def test_rows_are_kept_when_call_rows_days_is_0(client, container):
    sid = _chat(client, verify=False)
    container.call_records.export(sid)
    container.call_records.sweep()
    with container.db.connection() as conn:
        assert conn.execute("SELECT purged_at FROM sessions WHERE id = %s", (sid,)).fetchone()["purged_at"] is None


def test_deleting_a_call_deletes_its_record_file(client, container):
    sid = _chat(client, verify=False)
    key = container.call_records.export(sid)
    path = container.call_records.files.root / key
    assert path.exists()
    removed = maintenance.delete_call(container.db, container.recordings.files, sid, container.call_records.files)
    assert removed["call_record_files"] == 1 and not path.exists()
