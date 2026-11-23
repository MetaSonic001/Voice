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
