# Phase A Plan: Knowledge Service (General Information, No Verification)

Status: plan only, no code yet. Phase A covers MVP journey 1 (informational) as a **text service**. Voice, verification, bank data, actions and transfer come in later phases, and they plug into what we build here instead of replacing it.

---

## 1. What we build in Phase A

A small backend service that does three things:

1. **Manages knowledge documents.** You can upload any document (PDF, DOCX, XLSX, and also CSV, Markdown, HTML, PPTX), see exactly how it was parsed, and later replace, update or delete it. Every version is kept, so you can always tell which version produced an answer.
2. **Answers questions from those documents.** It handles English, Hindi and Hinglish:
   - It gives short, grounded answers with citations.
   - It refuses when the documents don't contain the answer: "I don't have approved information on that; I can connect you to a specialist."
   - It never makes up rates, fees or policy.
3. **Proves how each answer was made.** Every answer has a trace: which chunks were found, by which search, with what scores, which ones the answer used, and how long each step took. An evaluation runner scores the whole thing against a fixed question set.

**Not in Phase A:** voice, telephony, verification, customer data, actions, transfer and UI. FastAPI's built-in API docs page is enough to test with.

---

## 2. Design principles

| Principle | What it means in practice |
|---|---|
| **Swappable parts, small interfaces** | Every external technology sits behind a small interface ("port"): parser, embedder, keyword search, vector search, reranker, LLM, file storage. Switching provider means writing one new adapter and changing one config value. No business code changes. |
| **No over-engineering** | One service, one database (Postgres), a simple background job table. No Kafka, no Redis, no Celery, no microservices, no DI framework. A single "composition root" file wires the adapters together. |
| **One pipeline, chosen by measurement** | There's a single retrieval pipeline, not several to choose between at runtime. Individual parts (embedding model, reranker, LLM) are picked by running the evaluation set, not by opinion. |
| **Channel-agnostic core** | The core takes a text question and returns a structured answer. REST is the first "channel"; voice (Pipecat) and phone come later as more channels calling the same core. |
| **Low latency from day one** | Every step is timed and stored in the trace. Targets per step are set, and the evaluation report shows p50/p95. |
| **Production-ready basics** | Migrations, config through environment variables, structured logs with request IDs, health checks, an API key on admin endpoints, file type and size limits, tests. |

---

## 3. How it works

### 3.1 Ingestion (upload → searchable)

```
Upload ─▶ store original file ─▶ create document + version (status: uploaded)
        ─▶ background job: parse ─▶ chunk ─▶ embed + keyword-index ─▶ status: indexed
        ─▶ switch "current version" (atomic) ─▶ searchable
```

1. **Upload** (`POST /v1/documents`): accept the file and validate type and size.
   - Detect the **file type** from the content, not just the extension: pdf, docx, xlsx, csv, md, html, pptx.
   - Take an optional **category** (for example `deposits`, `cards`, `loans`, `rates`, `general`).
   - Take an optional **purpose**:
     - `knowledge` (default): indexed for answers.
     - `data`: stored only, never indexed. This is for files like the customer dummy-data spreadsheet, which must **never** go into the answer index, because it holds personal data. Phase B reads it through the bank adapter instead.
   - Compute a **SHA-256 hash**. If the same file was already uploaded, return the existing document instead of duplicating it.
   - Save the original file through the **file storage port** (local disk now, S3 later).
2. **Parse** with **Docling**: one library for PDF, DOCX, XLSX, PPTX, HTML and Markdown. It keeps headings, lists and **tables as tables**, producing a structured document tree.
   - An **ingestion report** records pages, sections, tables found, empty pages and warnings (for example "table extraction looks low quality on page 2").
   - **Known risk:** the rates PDF extracted badly with a plain text tool. If Docling's table model doesn't fix it, the fallback is to upload a **clean rates spreadsheet** as its own document. XLSX tables are handled natively.
3. **Chunk** using the document's structure, not a fixed character count:
   - **Text:** split by heading section, aiming for about 200–400 tokens. A section that's too long is split at paragraph boundaries; a section that's too short is merged with its neighbour.
   - **Tables:** split by rows, **repeating the header row in every chunk**, so "15–18 months | 7.00% | 7.60%" is never separated from "Tenure | Regular | Senior citizen".
   - **Context header** on every chunk: "Document title › Product › Section". This is a cheap, deterministic way to make each chunk self-explanatory for search, with no LLM cost.
   - **Duplicates** inside a version (the KB has the Royale section twice) are dropped by content hash.
