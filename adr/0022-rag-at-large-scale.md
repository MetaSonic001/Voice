# ADR-0022: Keeping retrieval fast and accurate at ~50,000 pages

- **Status:** Proposed (latency measured on synthetic data, 6 Nov 2026; accuracy changes not built yet)
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

The current KB is tiny: 7 documents, 220 chunks, about 0.5 MB. A realistic bank KB could be 100 PDFs × 500 pages = 50,000 pages. At ~350 words a page and ~150 words a chunk, that is **~100,000–150,000 chunks** (rate tables add more).

The question is whether the design in [0003](0003-postgres-pgvector-single-store.md), [0004](0004-hybrid-retrieval-rrf-rerank.md) and [0005](0005-structure-aware-chunking-native-parsers.md) survives that, in **latency**, **accuracy** and **ingestion**.

Which steps grow with corpus size:

| Step | Grows with corpus size? |
|---|---|
| Embed the question (bge-m3) | No |
| Dense search (HNSW) | Slightly (roughly logarithmic), **if the index fits in memory** |
| Keyword search (GIN + OR query + `ts_rank_cd`) | **Yes**: an OR of common words matches many chunks, and all of them get ranked |
| Rerank top 16 | No (fixed count) |
| LLM answer (5 passages) | No |

**Measured** (synthetic: 100,000 chunks × 1,024-dim vectors, 150 words each, on the dev laptop's pgserver with its default 128 MB `shared_buffers`):

| What | Result |
|---|---|
| Load 100k rows / build GIN / build HNSW | 132 s / 9 s / 129 s; table + indexes **1.48 GB** |
| Dense HNSW top-30, index **not** in memory (128 MB buffers) | **p50 303 ms**, no better than brute force (349 ms) |
| Dense HNSW top-30, **2 GB buffers, warmed** | **p50 45 ms** (max 53 ms) |
| Keyword: **current OR query**, top-30 | **p50 2.6–6.4 s, max 13 s**. It matched ~97% of rows, and every match gets ranked. |
| Keyword: **AND query** (`plainto_tsquery`), top-30 | **p50 13 ms** (max 160 ms); matched a median of 266 rows |

**Caveats:**
- The text was synthetic, built from the current KB's vocabulary. That makes OR matching worse than a real corpus, but common banking words ("rate", "interest", "card") would still match a large share of a real one.
- Random vectors are a worst case for HNSW; real embeddings cluster and usually search faster.
- Accuracy was **not** measured. It needs a real large corpus and golden questions.

**Conclusion:**
- The **current keyword query does not survive** a 50k-page KB; it alone would add seconds per question.
- Vector search survives **only if the index fits in memory**.
- With both fixes, retrieval stays around **100–150 ms** (vs ~85 ms today). The parts that don't grow (embedding, rerank, LLM) dominate.

## Decision (proposed)

**Keep the architecture** (Postgres + pgvector, hybrid + RRF + rerank) and add these measured, targeted changes when the KB grows past a few thousand pages:

1. **Keyword search: AND first** (`plainto_tsquery`; measured 13 ms vs 2.6–6.4 s). If AND finds fewer than ~10 chunks, fall back to OR over the **rarer** terms only (drop words found in most chunks), inside the metadata filter. Never OR every word across the whole corpus. Check on the current golden set that recall doesn't drop; if it does, keep OR at small KB sizes and switch above a chunk-count threshold.
2. **Metadata filters before vector search** (product, document type, current version, from [0018](0018-document-level-kb-metadata.md)). This shrinks the search space and cuts distractors.
   - Filtered HNSW can return too few rows on pgvector 0.6. Upgrade pgvector to **≥ 0.8** for *iterative index scans*, raise `hnsw.ef_search` (e.g. 100), and **delete embeddings of superseded versions** instead of only marking them inactive.
3. **Memory:** the index must fit in RAM. Production Postgres needs `shared_buffers` / RAM sized to the vector index (~0.5–1 GB at 100k chunks). Optionally store vectors as `halfvec` (16-bit) to halve that.
4. **Accuracy at scale:**
   - raise the rerank window from 16 to ~32 if measured recall drops (~+40 ms);
   - grow the golden set with the corpus;
   - report variant accuracy.
5. **Long-PDF parsing:**
   - strip repeated page headers and footers;
   - keep **page numbers** for citations;
   - join tables split across pages;
   - **detect** image-only pages and flag them (OCR remains a separate decision).
6. **Ingestion runs in background workers.** In production, run parsing in parallel processes and embedding on a GPU worker, separate from the live-call API. One-off cost: hours for parsing 50k pages on CPU, minutes for embedding on a GPU. Only changed documents are re-indexed.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Keep Postgres + pgvector, tune as above** | Same approved system; small changes; measured | Needs Postgres tuning and a pgvector upgrade |
| Dedicated vector DB (Qdrant, Milvus, OpenSearch) | Built-in filtered search at huge scale | Another system to secure and approve; unnecessary at ~150k vectors |
| Managed KB service (AWS Bedrock Knowledge Bases, Azure AI Search, Vertex AI Search) | Managed ingestion, sync and OCR; managed scaling | Less control over chunking (table rows, section paths) and our variant/conflict checks; a network hop per query; vendor lock-in; region availability (e.g. Mumbai) must be checked. Bedrock KB can itself use Aurora PostgreSQL as its vector store, so it doesn't replace Postgres. **Choose it if AU's platform team standardises on it, not because of page count.** |
| Elasticsearch/OpenSearch for keywords (BM25) | Better keyword ranking at scale | Another cluster; only if Postgres full-text can't be bounded |
| LLM query routing to a document subset | Precise | +1 LLM call per question |

## Consequences

- **Easier:** scale without a re-architecture; filters improve accuracy as well as speed.
- **Harder:** an ingestion pipeline for long PDFs (headers, footers, pages, split tables) and a tag-review step.
- **Revisit when:** vectors pass a few million, or measured p95 retrieval exceeds ~150 ms after these changes.

## Links

`brain/app/services/retrieval.py`, `brain/app/db.py` (HNSW index), [0004](0004-hybrid-retrieval-rrf-rerank.md), [0018](0018-document-level-kb-metadata.md).
