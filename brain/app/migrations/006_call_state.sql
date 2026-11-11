-- Phase 4: one explicit state record per call (open requests, what was done or declined, focus, the list being read).
ALTER TABLE sessions ADD COLUMN state jsonb NOT NULL DEFAULT '{}';
