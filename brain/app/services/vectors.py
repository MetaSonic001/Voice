"""Vector search outside Postgres, for a Postgres that has no pgvector extension.

- VECTOR_STORE=numpy (NumpyVectors): every vector in memory, exact search with one matrix product. No extra library
  (numpy is already loaded by torch), so nothing new for a locked-down laptop's Application Control to block.
  About 10 ms per search at ~50,000 chunks.
- VECTOR_STORE=chroma (ChromaVectors): in-process Chroma under data_dir/chroma (its Rust DLL must be allowed to load).

In both, Postgres stays the source of truth: chunk_embeddings keeps every vector as a plain real[] column, and `sync`
at startup (re)builds the index from it without re-embedding anything. Both classes have the same methods.
Going back to pgvector: set VECTOR_STORE=pgvector; db.ensure_vector_index converts the column back.

Only the running brain writes here (uploads go through its API).
"""

import logging
import threading
import time
from pathlib import Path

import numpy as np

from app.logs import log

logger = logging.getLogger(__name__)
BATCH = 1000  # well under Chroma's per-call limit


class ChromaVectors:
    def __init__(self, path: Path, model: str, dim: int):
        import chromadb  # optional dependency: only needed when VECTOR_STORE=chroma
        from chromadb.config import Settings as ChromaSettings

        path.mkdir(parents=True, exist_ok=True)
        self.model, self.dim = model, dim
        self.client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
        safe = "".join(c if c.isalnum() else "_" for c in model.lower())[:40]
        # One collection per embedding model and size, like the per-model pgvector index. No embedding function:
        # vectors always come from our own embedder.
        self.collection = self.client.get_or_create_collection(
            f"kb_{safe}_{dim}", metadata={"hnsw:space": "cosine"}, embedding_function=None)

    # ---------- writes ----------

    def add(self, ids: list[str], vectors: list[list[float]], version_ids: list[str], document_ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self.collection.upsert(
                ids=ids[i:i + BATCH], embeddings=vectors[i:i + BATCH],
                metadatas=[{"version_id": v, "document_id": d}
                           for v, d in zip(version_ids[i:i + BATCH], document_ids[i:i + BATCH])])

    def delete_version(self, version_id: str) -> None:
        self.collection.delete(where={"version_id": version_id})

    def delete_document(self, document_id: str) -> None:
        self.collection.delete(where={"document_id": document_id})

    # ---------- search ----------

    def search(self, vector: list[float], k: int, version_ids: list[str]) -> list[tuple[str, float]]:
        """The k nearest chunks among the given (active) document versions, as (chunk_id, cosine similarity)."""
        if not version_ids:
            return []
        where = {"version_id": version_ids[0]} if len(version_ids) == 1 else {"version_id": {"$in": version_ids}}
        res = self.collection.query(query_embeddings=[vector], n_results=k, where=where, include=["distances"])
        return [(cid, 1.0 - float(d)) for cid, d in zip(res["ids"][0], res["distances"][0])]

    # ---------- repair ----------

    def sync(self, db) -> None:
        """Make Chroma hold exactly the vectors Postgres has for this model: drop extras, copy missing ones."""
        t = time.perf_counter()
        with db.connection() as conn:
            pg_ids = {str(r["chunk_id"]) for r in conn.execute(
                "SELECT chunk_id FROM chunk_embeddings WHERE model = %s", (self.model,)).fetchall()}
        have = set(self.collection.get(include=[])["ids"])
        extra, missing = list(have - pg_ids), list(pg_ids - have)
        for i in range(0, len(extra), BATCH):
            self.collection.delete(ids=extra[i:i + BATCH])
        for i in range(0, len(missing), BATCH):
            with db.connection() as conn:
                rows = conn.execute(
                    "SELECT e.chunk_id, e.embedding, c.version_id, v.document_id FROM chunk_embeddings e "
                    "JOIN chunks c ON c.id = e.chunk_id JOIN document_versions v ON v.id = c.version_id "
                    "WHERE e.model = %s AND e.chunk_id = ANY(%s::uuid[])", (self.model, missing[i:i + BATCH])).fetchall()
            self.add([str(r["chunk_id"]) for r in rows], [list(r["embedding"]) for r in rows],
                     [str(r["version_id"]) for r in rows], [str(r["document_id"]) for r in rows])
        log(logger, "chroma ready", vectors=len(pg_ids), added=len(missing), removed=len(extra),
            ms=round((time.perf_counter() - t) * 1000))


class NumpyVectors:
    """Exact cosine search over vectors held in memory (VECTOR_STORE=numpy). Same methods as ChromaVectors."""

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
