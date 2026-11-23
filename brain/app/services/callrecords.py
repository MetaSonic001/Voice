"""The call record: one JSON file per call in the file store, written after the call (23 Nov 2026).

What it holds: everything `AuditStore.call_rows` returns: the call's details, every turn exactly as stored (with its hash,
so the chain can be checked again from the file alone), the verification steps, the call timeline, alerts and recording
details. Never the keypad code: `sessions.auth` (the code's hash) is not in it.

Where: data_dir/call_records on this machine, or <S3_PREFIX>call_records/ in the bucket with FILE_STORE=s3. On AWS
the bucket has Object Lock and versioning, so a record cannot be changed or deleted, and writing it again (a chat that
went on after it was exported) adds a new version instead of replacing the old one.

When:
- at hang-up (api/voice.py), in the background: the caller never waits for it;
- by `sweep` (a background thread, every 5 minutes), for calls that were never exported or have new turns since: a
  dropped call (no hang-up seen, e.g. a crashed container), a text chat that went quiet (CALL_IDLE_MIN).

Rows removed later (CALL_ROWS_DAYS > 0, production): that many days after export, a call's turns, timeline and
verification events are deleted from Postgres and its sessions row keeps only the call index (times, language,
verified, end reason, chain hash, record location, counts). `sessions.facts`, `state`, `pending` and `auth` are
cleared. Alerts, recordings, reviews and the fix list stay. The /calls page then rebuilds the call from the record.
0 (laptop) keeps every row, so testing works exactly as before.
"""

import json
import logging
import threading
from datetime import datetime

from app.config import Settings
from app.db import Database
from app.logs import log
from app.ports import FileStore
from app.services.audit import AuditStore

logger = logging.getLogger(__name__)
SWEEP_EVERY_S = 300
SWEEP_LOCK = 7_241_013  # advisory lock: one copy of the brain sweeps at a time
# Calls to export: activity in the last 3 days (turns, timeline, verification steps) newer than their record, and the
# call ended or went quiet. Driven by the recent rows (indexed by time), not by scanning every call.
_DUE = (
    "WITH recent AS (SELECT session_id, max(created_at) AS last FROM ("
    "SELECT session_id, created_at FROM traces WHERE session_id IS NOT NULL AND created_at > now() - interval '3 days' "
    "UNION ALL SELECT session_id, created_at FROM call_events WHERE created_at > now() - interval '3 days' "
    "UNION ALL SELECT session_id, created_at FROM auth_events WHERE session_id IS NOT NULL "
    "AND created_at > now() - interval '3 days') x GROUP BY session_id) "
    "SELECT s.id FROM recent r JOIN sessions s ON s.id = r.session_id WHERE s.purged_at IS NULL "
    "AND (s.exported_at IS NULL OR r.last > s.exported_at) "
    "AND (s.ended_at IS NOT NULL OR r.last < now() - make_interval(mins => %s)) ORDER BY r.last LIMIT 500")
DATES = ("created_at", "ended_at", "verified_at", "started_at", "deleted_at", "exported_at")


