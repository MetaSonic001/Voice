-- The journey catalogue's releases (services/releases.py, ADR 0032). brain/content/ in git is the base; a release holds
-- a product manager's changes on top of it. One draft at a time; published releases are never changed again.
CREATE TABLE content_releases (
    id           serial PRIMARY KEY,
    status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    body         jsonb NOT NULL DEFAULT '{}',   -- {messages, pending, auto, prompts, settings, tree}: changes on top of git
    rev          int NOT NULL DEFAULT 0,        -- bumped on every save: a save from an older screen is refused
    notes        text NOT NULL DEFAULT '',      -- what changed and why (written when publishing)
    author       text NOT NULL DEFAULT '',      -- who saved last / who published
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE UNIQUE INDEX content_one_draft ON content_releases ((status)) WHERE status = 'draft';

-- What is live now: the main release for most calls and, during a canary, a second release for a share of calls.
CREATE TABLE content_rollout (
    id           int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    stable_id    int REFERENCES content_releases (id),  -- NULL: brain/content as shipped
    canary_id    int REFERENCES content_releases (id),
    canary_pct   int NOT NULL DEFAULT 0 CHECK (canary_pct BETWEEN 0 AND 50),
    canary_since timestamptz,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    changed_by   text NOT NULL DEFAULT ''
);
INSERT INTO content_rollout (id) VALUES (1);

-- Every change to what is live: publish, promote, stop, roll back.
CREATE TABLE content_rollout_log (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    action     text NOT NULL,
    stable_id  int,
    canary_id  int,
    canary_pct int NOT NULL DEFAULT 0,
    who        text NOT NULL DEFAULT '',
    notes      text NOT NULL DEFAULT ''
);
