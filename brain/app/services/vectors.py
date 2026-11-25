"""Vector search outside Postgres, for a Postgres that has no pgvector extension (VECTOR_STORE=numpy).

NumpyVectors: every vector in memory, exact search with one matrix product. No extra library (numpy is already loaded
by torch), so nothing new for a locked-down laptop's Application Control to block. About 10 ms per search at ~50,000
chunks. The normal setup is pgvector (Docker Postgres locally, RDS on AWS); this is the fallback.

Postgres stays the source of truth: chunk_embeddings keeps every vector as a plain real[] column, and `sync` at
startup (re)builds the index from it without re-embedding anything.
Going back to pgvector: set VECTOR_STORE=pgvector; db.ensure_vector_index converts the column back.

Only the running brain writes here (uploads go through its API).
"""

import logging
import threading
import time

import numpy as np

from app.logs import log

logger = logging.getLogger(__name__)


class NumpyVectors:
    """Exact cosine search over vectors held in memory (VECTOR_STORE=numpy)."""

    def __init__(self, model: str, dim: int):
        self.model, self.dim = model, dim
        self._rows: dict[str, tuple[np.ndarray, str, str]] = {}  # chunk_id -> (unit vector, version_id, document_id)
        self._lock = threading.Lock()
        self._index = None  # (ids, matrix, version_ids), rebuilt on the first search after a change

    @staticmethod
    def _unit(vec) -> np.ndarray:
        v = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    # ---------- writes ----------

    def add(self, ids: list[str], vectors: list[list[float]], version_ids: list[str], document_ids: list[str]) -> None:
        with self._lock:
            for cid, vec, ver, doc in zip(ids, vectors, version_ids, document_ids):
                self._rows[cid] = (self._unit(vec), ver, doc)
            self._index = None

    def delete_version(self, version_id: str) -> None:
        self._drop(lambda ver, doc: ver == version_id)

    def delete_document(self, document_id: str) -> None:
        self._drop(lambda ver, doc: doc == document_id)

    def _drop(self, match) -> None:
        with self._lock:
            for cid in [c for c, (_, ver, doc) in self._rows.items() if match(ver, doc)]:
                del self._rows[cid]
            self._index = None

    # ---------- search ----------

    def _snapshot(self):
        with self._lock:
            if self._index is None:
                ids = list(self._rows)
                matrix = (np.stack([self._rows[c][0] for c in ids]) if ids
                          else np.zeros((0, self.dim), dtype=np.float32))
                self._index = (ids, matrix, np.array([self._rows[c][1] for c in ids], dtype=object))
            return self._index

    def search(self, vector: list[float], k: int, version_ids: list[str]) -> list[tuple[str, float]]:
        """The k nearest chunks among the given (active) document versions, as (chunk_id, cosine similarity)."""
        ids, matrix, versions = self._snapshot()
        if not ids or not version_ids:
            return []
        rows = np.flatnonzero(np.isin(versions, version_ids))
        if not rows.size:
            return []
        scores = matrix[rows] @ self._unit(vector)
        top = np.argpartition(-scores, k - 1)[:k] if scores.size > k else np.arange(scores.size)
        top = top[np.argsort(-scores[top])]
        return [(ids[rows[i]], float(scores[i])) for i in top]

    # ---------- load ----------

    def sync(self, db) -> None:
        """Load every vector of this model from Postgres (binary transfer: fast even for large knowledge bases)."""
        t = time.perf_counter()
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT e.chunk_id, e.embedding, c.version_id, v.document_id FROM chunk_embeddings e "
                "JOIN chunks c ON c.id = e.chunk_id JOIN document_versions v ON v.id = c.version_id "
                "WHERE e.model = %s", (self.model,), binary=True).fetchall()
        with self._lock:
            self._rows = {str(r["chunk_id"]): (self._unit(r["embedding"]), str(r["version_id"]), str(r["document_id"]))
                          for r in rows}
            self._index = None
        log(logger, "numpy vectors ready", vectors=len(rows), ms=round((time.perf_counter() - t) * 1000))