4. **Index** in Postgres:
   - a **dense vector** in a separate embeddings table keyed by model name, with an HNSW index;
   - a **keyword index** (Postgres full-text `tsvector`, GIN index).

   Because both live in the same database as the documents, **replace and delete are transactional**: there's no risk of a separate vector store drifting out of sync.
5. **Switch:** when the new version is fully indexed, one transaction marks it current and retires the previous version's chunks. Search only ever sees complete versions.

Processing runs in the background through a Postgres job table (one worker loop, `SKIP LOCKED`), so uploads return immediately. `GET /v1/documents/{id}` shows the status and the ingestion report.

### 3.2 Document lifecycle (replace, update, delete)

The model is a **document** (the logical item, for example "CASA + TD KB") with many **versions** (each uploaded file). Chunks belong to a version.

| Operation | Endpoint | What happens |
|---|---|---|
| Upload new | `POST /v1/documents` | New document + version 1 |
| **Replace content** | `POST /v1/documents/{id}/versions` | New version is parsed and indexed in the background, then swapped in atomically. The old version is kept but no longer searched. |
| **Update details** (title, category, effective date, enabled) | `PATCH /v1/documents/{id}` | Metadata only, no re-processing. **Disabling** removes it from search instantly. |
| **Delete** | `DELETE /v1/documents/{id}` | **Soft delete**: removed from search instantly; file and versions kept for audit. A separate **purge** (`DELETE …?purge=true`, admin only) removes the files and chunks permanently. |
| Roll back | `POST /v1/documents/{id}/versions/{v}/activate` | Makes an older version current again. |
| Re-index | `POST /v1/documents/{id}/reindex` (or all) | Re-runs chunking and embedding when the chunker or embedding model changes. Embeddings are stored per model, so a new model can be built alongside the old one and switched in when ready. |
| Inspect | `GET /v1/documents`, `GET /v1/documents/{id}/chunks` | See exactly what was parsed and how it was chunked: the "check what is uploaded" requirement. |

**Why keep old versions:** banking answers must be explainable later. A trace from last week points to `chunk 812 of version 3`. If version 3 was purged, the trace still holds a short copy of the text it used.

**Effective dates:** each version can carry `effective_from` (for example, rates "effective 23 Apr 2026"). Answers can say "as of 23 April 2026", and stale documents are easy to spot.

### 3.3 Answering a question

```
question
  ─▶ understand query (one small LLM call, JSON): language, English search phrasing, in-scope?
  ─▶ in parallel: dense search (original question) + keyword search (English phrasing)
  ─▶ fuse (Reciprocal Rank Fusion) ─▶ rerank top ~20 ─▶ keep top 3–5 above a threshold
  ─▶ nothing above threshold?  ─▶ refusal + offer specialist
  ─▶ grounded answer (LLM, JSON): {answerable, answer, citations[], language}
  ─▶ checks: citations exist? every number in the answer present in the cited chunks?
        fail ─▶ refusal (never an unchecked figure)
  ─▶ response + trace_id
```

- **Why "understand query" first:** the documents are in English, but callers ask in Hindi, Hinglish or English.
  - Dense multilingual search handles cross-language meaning on its own.
  - Keyword search needs English words ("FD", "senior citizen", "premature").
  - One small LLM call returns the language plus an English phrasing; keyword search uses the phrasing, dense search uses the original question.
  - In the voice phase, this call **merges into the main intent-detection call**, so it costs nothing extra there.
- **Hybrid + RRF + reranker** is the single pipeline:
  - Dense search catches meaning and other languages.
  - Keyword search catches exact terms and numbers ("Zenith+", "7824", "BSBDA").
  - RRF merges the two lists without tuning weights.
  - The reranker puts the truly relevant chunk first. This matters because the LLM only gets 3–5 chunks.
- **The figure check is the banking-critical step.** If the answer says "7.10%" but no cited chunk contains 7.10, the answer is rejected and the caller gets the refusal line. This turns "the LLM never makes up rates" from a prompt hope into a code guarantee.
- **The language of the answer** follows the question (English, Hindi, or Hinglish written in Devanagari for Hindi words, ready for TTS later). Answers are kept short, at 1–3 sentences, because they'll be spoken.
- **Clarifying questions:** if the answer depends on a missing detail (FD tenure, senior citizen or not), the answer service returns `needs_clarification` with the question to ask, instead of guessing.

