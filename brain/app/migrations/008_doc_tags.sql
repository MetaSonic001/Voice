-- Phase 4: document-level tags set or corrected by a person (doc_type, authority, effective_to, superseded_by, instruction_flag).
ALTER TABLE documents ADD COLUMN tags jsonb NOT NULL DEFAULT '{}';
