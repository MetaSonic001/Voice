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