### 3.4 Traceability (where did this answer come from?)

Every `/v1/ask` call stores a **trace**:
- question, detected language, English phrasing;
- dense hits (chunk, score, rank) and keyword hits (chunk, score, rank);
- fused ranks, reranker scores, the chunks sent to the LLM;
- the citations the LLM chose, and the figure-check result;
- model names, prompt version, config version;
- **timings per step**: understand, embed, dense, keyword, fuse, rerank, generate, check, total.

`GET /v1/traces/{id}` returns it, and each citation in the answer links to document → version → section → chunk text.

---

## 4. Technology for Phase A (each behind a port)

| Port | Phase A choice | Why | Swappable to |
|---|---|---|---|
| API | **FastAPI** + Pydantic v2 | Async, typed, automatic API docs page | – |
| Database | **Postgres 16 + pgvector** (SQLAlchemy 2 async, Alembic migrations) | One store for documents, versions, chunks, vectors, keywords, jobs, traces. Transactional replace and delete. Runs locally and on AWS RDS. | – |
| File storage | **Local disk** | Simplest for now | S3 (or MinIO) adapter |
| Parser | **Docling** | One library for PDF/DOCX/XLSX/PPTX/HTML/MD, keeps tables and headings | Unstructured, LlamaParse, cloud document AI |
| Embedder | **bge-m3** (open source, multilingual incl. Hindi, 1024-d, runs locally, free, data stays in India) | Strong cross-language retrieval, no API latency or cost | API embedders (Gemini, Cohere, OpenAI) or newer open models; **benchmark 1–2 alternatives once** with the evaluation set |
| Keyword search | **Postgres full-text** (`tsvector`, GIN) | Built in, available everywhere including RDS | True BM25 extensions (ParadeDB pg_search, pg_textsearch) where the host supports them |
| Reranker | **bge-reranker-v2-m3** (open source, Apache 2.0, multilingual) | Best accuracy gain per millisecond; local | Jina reranker v3, Cohere Rerank API. **CPU speed is a risk**; measure, and switch or use a GPU if p50 > ~150 ms |
| LLM | One provider behind the port, JSON output (Gemini Flash / GPT mini / Claude Haiku / Groq-hosted / Sarvam) | Picked by the evaluation set on Hinglish accuracy and speed | Any of the others |
| Jobs | Postgres job table + one worker loop | No extra infrastructure | A real queue only if volume ever needs it |
| Evaluation | **Own small runner** (plus Ragas metrics where useful) | Simple, versioned, runs in CI | Langfuse later for dashboards |

**Rough latency targets** (to confirm in A5):

| Step | Target (p50) |
|---|---|
| Query understanding | ≤ 250 ms |
| Embed query (bge-m3) | ~20–80 ms |
| Dense + keyword search | ≤ 20 ms (parallel) |
| Rerank top 20 | ≤ 150 ms |
| Answer generation | first tokens ≤ 400 ms |

Retrieval itself should stay **under ~250 ms**.

---

## 5. Structure of the code (clean, not over-built)

```
app/
  api/            routers: documents, ask, search, traces, evals, health
  core/           config (env), logging, errors, ids, timing
  domain/         plain models: Document, Version, Chunk, Answer, Citation, Trace
  ports/          small interfaces: Parser, Chunker, Embedder, VectorIndex, KeywordIndex,
                  Reranker, LLM, FileStorage
  adapters/       docling_parser, bge_embedder, pg_vector, pg_keyword, bge_reranker,
                  llm_<provider>, local_storage (s3_storage later)
  services/       ingestion, lifecycle, retrieval, answering, evaluation
  workers/        ingestion job loop
  container.py    composition root: config → which adapter for each port
migrations/       Alembic
evals/            golden set (YAML, versioned) + runner + reports
tests/            unit (chunker, figure check, fusion), integration (upload → ask)
```

**Rule:** `services` depend only on `ports` and `domain`, never on a concrete adapter. That is what makes swapping safe.

