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
