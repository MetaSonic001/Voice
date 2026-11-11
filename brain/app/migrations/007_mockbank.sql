-- Phase 4: the mock bank's write idempotency (a retry with the same key returns the original result).
CREATE TABLE corebank.idempotency (
    key          text PRIMARY KEY,
    request_hash text NOT NULL,
    status       int  NOT NULL,
    response     jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
