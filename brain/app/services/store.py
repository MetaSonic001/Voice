"""Sessions and traces (one trace per question)."""

import json
import uuid

from app.db import Database


class TraceStore:
    def __init__(self, db: Database):
        self.db = db

    def create_session(self, language: str | None = None, caller_number: str | None = None) -> dict:
        """`caller_number`: the number the call came from (telephony caller ID; picked in the test UI)."""
        session_id = str(uuid.uuid4())
        with self.db.connection() as conn:
            row = conn.execute("INSERT INTO sessions (id, language, caller_number) VALUES (%s, %s, %s) "
                               "RETURNING id, language, created_at", (session_id, language, caller_number)).fetchone()
        return row

    def session_exists(self, session_id: str) -> bool:
        if not _is_uuid(session_id):
            return False
        with self.db.connection() as conn:
            return conn.execute("SELECT 1 FROM sessions WHERE id = %s", (session_id,)).fetchone() is not None

    def history(self, session_id: str, turns: int) -> list[dict]:
        """The last turns, for resolving follow-ups. Answers that disclosed account data are replaced by a
        placeholder: balances and transactions never go back to the LLM."""
        with self.db.connection() as conn:
            rows = conn.execute("SELECT question, answer, language, intent, outcome FROM traces WHERE session_id = %s "
                                "AND outcome NOT IN ('error', 'interrupted') ORDER BY created_at DESC LIMIT %s",
                                (session_id, turns)).fetchall()
        for r in rows:
            if r["intent"] == "personal" and r["outcome"] == "answered":
                r["answer"] = "(account details were given)"
        return list(reversed(rows))

    def facts(self, session_id: str) -> dict:
        with self.db.connection() as conn:
            row = conn.execute("SELECT facts FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return dict(row["facts"] or {}) if row else {}

    def save_facts(self, session_id: str, facts: dict) -> None:
        with self.db.connection() as conn:
            conn.execute("UPDATE sessions SET facts = %s WHERE id = %s", (json.dumps(facts), session_id))

    def last_language(self, session_id: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT language FROM traces WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                               (session_id,)).fetchone()
        return row["language"] if row else None

    def session(self, session_id: str) -> dict | None:
        if not _is_uuid(session_id):
            return None
        with self.db.connection() as conn:
            s = conn.execute("SELECT id, language, created_at, customer_id, verified_at, facts FROM sessions WHERE id = %s",
                             (session_id,)).fetchone()
            if not s:
                return None
            s["turns"] = conn.execute(
                "SELECT id AS trace_id, created_at, question, language, intent, outcome, answer, total_ms "
                "FROM traces WHERE session_id = %s ORDER BY created_at", (session_id,)).fetchall()
        return s

    def save(self, trace: dict) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO traces (id, session_id, question, language, intent, outcome, answer, total_ms, data) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (trace["id"], trace.get("session_id"), trace["question"], trace.get("language"), trace.get("intent"),
                 trace["outcome"], trace["answer"], trace["total_ms"], json.dumps(trace["data"], default=str)))

    def add_voice_timings(self, trace_id: str, timings: dict) -> None:
        with self.db.connection() as conn:
            conn.execute("UPDATE traces SET data = jsonb_set(data, '{voice}', %s::jsonb) WHERE id = %s",
                         (json.dumps(timings), trace_id))

    def add_feedback(self, trace_id: str, verdict: str, note: str) -> bool:
        """A tester's judgement of one answer, kept with its trace (and counted in /v1/stats)."""
        with self.db.connection() as conn:
            row = conn.execute("UPDATE traces SET data = jsonb_set(data, '{feedback}', %s::jsonb) WHERE id = %s RETURNING id",
                               (json.dumps({"verdict": verdict, "note": note}), trace_id)).fetchone()
        return row is not None

    def get(self, trace_id: str) -> dict | None:
        if not _is_uuid(trace_id):
            return None
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM traces WHERE id = %s", (trace_id,)).fetchone()

    def recent(self, limit: int = 50, outcome: str | None = None) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT id, session_id, created_at, question, language, intent, outcome, answer, total_ms FROM traces "
                + ("WHERE outcome = %s " if outcome else "") + "ORDER BY created_at DESC LIMIT %s",
                ((outcome, limit) if outcome else (limit,))).fetchall()

    def stats(self, hours: int = 24) -> dict:
        with self.db.connection() as conn:
            outcomes = conn.execute("SELECT outcome, count(*) AS n FROM traces WHERE created_at > now() - make_interval(hours => %s) "
                                    "GROUP BY outcome", (hours,)).fetchall()
            feedback = conn.execute("SELECT data->'feedback'->>'verdict' AS verdict, count(*) AS n FROM traces "
                                    "WHERE data ? 'feedback' AND created_at > now() - make_interval(hours => %s) "
                                    "GROUP BY 1", (hours,)).fetchall()
            latency = conn.execute(
                "SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95 FROM traces "
                "WHERE created_at > now() - make_interval(hours => %s)", (hours,)).fetchone()
            docs = conn.execute(
                "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS documents, "
                "(SELECT count(*) FROM chunks WHERE active) AS searchable_chunks, "
                "(SELECT count(*) FROM jobs WHERE status IN ('queued','running')) AS pending_jobs FROM documents").fetchone()
        return {"window_hours": hours, "questions": latency["n"], "latency_ms": {"p50": latency["p50"], "p95": latency["p95"]},
                "outcomes": {r["outcome"]: r["n"] for r in outcomes},
                "feedback": {r["verdict"]: r["n"] for r in feedback}, **docs}


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
