-- The pgvector extension is created by app/db.py only when VECTOR_STORE=pgvector (the default), so this schema also
-- works on a Postgres without it (VECTOR_STORE=chroma).

-- A document is the logical item ("CASA + TD KB"); each upload of it is a version.
CREATE TABLE documents (
    id                 uuid PRIMARY KEY,
    title              text NOT NULL,
    category           text,
    purpose            text NOT NULL CHECK (purpose IN ('knowledge', 'data')),
    enabled            boolean NOT NULL DEFAULT true,
    deleted_at         timestamptz,
    current_version_id uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_versions (
    id              uuid PRIMARY KEY,
    document_id     uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    version_no      int NOT NULL,
    filename        text NOT NULL,
    file_type       text NOT NULL,
    size_bytes      bigint NOT NULL,
    sha256          text NOT NULL,
    storage_key     text NOT NULL,
    status          text NOT NULL CHECK (status IN ('queued', 'processing', 'indexed', 'failed', 'superseded')),
    error           text,
    effective_from  date,
    options         jsonb NOT NULL DEFAULT '{}',
    report          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    indexed_at      timestamptz,
    UNIQUE (document_id, version_no)
);
CREATE INDEX document_versions_sha ON document_versions (sha256);

-- Chunks belong to a version. `active` = searchable right now (current version of an enabled, not-deleted document).
CREATE TABLE chunks (
    id            uuid PRIMARY KEY,
    version_id    uuid NOT NULL REFERENCES document_versions (id) ON DELETE CASCADE,
    ordinal       int NOT NULL,
    kind          text NOT NULL,            -- text | table
    section_path  text NOT NULL,
    text          text NOT NULL,
    content_hash  text NOT NULL,
    word_count    int NOT NULL,
    active        boolean NOT NULL DEFAULT false,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', section_path || ' ' || text)) STORED
);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX chunks_version ON chunks (version_id);
CREATE INDEX chunks_active ON chunks (id) WHERE active;

-- Embeddings are stored per model, so a new model can be built alongside the old one.
-- Created as a plain real[]; with pgvector, startup (db.ensure_vector_index) turns it into `vector` and adds the
-- HNSW index for the configured model and dimension. With Chroma it stays real[] (db.ensure_plain_embeddings).
CREATE TABLE chunk_embeddings (
    chunk_id  uuid NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    model     text NOT NULL,
    embedding real[] NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

CREATE TABLE jobs (
    id          uuid PRIMARY KEY,
    kind        text NOT NULL,              -- ingest
    version_id  uuid REFERENCES document_versions (id) ON DELETE CASCADE,
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts    int NOT NULL DEFAULT 0,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    started_at  timestamptz,
    finished_at timestamptz
);
CREATE INDEX jobs_queued ON jobs (created_at) WHERE status = 'queued';

CREATE TABLE sessions (
    id         uuid PRIMARY KEY,
    language   text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- One trace per question: everything needed to explain the answer.
CREATE TABLE traces (
    id          uuid PRIMARY KEY,
    session_id  uuid REFERENCES sessions (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    question    text NOT NULL,
    language    text,
    intent      text,
    outcome     text NOT NULL,              -- answered | refused | clarify | needs_verification | handoff | smalltalk | error
    answer      text NOT NULL,
    total_ms    int NOT NULL,
    data        jsonb NOT NULL              -- understanding, hits, citations, checks, timings, models, prompt version
);
CREATE INDEX traces_session ON traces (session_id, created_at);
CREATE INDEX traces_created ON traces (created_at DESC);

CREATE TABLE eval_runs (
    id          uuid PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    status      text NOT NULL DEFAULT 'running',
    golden_file text NOT NULL,
    config      jsonb NOT NULL,
    metrics     jsonb,
    results     jsonb,
    report_path text,
    error       text
);