### Main tables
- `documents`: id, title, category, purpose, enabled, current_version_id, created/updated
- `document_versions`: id, document_id, version_no, filename, mime, size, sha256, storage_key, status, effective_from, parser/chunker version, ingestion_report, created_at, superseded_at
- `chunks`: id, version_id, ordinal, section_path, kind (text / table_row), text, content_hash, token_count, tsvector
- `chunk_embeddings`: chunk_id, model, vector (HNSW index per model)
- `jobs`: id, type, payload, status, attempts, error, timestamps
- `traces`: id, question, language, steps (JSON), chunks used (with text snapshot), answer, timings, model / prompt / config versions
- `eval_runs`: id, golden_set_version, config snapshot, metrics, per-question results

### Endpoints (Phase A)
- **Documents:** `POST /v1/documents` · `GET /v1/documents` · `GET /v1/documents/{id}` · `GET /v1/documents/{id}/chunks` · `PATCH /v1/documents/{id}` · `DELETE /v1/documents/{id}` · `POST /v1/documents/{id}/versions` · `POST /v1/documents/{id}/versions/{v}/activate` · `POST /v1/documents/{id}/reindex`
- **Answers:** `POST /v1/ask` (answer + citations + trace_id) · `POST /v1/search` (retrieval only, for debugging) · `GET /v1/traces/{id}`
- **Evaluation:** `POST /v1/evals/run` · `GET /v1/evals/{run_id}`
- **Ops:** `GET /health` (alive) · `GET /health/ready` (database reachable)

---

## 6. Evaluation from day one

**Golden set** (YAML, version-controlled), about **80–120 questions** to start:
- Product, rate, fee and eligibility questions answerable from the KB and rates sheet. This includes sample-question section 8 and the general parts of sections 1–4 (for example minimum balance for Value, senior citizen 1-year FD rate, lounge access on Zenith+).
- **Each asked in English, Hindi and Hinglish.**
- **Must-refuse questions** (about 20%): not in the KB (FD premature penalty, PL documents), out of scope, or personal ("what's my balance?" → not a Phase A answer).
- **Needs-clarification questions** ("FD rate kya hai?" without a tenure).

Each item holds the question, language, expected key facts, and **expected source passages as "must-contain" text**. Text is used rather than chunk IDs, because chunk IDs change on re-indexing.

**Metrics** (reported per language and overall):

| Area | Metric |
|---|---|
| Retrieval | Hit@5, Recall@5, MRR: is the right passage found, and how high? |
| Answer | Key-fact correctness (exact match for numbers, LLM judge for wording) |
| Grounding | Share of answers whose every figure appears in a cited chunk (target **100%**) |
| Refusal | Correctly refused / wrongly refused / wrongly answered |
| Clarification | Asked when it should; didn't when it shouldn't |
| Latency | p50/p95 per step and total |
| Cost | Tokens and ₹ per question |

**Proposed Phase A "done" bar:**

| Metric | Target |
|---|---|
| Hit@5 | ≥ 0.90 in all three languages |
| Answer correctness | ≥ 0.90 |
| Ungrounded figures | 0 |
| Wrongly answered (should have refused) | ≤ 2% |
| Retrieval p50 | ≤ 250 ms |

Every failure in the report links to its trace, so you can see whether search missed the passage, the reranker buried it, or the LLM misread it.

---

## 7. Build order inside Phase A

| Step | Build | Done when |
|---|---|---|
| **A0 Skeleton** | Repo, config, logging, Postgres + pgvector, migrations, health checks, ports, container, CI with tests | Service starts; `/health/ready` is green |
| **A1 Ingestion + lifecycle** | Upload, type detection, hash dedupe, storage, Docling parsing, chunking (sections + table rows + context header), ingestion report, versions, replace, disable, soft delete, purge, roll back, job worker | All KB docs + rates PDF uploaded; `/chunks` shows sensible chunks; replace and delete behave atomically |
| **A2 Indexing** | Embeddings (bge-m3) + full-text index, per-model embedding table, re-index | Every current chunk has a vector and keyword entry |
| **A3 Retrieval + traces** | Query understanding, dense + keyword in parallel, RRF, reranker, threshold, `/search`, trace storage | `/search` returns the right passage for the sample questions in all three languages |
| **A4 Answering** | Grounded JSON answer with citations, figure check, refusal, clarification, language-matched short answers, `/ask` | Correct, cited answers; refusals when the KB has nothing |
| **A5 Evaluation + tuning** | Golden set, runner, report; tune chunk size, top-k, threshold; benchmark 1–2 alternative embedders / rerankers / LLMs once; lock the choices | The "done" bar in §6 is met; choices recorded with their numbers |

