"""Background ingestion worker: one thread polling the jobs table (FOR UPDATE SKIP LOCKED).

Several app processes can run workers against the same database safely.
"""

import logging
import threading
import time
import traceback

from app.db import Database
from app.logs import log
from app.services.documents import DocumentService

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 2


class IngestWorker:
    def __init__(self, db: Database, documents: DocumentService, poll_s: float = 1.0):
        self.db, self.documents, self.poll_s = db, documents, poll_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ingest-worker", daemon=True)
        self.busy = False

    def start(self) -> None:
        with self.db.connection() as conn:  # jobs left 'running' by a crash go back to the queue
            conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def run_pending(self) -> int:
        """Process every queued job now (used by tests and CLI). Returns how many ran."""
        count = 0
        while self._take_one():
            count += 1
        return count

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._take_one():
                    self._stop.wait(self.poll_s)
            except Exception:  # never let the worker die
                logger.exception("worker loop error")
                self._stop.wait(self.poll_s)

    def _take_one(self) -> bool:
        with self.db.connection() as conn, conn.transaction():
            job = conn.execute(
                "SELECT id, version_id, attempts FROM jobs WHERE status = 'queued' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not job:
                return False
            conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = now() WHERE id = %s",
                         (job["id"],))
        self.busy = True
        started = time.perf_counter()
        try:
            self.documents.process_version(str(job["version_id"]))
            status, error = "done", None
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error("ingest failed", extra={"data": {"job_id": str(job["id"]), "error": error,
                                                          "trace": traceback.format_exc(limit=5)}})
            retry = job["attempts"] + 1 < MAX_ATTEMPTS and not isinstance(e, ValueError)
            status = "queued" if retry else "failed"
            if not retry:
                self.documents.mark_failed(str(job["version_id"]), error)
        finally:
            self.busy = False
        with self.db.connection() as conn:
            conn.execute("UPDATE jobs SET status = %s, error = %s, finished_at = now() WHERE id = %s",
                         (status, error, job["id"]))
        log(logger, "job finished", job_id=str(job["id"]), status=status,
            ms=round((time.perf_counter() - started) * 1000))
        return True

    def pending(self) -> int:
        with self.db.connection() as conn:
            return conn.execute("SELECT count(*) AS n FROM jobs WHERE status IN ('queued', 'running')").fetchone()["n"]
