"""Hybrid retrieval: dense (pgvector) + keyword (Postgres full-text) -> Reciprocal Rank Fusion -> rerank.

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
    "v.id AS version_id, v.version_no, v.effective_from"
)
_JOIN = "FROM chunks c JOIN document_versions v ON v.id = c.version_id JOIN documents d ON d.id = v.document_id"


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
    def __init__(self, db: Database, embedder: Embedder, reranker: Reranker, settings):
        self.db, self.embedder, self.reranker, self.s = db, embedder, reranker, settings

    def embed(self, text: str) -> list[float]:
        return self.embedder.embed_query(text)

    def dense(self, vector: list[float], k: int) -> list[Hit]:
        dim = self.embedder.dim
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT {_HIT_COLUMNS}, 1 - (e.embedding::vector({dim}) <=> %s::vector({dim})) AS score "
                f"{_JOIN} JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = %s "
                f"WHERE c.active ORDER BY e.embedding::vector({dim}) <=> %s::vector({dim}) LIMIT %s",
                (str(vector), self.embedder.name, str(vector), k)).fetchall()
        return [Hit(**_hit(r), dense_score=float(r["score"])) for r in rows]

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
        t = time.perf_counter()
        reranked = await asyncio.to_thread(self.rerank, keyword_query or dense_query, candidates) if candidates else []
        timings["rerank_ms"] = _ms(t)
        return reranked, fused


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
    }


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
