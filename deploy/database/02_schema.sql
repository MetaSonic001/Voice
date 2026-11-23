-- AU voice agent: every table, index and schema the app needs (run 2 of 2).
-- Built from brain/app/migrations/001-013 (18 Nov 2026; 012-013 added 23 Nov). Same statements, same order, in one transaction.
--
-- Run in the database au_voice. Connect as the master user (01_bootstrap.sql made it a member of au_voice_app)
-- or as au_voice_app itself:
--   psql "host=<RDS endpoint> dbname=au_voice user=<master> sslmode=require" -v ON_ERROR_STOP=1 -f 02_schema.sql
-- Everything is created as au_voice_app (SET ROLE below), so the app owns its tables: it needs that to re-seed the
-- mock bank (TRUNCATE) and to apply later migrations.
-- At the end the 13 migrations are recorded in schema_migrations, so the app sees them done and changes nothing.

\set ON_ERROR_STOP on
BEGIN;
SET ROLE au_voice_app;

-- The app's record of applied migration files (brain/app/db.py).
CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now());

-- ════════ 001_init.sql ════════
CREATE EXTENSION IF NOT EXISTS vector;

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
-- The HNSW index is created at startup for the configured model and dimension.
CREATE TABLE chunk_embeddings (
    chunk_id  uuid NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    model     text NOT NULL,
    embedding vector NOT NULL,
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

-- ════════ 002_auth.sql ════════
-- MVP 2: caller verification (registered mobile + one-time code) and an audit trail of every auth step.

ALTER TABLE sessions
    ADD COLUMN caller_number text,               -- the number the call came from (telephony caller ID; simulated in the UI)
    ADD COLUMN customer_id   text,               -- set only after a successful verification
    ADD COLUMN verified_at   timestamptz,
    ADD COLUMN auth          jsonb NOT NULL DEFAULT '{}';  -- OTP hash/expiry/attempts, the question waiting for it

CREATE TABLE auth_events (
    id          bigserial PRIMARY KEY,
    session_id  uuid REFERENCES sessions (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    event       text NOT NULL,                   -- otp_sent | otp_verified | otp_failed | locked | mobile_not_registered | data_read | ...
    customer_id text,
    detail      jsonb NOT NULL DEFAULT '{}'      -- never the code itself; masked numbers only
);
CREATE INDEX auth_events_session ON auth_events (session_id, created_at);

-- ════════ 003_call_facts.sql ════════
-- What the caller has told us that matters for answers (senior citizen, product, tenure, amount, account),
-- kept for the whole call - unlike the recent-turns window, it is never forgotten.
ALTER TABLE sessions ADD COLUMN facts jsonb NOT NULL DEFAULT '{}';

-- ════════ 004_audit.sql ════════
-- Call audit: tamper-evident turns, call timeline, recordings and alerts. Only additions: nothing existing changes.

ALTER TABLE sessions
    ADD COLUMN ended_at   timestamptz,          -- live call hung up (chat sessions stay open)
    ADD COLUMN end_reason text,
    ADD COLUMN chain_hash text,                 -- hash of the latest turn: deleting or editing any turn breaks the chain
    ADD COLUMN chain_len  int NOT NULL DEFAULT 0;

ALTER TABLE traces
    ADD COLUMN prev_hash text,                  -- hash of the previous turn of the same session ('' for the first)
    ADD COLUMN hash      text;                  -- SHA-256(prev_hash + this turn's content); NULL for turns before this migration

-- Things that happen in a call besides question/answer turns: disclosure played, caller interrupted, call ended.
CREATE TABLE call_events (
    id         bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX call_events_session ON call_events (session_id, created_at);

-- Audio of a live call (stereo: caller left, agent right) or of one voice-note turn. Encrypted on disk.
CREATE TABLE recordings (
    id          uuid PRIMARY KEY,
    session_id  uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    trace_id    uuid,                           -- voice note: the turn it belongs to
    kind        text NOT NULL,                  -- call | voice_note
    started_at  timestamptz NOT NULL,
    duration_ms int NOT NULL,
    format      text NOT NULL,                  -- audio/ogg (Opus)
    bytes       int NOT NULL,
    sha256      text NOT NULL,                  -- of the stored (encrypted) file
    path        text,                           -- NULL once deleted by the retention policy
    deleted_at  timestamptz
);
CREATE INDEX recordings_session ON recordings (session_id, started_at);

CREATE TABLE alerts (
    id         bigserial PRIMARY KEY,
    session_id uuid REFERENCES sessions (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL,                   -- wants_human | struggling | system_health
    severity   text NOT NULL,                   -- high | medium | critical
    message    text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX alerts_created ON alerts (id DESC);
CREATE INDEX alerts_session ON alerts (session_id, kind);

-- ════════ 005_corebank.sql ════════
-- Phase 3. Two separate things:
--  1. schema "corebank": a MOCK of the bank's own systems (accounts, cards, deposits, loans). The agent reaches it
--     only through the Bank port (adapters/mock_corebank.py), exactly as it would reach real bank APIs later.
--  2. app tables for what the agent itself manages: pending confirmations, tickets, handoffs to people.
-- Only additions: nothing existing changes.

CREATE SCHEMA corebank;

CREATE TABLE corebank.branches (
    id   text PRIMARY KEY,
    name text NOT NULL,
    city text NOT NULL,
    type text NOT NULL                          -- urban | semi_urban | rural (decides some balance rules)
);

CREATE TABLE corebank.customers (
    cif        text PRIMARY KEY,
    name       text NOT NULL,
    mobile     text NOT NULL UNIQUE,            -- registered mobile (last 10 digits)
    segment    text NOT NULL DEFAULT 'regular', -- regular | senior | priority
    branch_id  text NOT NULL REFERENCES corebank.branches (id),
    language   text,                            -- preferred language, if known
    rm_name    text,                            -- relationship manager (priority customers)
    scenario   text                             -- demo customers only: what their data is set up to show
);

CREATE TABLE corebank.accounts (
    number            text PRIMARY KEY,
    cif               text NOT NULL REFERENCES corebank.customers (cif),
    variant           text NOT NULL,            -- Digital | Value | Platinum | Royale | BSBDA | Vikaas | ...
    branch_id         text NOT NULL REFERENCES corebank.branches (id),
    status            text NOT NULL DEFAULT 'active',   -- active | inactive | dormant
    is_salary         boolean NOT NULL DEFAULT false,
    opened_on         date NOT NULL,
    ledger_balance    numeric(14,2) NOT NULL,
    available_balance numeric(14,2) NOT NULL,
    hold_amount       numeric(14,2) NOT NULL DEFAULT 0,
    hold_reason       text,
    amb_required      numeric(14,2) NOT NULL DEFAULT 0,
    amb_this_month    numeric(14,2),
    amb_last_month    numeric(14,2)
);
CREATE INDEX ON corebank.accounts (cif);

CREATE TABLE corebank.account_txns (
    id             bigserial PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    posted_at      timestamptz NOT NULL,
    description    text NOT NULL,
    kind           text NOT NULL,               -- Credit | Debit
    amount         numeric(14,2) NOT NULL,
    channel        text NOT NULL,               -- UPI | IMPS | NEFT | POS | ECOM | ATM | NACH | CASH | INTEREST | CHARGE
    counterparty   text,                        -- person or merchant
    reference      text NOT NULL,
    status         text NOT NULL DEFAULT 'success',  -- success | failed | pending | reversed
    note           text                         -- e.g. "cash not dispensed: auto-reversal due by 5 working days"
);
CREATE INDEX ON corebank.account_txns (account_number, posted_at DESC);

CREATE TABLE corebank.account_charges (
    id             bigserial PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    charged_on     date NOT NULL,
    type           text NOT NULL,               -- amb_shortfall | debit_card_annual_fee | sms_alerts | emi_bounce
    amount         numeric(14,2) NOT NULL,
    reversed       boolean NOT NULL DEFAULT false
);

CREATE TABLE corebank.debit_cards (
    id             text PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    last4          text NOT NULL,
    variant        text NOT NULL,
    status         text NOT NULL DEFAULT 'active',  -- active | temp_blocked | blocked (permanent)
    atm            boolean NOT NULL,
    pos            boolean NOT NULL,
    ecom           boolean NOT NULL,
    contactless    boolean NOT NULL,
    international  boolean NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE corebank.credit_cards (
    id                   text PRIMARY KEY,
    cif                  text NOT NULL REFERENCES corebank.customers (cif),
    last4                text NOT NULL,
    variant              text NOT NULL,
    status               text NOT NULL DEFAULT 'active',  -- active | temp_blocked | blocked
    credit_limit         numeric(14,2) NOT NULL,
    outstanding          numeric(14,2) NOT NULL,     -- everything spent, billed or not
    total_due            numeric(14,2) NOT NULL,     -- last statement
    minimum_due          numeric(14,2) NOT NULL,
    statement_date       date,
    due_date             date,
    last_payment_on      date,
    last_payment_amount  numeric(14,2),
    last_payment_status  text,                       -- posted | processing
    previous_bill_paid   boolean,
    annual_fee           numeric(14,2),
    annual_fee_charged_on date,
    annual_fee_waiver_spend numeric(14,2),           -- spend that waives the fee (from the card's KB page)
    spend_last_year      numeric(14,2),
    lounge_per_quarter   int NOT NULL DEFAULT 0,
    lounge_used_quarter  int NOT NULL DEFAULT 0,
    international        boolean NOT NULL DEFAULT false,
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON corebank.credit_cards (cif);

CREATE TABLE corebank.card_txns (
    id            bigserial PRIMARY KEY,
    card_id       text NOT NULL REFERENCES corebank.credit_cards (id),
    posted_at     timestamptz NOT NULL,
    merchant      text NOT NULL,
    amount        numeric(14,2) NOT NULL,
    status        text NOT NULL DEFAULT 'success',
    international boolean NOT NULL DEFAULT false
);

CREATE TABLE corebank.fixed_deposits (
    number          text PRIMARY KEY,
    cif             text NOT NULL REFERENCES corebank.customers (cif),
    principal       numeric(14,2) NOT NULL,
    rate            numeric(5,2) NOT NULL,
    opened_on       date NOT NULL,
    tenure_months   int NOT NULL,
    maturity_date   date NOT NULL,
    maturity_amount numeric(14,2) NOT NULL,
    payout          text NOT NULL,              -- cumulative | quarterly
    auto_renew      boolean NOT NULL,
    status          text NOT NULL               -- active | matured | closed
);
CREATE INDEX ON corebank.fixed_deposits (cif);

CREATE TABLE corebank.loans (
    number          text PRIMARY KEY,
    cif             text NOT NULL REFERENCES corebank.customers (cif),
    type            text NOT NULL,              -- home | personal | vehicle | gold | business
    rate_type       text NOT NULL,              -- fixed | floating
    rate            numeric(5,2) NOT NULL,
    sanctioned      numeric(14,2) NOT NULL,
    outstanding     numeric(14,2) NOT NULL,
    emi             numeric(14,2) NOT NULL,
    next_due        date,
    emis_paid       int NOT NULL,
    emis_left       int NOT NULL,
    debit_account   text,
    status          text NOT NULL DEFAULT 'active'
);
CREATE INDEX ON corebank.loans (cif);

CREATE TABLE corebank.loan_emis (
    id          bigserial PRIMARY KEY,
    loan_number text NOT NULL REFERENCES corebank.loans (number),
    due_date    date NOT NULL,
    amount      numeric(14,2) NOT NULL,
    status      text NOT NULL,                  -- paid | bounced
    reason      text,                           -- bounced: why
    charge      numeric(14,2) NOT NULL DEFAULT 0
);

CREATE TABLE corebank.loan_rate_changes (
    id          bigserial PRIMARY KEY,
    loan_number text NOT NULL REFERENCES corebank.loans (number),
    changed_on  date NOT NULL,
    old_rate    numeric(5,2) NOT NULL,
    new_rate    numeric(5,2) NOT NULL,
    old_emi     numeric(14,2) NOT NULL,
    new_emi     numeric(14,2) NOT NULL
);

-- Every change made through the agent, as the bank's own system would log it.
CREATE TABLE corebank.card_actions (
    id         bigserial PRIMARY KEY,
    reference  text NOT NULL UNIQUE,
    cif        text NOT NULL,
    card_kind  text NOT NULL,                   -- debit | credit
    card_id    text NOT NULL,
    action     text NOT NULL,
    before     jsonb NOT NULL,
    after      jsonb NOT NULL,
    channel    text NOT NULL DEFAULT 'voice-agent',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ── the agent's own records ──

-- A question the agent asked and is waiting on: "Shall I block card ending 6712?", "Shall I connect you?".
ALTER TABLE sessions ADD COLUMN pending jsonb;

-- Complaints, disputes, service requests, callbacks and new-customer leads raised on a call.
-- (In production these go to the bank's CRM through the same port.)
CREATE TABLE tickets (
    id          text PRIMARY KEY,               -- AU-CMP-20261231-0001
    session_id  uuid REFERENCES sessions (id) ON DELETE SET NULL,
    cif         text,
    kind        text NOT NULL,                  -- complaint | dispute | service_request | callback | lead
    category    text NOT NULL,
    department  text NOT NULL,
    summary     text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'open',
    due_by      date,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tickets (session_id);

-- A call (or chat) handed to a person: waiting, then accepted by an agent at the desk.
CREATE TABLE handoffs (
    id           uuid PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    department   text NOT NULL,
    priority     int NOT NULL,                  -- 1 = critical (fraud) .. 3 = normal
    reason       text NOT NULL,
    note         jsonb NOT NULL,                -- the brief the person sees: who, verified?, what was asked/done, pending
    status       text NOT NULL DEFAULT 'waiting',  -- waiting | active | completed | abandoned | callback
    channel      text NOT NULL,                 -- call | chat
    created_at   timestamptz NOT NULL DEFAULT now(),
    accepted_at  timestamptz,
    ended_at     timestamptz,
    agent_name   text,
    disposition  text,
    agent_notes  text
);
CREATE INDEX ON handoffs (status, priority, created_at);

-- Messages between the person at the desk and a caller who was handed over on the chat channel.
CREATE TABLE handoff_messages (
    id         bigserial PRIMARY KEY,
    handoff_id uuid NOT NULL REFERENCES handoffs (id) ON DELETE CASCADE,
    sender     text NOT NULL,                   -- customer | agent
    text       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ════════ 006_call_state.sql ════════
-- Phase 4: one explicit state record per call (open requests, what was done or declined, focus, the list being read).
ALTER TABLE sessions ADD COLUMN state jsonb NOT NULL DEFAULT '{}';

-- ════════ 007_mockbank.sql ════════
-- Phase 4: the mock bank's write idempotency (a retry with the same key returns the original result).
CREATE TABLE corebank.idempotency (
    key          text PRIMARY KEY,
    request_hash text NOT NULL,
    status       int  NOT NULL,
    response     jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ════════ 008_doc_tags.sql ════════
-- Phase 4: document-level tags set or corrected by a person (doc_type, authority, effective_to, superseded_by, instruction_flag).
ALTER TABLE documents ADD COLUMN tags jsonb NOT NULL DEFAULT '{}';

-- ════════ 009_call_reviews.sql ════════
-- Phase 4: automatic review of every call after it ends (code checks always, an LLM judge on a risk-weighted slice).
CREATE TABLE call_reviews (
    session_id   uuid PRIMARY KEY REFERENCES sessions (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    mode         text NOT NULL,                 -- judged | code_only
    reason       text,                          -- why it was judged (or why it was not)
    checks       jsonb NOT NULL DEFAULT '[]',   -- [{name, ok, severity, detail}]: the free, deterministic checks
    judge        jsonb,                         -- dimension scores, evidence (verified), failure categories, recommended fix
    model        text,
    score        numeric(3,1),                  -- mean of the judge's dimension scores (1-5)
    critical     boolean NOT NULL DEFAULT false,
    needs_review boolean NOT NULL DEFAULT false,
    human        jsonb                          -- a person's labels for calibration: {scores, critical, note}
);
CREATE INDEX call_reviews_attention ON call_reviews (needs_review) WHERE needs_review;

-- ════════ 010_fix_items.sql ════════
-- Phase 4: the fix list. Every problem the post-call judge finds becomes a to-do a person can mark done (or won't fix).
-- Kept when its call is deleted (clean slate): the link to the call is cleared, the to-do stays.
CREATE TABLE fix_items (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    session_id    uuid REFERENCES sessions (id) ON DELETE SET NULL,
    turn          integer,                       -- the turn number shown on /calls ("Turn 7")
    category      text NOT NULL,                 -- failure label, e.g. poor_voice_response
    source        text NOT NULL,                 -- agent | bank_system | knowledge_gap | unclear_caller | insufficient_evidence
    fix_area      text NOT NULL,                 -- router | retrieval | answer_prompt | bank_templates | workflow | code | ...
    what_happened text NOT NULL,
    why_it_matters text NOT NULL DEFAULT '',
    fix           text NOT NULL,
    quote         text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'open',  -- open | done | wont_fix
    status_note   text NOT NULL DEFAULT '',
    status_at     timestamptz
);
CREATE INDEX fix_items_status ON fix_items (status, created_at DESC);

-- ════════ 011_emi_paid_on.sql ════════
-- The day an EMI was actually taken from the account (NULL while unpaid or bounced). The due date is not the debit
-- date: "when was my last EMI deducted?" is answered from this, never from the due date (PM review, 17 Nov 2026).
ALTER TABLE corebank.loan_emis ADD COLUMN IF NOT EXISTS paid_on date;

-- ════════ 012_benchmark.sql ════════
-- The benchmark to beat on the /bench page (services/benchmark.py): one row at most. Its numbers are a frozen snapshot,
-- so they do not change as more turns come in; a person replaces it ("keep as benchmark") when a setup should become
-- the new bar. Traces themselves carry their setup in data->>'setup_id' (no column, no index needed at demo volume).
CREATE TABLE benchmark_baseline (
    id         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    setup_id   text NOT NULL,
    label      text NOT NULL DEFAULT '',
    snapshot   jsonb NOT NULL,
    pinned_at  timestamptz NOT NULL DEFAULT now()
);

-- ════════ 013_call_records.sql ════════
-- Call record export (23 Nov 2026): one JSON record per call in the file store (local folder or S3 with Object Lock),
-- written after the call. Optionally, the turn rows are removed from Postgres some days later (CALL_ROWS_DAYS);
-- the sessions row stays as the call index. Only additions: nothing existing changes.

ALTER TABLE sessions
    ADD COLUMN record_key  text,          -- where the record is in the file store (call_records/...)
    ADD COLUMN exported_at timestamptz,   -- when the record was last written
    ADD COLUMN purged_at   timestamptz,   -- when the turn and event rows were removed (the record keeps them)
    ADD COLUMN summary     jsonb;         -- list-page counts kept after the rows are removed

CREATE INDEX sessions_export ON sessions (exported_at) WHERE purged_at IS NULL;
-- the export sweep finds recent activity by time (traces already has traces_created)
CREATE INDEX call_events_created ON call_events (created_at);
CREATE INDEX auth_events_created ON auth_events (created_at);

-- ════════ made by the app at startup (written here too, so the DBA sees every object) ════════
-- Mock bank: which day the demo data was seeded for (brain/app/adapters/mock_corebank.py).
CREATE TABLE IF NOT EXISTS corebank.seed_info (seeded_for date NOT NULL);
-- Vector search index for the embedding model BAAI/bge-m3, 1024 numbers (brain/app/db.py ensure_vector_index).
CREATE INDEX IF NOT EXISTS emb_hnsw_baai_bge_m3_1024 ON chunk_embeddings
    USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE model = 'BAAI/bge-m3';

INSERT INTO schema_migrations (name) VALUES
    ('001_init.sql'),
    ('002_auth.sql'),
    ('003_call_facts.sql'),
    ('004_audit.sql'),
    ('005_corebank.sql'),
    ('006_call_state.sql'),
    ('007_mockbank.sql'),
    ('008_doc_tags.sql'),
    ('009_call_reviews.sql'),
    ('010_fix_items.sql'),
    ('011_emi_paid_on.sql'),
    ('012_benchmark.sql'),
    ('013_call_records.sql')
ON CONFLICT (name) DO NOTHING;

RESET ROLE;
COMMIT;
