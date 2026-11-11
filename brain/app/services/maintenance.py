"""Clean slate (PHASE-4-PLAN §16): remove every recorded call so new test calls start from nothing.

Deleted: sessions, turns, call events, alerts, auth events, recordings (rows and encrypted files), handoffs and their
messages, tickets, call reviews, and the mock bank's data (re-seeded on next start, with dates relative to today).
Kept: the knowledge base (documents, versions, chunks, embeddings), jobs, eval runs, the turns of eval runs and other
turns that belong to no call (so golden-run evidence survives), and the recording key.
It cannot be undone, so the caller must pass `confirm=True`.
"""

import logging
import shutil

from app.db import Database
from app.logs import log

logger = logging.getLogger(__name__)

# order does not matter: TRUNCATE ... CASCADE handles the references between them
CALL_TABLES = ("sessions", "traces", "call_events", "alerts", "auth_events", "recordings", "handoffs", "handoff_messages",
               "tickets", "call_reviews")
BANK_SCHEMA = "corebank"


def preview(db: Database) -> dict:
    """What a clean slate would delete (row counts), so the person can look before confirming."""
    out = {}
    with db.connection() as conn:
        for table in CALL_TABLES:
            if _exists(conn, "public", table):
                where = " WHERE session_id IS NOT NULL" if table == "traces" else ""  # turns of no call (evals) are kept
                out[table] = conn.execute(f"SELECT count(*) AS n FROM {table}{where}").fetchone()["n"]
        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (BANK_SCHEMA,)).fetchall()
        out["mock_bank_tables"] = len(tables)
    return out


def clean_slate(db: Database, data_dir, *, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("clean_slate deletes every recorded call and cannot be undone: pass confirm=True")
    removed = preview(db)
    with db.connection() as conn, conn.transaction():
        existing = [t for t in CALL_TABLES if _exists(conn, "public", t)]
        # traces reference sessions, so TRUNCATE sessions CASCADE would wipe every turn, eval runs included: delete the
        # turns that belong to a call, then the sessions with a plain DELETE (nothing left to cascade into)
        if "traces" in existing:
            conn.execute("DELETE FROM traces WHERE session_id IS NOT NULL")
        rest = [t for t in existing if t not in ("traces", "sessions")]
        if rest:
            conn.execute(f"TRUNCATE {', '.join(rest)} RESTART IDENTITY CASCADE")
        if "sessions" in existing:
            conn.execute("DELETE FROM sessions")
        bank = [r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'", (BANK_SCHEMA,)).fetchall()]
        if bank:
            conn.execute(f"TRUNCATE {', '.join(f'{BANK_SCHEMA}.{t}' for t in bank)} RESTART IDENTITY CASCADE")
    recordings = data_dir / "recordings"
    files = sum(1 for _ in recordings.glob("*")) if recordings.is_dir() else 0
    if recordings.is_dir():
        shutil.rmtree(recordings)
    removed["recording_files"] = files
    log(logger, "clean slate done", **removed)
    return removed


def delete_call(db: Database, data_dir, session_id: str) -> dict | None:
    """Delete one call for good: its turns, timeline, verification events, alerts, review, fix-list items, handoffs and
    their messages, recordings (rows and encrypted files) and the call itself. Tickets raised in the mock bank's CRM stay
    (they are the bank's records); their link to the call is cleared. Returns what was deleted, or None if no such call."""
    with db.connection() as conn, conn.transaction():
        if not conn.execute("SELECT 1 FROM sessions WHERE id = %s", (session_id,)).fetchone():
            return None
        files = [r["path"] for r in conn.execute("SELECT path FROM recordings WHERE session_id = %s AND path IS NOT NULL",
                                                 (session_id,)).fetchall()]
        removed = {}
        if _exists(conn, "public", "handoff_messages"):
            removed["handoff_messages"] = conn.execute(
                "DELETE FROM handoff_messages WHERE handoff_id IN (SELECT id FROM handoffs WHERE session_id = %s)",
                (session_id,)).rowcount
        for table in ("fix_items", "traces", "call_events", "alerts", "auth_events", "recordings", "call_reviews", "handoffs"):
            if _exists(conn, "public", table):
                removed[table] = conn.execute(f"DELETE FROM {table} WHERE session_id = %s", (session_id,)).rowcount
        removed["sessions"] = conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,)).rowcount
    for name in files:
        (data_dir / "recordings" / name).unlink(missing_ok=True)
    removed["recording_files"] = len(files)
    log(logger, "call deleted", call=session_id, **removed)
    return removed


def _exists(conn, schema: str, table: str) -> bool:
    return conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                        (schema, table)).fetchone() is not None
