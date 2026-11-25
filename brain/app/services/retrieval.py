"""Hybrid retrieval: dense (pgvector, or numpy per VECTOR_STORE) + keyword (Postgres full-text) -> Reciprocal Rank Fusion -> rerank.

Dense search handles meaning and other languages (Hindi/Hinglish questions over English documents);
keyword search handles exact terms and product names. RRF merges both lists without tuning weights;
the cross-encoder reranker then orders the best candidates precisely.
"""

import asyncio
import time

from app.db import Database
from app.domain import Hit
from app.ports import Embedder, Reranker

RRF_K = 60

_HIT_COLUMNS = (
    "c.id AS chunk_id, c.text, c.section_path, d.id AS document_id, d.title AS document_title, "
    "v.id AS version_id, v.version_no, v.effective_from, d.category AS doc_category, d.tags AS doc_tags"
)
_JOIN = "FROM chunks c JOIN document_versions v ON v.id = c.version_id JOIN documents d ON d.id = v.document_id"


def family_variants(db, family: str) -> list[str]:
    """Every variant of a product family in the live knowledge base ("Savings Account" -> Swadesh, Value, Royale, ...),
    the ones with the most passages first. Read when the agent has to ask "which one?" (rare), so it is never stale."""
    from app.services.kbmeta import product_of
    with db.connection() as conn:
        rows = conn.execute("SELECT section_path FROM chunks WHERE active").fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        f, v = product_of(r["section_path"])
        if v and f == family:
            counts[v] = counts.get(v, 0) + 1
    return sorted(counts, key=lambda v: -counts[v])


def rrf_fuse(dense: list[Hit], keyword: list[Hit]) -> list[Hit]:
    """Merge two ranked lists. A chunk found by both gets both contributions."""
    merged: dict[str, Hit] = {}
    for hits, field in ((dense, "dense_rank"), (keyword, "keyword_rank")):
        for rank, hit in enumerate(hits, start=1):
            existing = merged.setdefault(hit.chunk_id, hit)
            if existing is not hit:  # keep scores from both searches on one object
                existing.keyword_rank, existing.keyword_score = hit.keyword_rank, hit.keyword_score
            setattr(existing, field, rank)
            existing.rrf += 1.0 / (RRF_K + rank)
    return sorted(merged.values(), key=lambda h: h.rrf, reverse=True)


