"""Call audit: the turn chain detects edits and deletions; the call record and transcript are readable."""

import uuid

from app.services.audit import render_transcript


def _turn(session_id: str, question: str) -> dict:
    return {"id": str(uuid.uuid4()), "session_id": session_id, "question": question, "language": "en", "intent": "general",
            "outcome": "answered", "answer": f"Answer to {question}", "total_ms": 1200,
            "data": {"timings": {"total_ms": 1200}, "checks": [], "citations": [], "score": 0.97,
                     "grounding": {"level": "high", "basis": ["source match 0.97"]}}}


def test_chain_detects_edits_and_deleted_turns(container):
    traces, audit, db = container.traces, container.audit, container.db
    sid = str(traces.create_session("en", "9876543210")["id"])
    turns = [_turn(sid, q) for q in ("first", "second", "third")]
    for t in turns:
        traces.save(t)
    traces.add_voice_timings(turns[0]["id"], {"voice_to_voice_ms": 2000})  # added later: not part of the hash
    assert audit.call(sid)["integrity"]["status"] == "intact"

    with db.connection() as conn:  # someone edits an answer in the database
        conn.execute("UPDATE traces SET answer = 'changed' WHERE id = %s", (turns[1]["id"],))
    broken = audit.call(sid)["integrity"]
    assert broken["status"] == "broken" and broken["turn"] == 2

    with db.connection() as conn:  # put it back, then delete the last turn
        conn.execute("UPDATE traces SET answer = %s WHERE id = %s", (turns[1]["answer"], turns[1]["id"]))
        conn.execute("DELETE FROM traces WHERE id = %s", (turns[2]["id"],))
    assert audit.call(sid)["integrity"]["status"] == "broken"


def test_transcript_reads_like_a_call_record(container):
    traces, audit = container.traces, container.audit
    sid = str(traces.create_session("en", "9876543210")["id"])
    traces.save(_turn(sid, "What is the FD rate?"))
    audit.event(sid, "disclosure", text="This call is recorded for quality and security.")
    text = render_transcript(audit.call(sid))
    assert "CALLER (typed): What is the FD rate?" in text and "AGENT: Answer to What is the FD rate?" in text
    assert "This call is recorded" in text and "Integrity  intact" in text and "98•••••210" in text
