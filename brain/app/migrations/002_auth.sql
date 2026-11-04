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