class CallRecords:
    def __init__(self, db: Database, audit: AuditStore, files: FileStore, settings: Settings):
        self.db, self.audit, self.files, self.s = db, audit, files, settings
        self._sweeping = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # write ----------------------------------------------------------------------------------------------------
    def export(self, session_id: str) -> str | None:
        """Write this call's record; returns its key (None: no such call, or nothing to export yet)."""
        rows = self.audit.call_rows(session_id)
        if rows is None or rows.get("purged_at") or not (rows["turns"] or rows["events"] or rows["recordings"]):
            return None
        key = rows.get("record_key") or self._key(rows)
        rows = {k: v for k, v in rows.items() if k not in ("record_key", "exported_at", "purged_at")}
        # `exported_at` = the newest turn or event in this record (not the clock): anything saved later is newer, so
        # the sweep exports again and rows are never removed before the record has them
        covered = max((r["created_at"] for part in ("turns", "events", "auth_events") for r in rows[part]),
                      default=rows["created_at"])
        record = {"format": 1, "exported_at": covered, **rows}
        self.files.put(key, json.dumps(record, default=_json, ensure_ascii=False).encode("utf-8"))
        turns = rows["turns"]
        summary = {"turns": len(turns),
                   "first_at": _json(turns[0]["created_at"]) if turns else None,
                   "last_at": _json(turns[-1]["created_at"]) if turns else None,
                   "answered": sum(1 for t in turns if t["outcome"] == "answered"),
                   "unanswered": sum(1 for t in turns if t["outcome"] in ("refused", "error"))}
        with self.db.connection() as conn:
            conn.execute("UPDATE sessions SET record_key = %s, exported_at = %s, summary = %s WHERE id = %s",
                         (key, covered, json.dumps(summary), session_id))
        log(logger, "call record saved", call=session_id, key=key, turns=len(turns))
        return key

    def after_call(self, session_id: str) -> None:
        """Hang-up: export this call now (the sweep catches anything this misses)."""
        if not self.s.export_calls:
            return
        try:
            self.export(session_id)
        except Exception:  # the rows are still in Postgres: the next sweep retries
            logger.exception("call record not saved (the next sweep retries)")

    def start(self) -> None:
        """Sweep every 5 minutes in a background thread (started with the ingest worker; tests call `sweep` directly)."""
        if self.s.export_calls and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="call-records", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.wait(SWEEP_EVERY_S):
            try:
                self.sweep()
            except Exception:  # never let the thread die
                logger.exception("call record sweep failed")

    def sweep(self) -> dict:
        """Export calls that need it, then remove rows past CALL_ROWS_DAYS. Safe to run on several copies at once."""
        out = {"exported": 0, "rows_removed": 0}
        if not self.s.export_calls or not self._sweeping.acquire(blocking=False):
            return out
        try:
            with self.db.connection() as conn:
                if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (SWEEP_LOCK,)).fetchone()["ok"]:
                    return out
                try:
                    due = [r["id"] for r in conn.execute(_DUE, (self.s.call_idle_min,)).fetchall()]
                    for sid in due:
                        try:
                            out["exported"] += bool(self.export(str(sid)))
                        except Exception:
                            logger.exception("call record not saved", extra={"data": {"call": str(sid)}})
                    out["rows_removed"] = self._remove_rows(conn)
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (SWEEP_LOCK,))
        finally:
            self._sweeping.release()
        if out["exported"] or out["rows_removed"]:
            log(logger, "call records swept", **out)
        return out

    def _remove_rows(self, conn) -> int:
        days = self.s.call_rows_days
        if days <= 0:
            return 0
        # only calls whose record has every row (nothing newer than what was exported)
        ids = [r["id"] for r in conn.execute(
            "SELECT s.id FROM sessions s WHERE s.purged_at IS NULL AND s.exported_at IS NOT NULL "
            "AND s.exported_at < now() - %s * interval '1 day' "
            + "".join(f"AND NOT EXISTS (SELECT 1 FROM {t} x WHERE x.session_id = s.id AND x.created_at > s.exported_at) "
                      for t in ("traces", "call_events", "auth_events")) + "LIMIT 500", (days,)).fetchall()]
        for sid in ids:
            with conn.transaction():
                for table in ("traces", "call_events", "auth_events"):
                    conn.execute(f"DELETE FROM {table} WHERE session_id = %s", (sid,))
                conn.execute("UPDATE sessions SET facts = '{}', state = '{}', pending = NULL, auth = '{}', "
                             "purged_at = now() WHERE id = %s", (sid,))
        return len(ids)

    # read -----------------------------------------------------------------------------------------------------
    def load(self, key: str | None) -> dict | None:
        """The stored record as rows again (dates back to datetimes), or None if it can't be read."""
        if not key:
            return None
        try:
            with self.files.local_path(key) as path:
                record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("call record could not be read", extra={"data": {"key": key}})
            return None
        record.pop("format", None)
        record.pop("exported_at", None)
        _dates(record)
        for part in ("turns", "auth_events", "events", "alerts", "recordings"):
            for row in record.get(part) or []:
                _dates(row)
        return record

    def delete(self, key: str | None) -> None:
        """Local development only (clean slate, delete one call): on AWS, Object Lock refuses it, as it should."""
        if key:
            self.files.delete_prefix(key)

    @staticmethod
    def _key(rows: dict) -> str:
        started = rows["created_at"]
        return f"{started:%Y/%m/%d}/{rows['id']}.json"  # by date: lifecycle rules and Athena partitions


def _json(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _dates(row: dict) -> None:
    for k in DATES:
        if isinstance(row.get(k), str):
            row[k] = datetime.fromisoformat(row[k])