class Retriever:
    def __init__(self, db: Database, embedder: Embedder, reranker: Reranker, settings, vectors=None):
        self.db, self.embedder, self.reranker, self.s = db, embedder, reranker, settings
        self.vectors = vectors  # NumpyVectors (VECTOR_STORE=numpy); None = pgvector inside Postgres
        self._last_used = time.monotonic()

    def embed(self, text: str) -> list[float]:
        self._last_used = time.monotonic()
        return self.embedder.embed_query(text)

    def warm(self) -> None:
        """A GPU that has been idle for ~10 s clocks down and its next run takes ~1 s instead of ~50 ms. Run while
        the question is being understood (an LLM call of ~1 s), this moves that wake-up off the caller's wait."""
        if time.monotonic() - self._last_used < 8:
            return
        self._last_used = time.monotonic()
        self.embedder.embed_query("warm up")
        if self.s.rerank:
            self.reranker.score("warm up", ["warm up"])

    def dense(self, vector: list[float], k: int) -> list[Hit]:
        if self.vectors is not None:
            return self._dense_store(vector, k)
        dim = self.embedder.dim
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT {_HIT_COLUMNS}, 1 - (e.embedding::vector({dim}) <=> %s::vector({dim})) AS score "
                f"{_JOIN} JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = %s "
                f"WHERE c.active ORDER BY e.embedding::vector({dim}) <=> %s::vector({dim}) LIMIT %s",
                (str(vector), self.embedder.name, str(vector), k)).fetchall()
        return [Hit(**_hit(r), dense_score=float(r["score"])) for r in rows]

    def _dense_store(self, vector: list[float], k: int) -> list[Hit]:
        """The numpy vector store finds the nearest chunks among the searchable document versions; Postgres supplies their text.
        `c.active` is checked again, so a document turned off a moment ago never comes back."""
        with self.db.connection() as conn:
            versions = [str(r["v"]) for r in conn.execute(
                "SELECT current_version_id AS v FROM documents WHERE enabled AND deleted_at IS NULL "
                "AND purpose = 'knowledge' AND current_version_id IS NOT NULL").fetchall()]
            found = self.vectors.search([float(x) for x in vector], k, versions)
            if not found:
                return []
            rows = {str(r["chunk_id"]): r for r in conn.execute(
                f"SELECT {_HIT_COLUMNS} {_JOIN} WHERE c.active AND c.id = ANY(%s::uuid[])",
                ([cid for cid, _ in found],)).fetchall()}
        return [Hit(**_hit(rows[cid]), dense_score=score) for cid, score in found if cid in rows]

    def keyword(self, query: str, k: int) -> list[Hit]:
        if not query.strip():
            return []
        with self.db.connection() as conn:
            # OR-of-terms query: any matching word counts, ts_rank_cd rewards more and closer matches.
            rows = conn.execute(
                f"WITH q AS (SELECT to_tsquery('english', (SELECT string_agg(quote_literal(lex), ' | ') "
                f"FROM unnest(tsvector_to_array(to_tsvector('english', %s))) AS lex)) AS query) "
                f"SELECT {_HIT_COLUMNS}, ts_rank_cd(c.tsv, q.query) AS score {_JOIN}, q "
                f"WHERE c.active AND q.query IS NOT NULL AND c.tsv @@ q.query ORDER BY score DESC LIMIT %s",
                (query, k)).fetchall()
        return [Hit(**_hit(r), keyword_score=float(r["score"])) for r in rows]

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        scores = self.reranker.score(query, [f"{h.section_path}\n{h.text}" for h in hits])
        for hit, score in zip(hits, scores):
            hit.rerank_score = score
        return sorted(hits, key=lambda h: h.rerank_score or 0.0, reverse=True)

    async def search(self, dense_query: str, keyword_query: str, timings: dict,
                     dense_vector: list[float] | None = None) -> tuple[list[Hit], list[Hit]]:
        """Returns (reranked candidates, fused list before reranking). Dense and keyword run in parallel."""

        async def dense_task():
            t = time.perf_counter()
            vector = dense_vector or await asyncio.to_thread(self.embed, dense_query)
            if dense_vector is None:
                timings["embed_ms"] = _ms(t)
            t2 = time.perf_counter()
            hits = await asyncio.to_thread(self.dense, vector, self.s.dense_k)
            timings["dense_ms"] = _ms(t2)
            return hits

        async def keyword_task():
            t = time.perf_counter()
            hits = await asyncio.to_thread(self.keyword, keyword_query, self.s.keyword_k)
            timings["keyword_ms"] = _ms(t)
            return hits

        dense_hits, keyword_hits = await asyncio.gather(dense_task(), keyword_task())
        fused = rrf_fuse(dense_hits, keyword_hits)
        candidates = fused[: self.s.rerank_candidates]
        if not self.s.rerank:  # RERANK=false: keep the RRF order; no rerank_ms (the step did not run)
            return without_reranker(candidates), fused
        t = time.perf_counter()
        reranked = await asyncio.to_thread(self.rerank, keyword_query or dense_query, candidates) if candidates else []
        timings["rerank_ms"] = _ms(t)
        return reranked, fused


def without_reranker(candidates: list[Hit]) -> list[Hit]:
    """RERANK=false. The candidates in the RRF order, each with `rerank_score` = its RRF score scaled to 0..1 (1.0 =
    first in both searches), so the code after search (passage trim, source checks) works unchanged. This score only
    orders passages: it does not say whether they answer the question, so the refusal gate uses the embedding
    similarity instead (`Conversation`, MIN_DENSE_SCORE)."""
    best = 2 / (RRF_K + 1)
    for h in candidates:
        h.rerank_score = round(min(1.0, h.rrf / best), 4)
    return candidates


def _hit(row: dict) -> dict:
    return {
        "chunk_id": str(row["chunk_id"]),
        "text": row["text"],
        "section_path": row["section_path"],
        "document_id": str(row["document_id"]),
        "document_title": row["document_title"],
        "version_id": str(row["version_id"]),
        "version_no": row["version_no"],
        "effective_from": row["effective_from"].isoformat() if row["effective_from"] else None,
        "doc_category": row["doc_category"],
        "doc_tags": row["doc_tags"] or {},
    }


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
