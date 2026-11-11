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
