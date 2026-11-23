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