**Prepare in parallel (not code):**
- write the golden set;
- check whether Docling reads the rates PDF correctly, and if not, prepare a clean rates spreadsheet;
- list the KB conflicts to resolve with AU (Vikaas limit, Zenith+ fee, PL rate).

---

## 8. How later phases plug in (nothing rebuilt)

```
            ┌───────────────── channels ─────────────────┐
            │ REST (Phase A) · Browser voice · Phone      │   voice = Pipecat wrapper
            └───────────────────────┬────────────────────┘
                                    ▼
                    Conversation service (Phase B+)
          intent detection (LLM JSON) · state machine · language · templates
                 │                 │                  │               │
                 ▼                 ▼                  ▼               ▼
      Knowledge service    Policy gateway ──▶  Bank adapter     Handoff service
       (Phase A, as is)    verification levels,  (mock → real)   (tickets, console)
                           confirmation tokens,
                           idempotency, audit log
```

| Phase | What gets added | What from Phase A is reused unchanged |
|---|---|---|
| **B: Personalised (read-only, verified)** | Conversation service (intent detection → state machine), session + verification service (caller ID + last 4), **bank adapter port** with a mock adapter over the dummy spreadsheet (uploaded with `purpose=data`, loaded read-only), response templates with Indian number formatting, audit event log (hash-chained), prefetch | Knowledge service becomes the "general info" tool the state machine calls. Ports/adapters pattern, config, jobs, traces, evaluation runner (extended with journey cases) |
| **C: Controlled actions** | Action tools behind the policy gateway (OTP step-up, 60 s confirmation token, idempotency key), runtime state tables (card status, references), verified-result handling, SMS mock | Everything above |
| **D: Human transfer** | Handoff service: department routing, context card, ticket when the queue is closed, agent console | Everything above |
| **Voice** | Pipecat pipeline as a new channel: STT/TTS ports (Sarvam first), VAD, turn detection, barge-in, fillers; the latency waterfall extends the trace | The conversation service is channel-agnostic, so voice only adds audio in front and behind it |

The parts that **change** later are small and planned:
- the "understand query" call merges into the intent-detection call;
- answers gain a "speech" format (shorter, numbers as words);
- traces gain audio and latency stages.

---

## 9. Open points to decide before A1
1. **Where Postgres runs for development:** local install, Docker, or a managed dev database.
2. **The first LLM provider** for A3/A4. Any one works; A5 picks the final one.
3. **CPU or GPU** for the local embedding model and reranker during development. CPU is fine to start, but measure the reranker.
4. **Which documents are authoritative** when the KB and the rates sheet disagree. The current suggestion is the rates sheet for rates and fees, and the KB for product features.

---

## Sources (research for this plan)
- Embeddings: [BentoML open-source embedding guide 2026](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) · [Hindi-BEIR benchmark](https://arxiv.org/pdf/2408.09437) · [Choosing embedding models via MTEB](https://thottingal.in/blog/2026/10/09/mteb-scores/)
- Rerankers: [Best rerankers 2026](https://futureagi.com/blog/best-rerankers-for-rag-2026/) · [Jina reranker v3](https://jina.ai/models/jina-reranker-v3) · [Reranker benchmark](https://aimultiple.com/rerankers) · [BGE vs Jina rerankers](https://agentset.ai/rerankers/compare/baaibge-reranker-v2-m3-vs-jina-reranker-v2-base-multilingual)
- Parsing: [PDF table extraction: Docling](https://boringbot.substack.com/p/pdf-table-extraction-showdown-docling) · [PDF parser benchmark](https://github.com/applied-artificial-intelligence/pdf-parser-benchmark) · [Unstructured benchmark](https://unstructured.io/benchmarks)
- Hybrid search in Postgres: [TigerData: BM25 + vector + RRF in Postgres](https://www.tigerdata.com/blog/elasticsearchs-hybrid-search-now-in-postgres-bm25-vector-rrf) · [Replacing Elasticsearch with Postgres hybrid search](https://www.softwareseni.com/replacing-elasticsearch-with-postgres-using-bm25-hybrid-search-and-rrf/)
- RAG evaluation: [RAG evaluation 2026 (PremAI)](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026) · [Anyscale RAG evaluation](https://docs.anyscale.com/rag/evaluation) · [RAG eval metrics guide](https://dev.to/dublecc/rag-evaluation-metrics-guide-2026-faithfulness-context-precision-and-how-to-score-a-pipeline-46gi)
