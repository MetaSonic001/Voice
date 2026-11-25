# ADR-0004: Hybrid retrieval: vectors + keywords, merged by RRF, then a reranker

- **Status:** Accepted. The reranker step runs only with a GPU since 25 Nov 2026 (`RERANK=auto`, [0031](0031-eks-uat-cpu-no-reranker.md)); hybrid search and RRF are unchanged
- **Date:** 2026-11-03
- **Deciders:** project owner

## Context

Callers ask in English, Hindi and Hinglish about documents written in English. Questions include exact product names ("Zenith+", "BSBDA") and paraphrases ("byaaj dar" = interest rate). Vector search alone misses exact names; keyword search alone misses meaning and other languages. The answer model should see only a few highly relevant passages, both for accuracy and for speed.

## Decision

1. **Dense search:** embed the question's English rewrite with **BAAI/bge-m3** (multilingual, 1,024 dimensions, fp16 on GPU); take the top **30** by cosine distance from the pgvector **HNSW** index.
2. **Keyword search:** Postgres full-text OR-query on the same rewrite; take the top **30**.
3. Both run **in parallel**.
4. **Merge** with Reciprocal Rank Fusion (RRF, k = 60). It needs no score tuning.
5. **Rerank** the top **16** with the cross-encoder **BAAI/bge-reranker-v2-m3**, which reads the question and passage together.
6. Pass up to **5** passages to the answer model. If the best rerank score is **< 0.03**, refuse without calling the LLM.
7. Warm the GPU while the question is being understood (an idle GPU slows from ~50 ms to ~1 s).

All numbers are settings (`DENSE_K`, `KEYWORD_K`, `RERANK_CANDIDATES`, `CONTEXT_CHUNKS`, `MIN_RERANK_SCORE`).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Hybrid + RRF + rerank** | Catches both names and meaning; reranker gives precision; cost is fixed per query | Two models on the GPU |
| Vectors only | Simplest | Misses exact product names and numbers |
| Keywords only (BM25) | Fast, exact | Fails on Hindi/Hinglish and paraphrases |
| Weighted score blend | Tunable | Weights need constant re-tuning; RRF doesn't |
| LLM reranking | Strong | +0.5–1 s and API quota per question |

## Consequences

- **Easier:** robust multilingual retrieval. Measured on the golden set (3 Nov): hit@5 ≈ 94%, MRR ≈ 0.81. Retrieval ≈ 85 ms when the GPU is warm.
- **Harder:** a GPU is needed for low latency (CPU works, but slower).
- **Known limit:** finding *a* relevant passage isn't the same as using the right **variant** or a **current** document. That's handled by [0018](0018-document-level-kb-metadata.md).
- **Revisit when:** the corpus grows large; see [0022](0022-rag-at-large-scale.md).

## Links

`brain/app/services/retrieval.py`, `PROJECT-GUIDE.md` §7.
