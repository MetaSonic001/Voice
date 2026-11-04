-- What the caller has told us that matters for answers (senior citizen, product, tenure, amount, account),
-- kept for the whole call - unlike the recent-turns window, it is never forgotten.
ALTER TABLE sessions ADD COLUMN facts jsonb NOT NULL DEFAULT '{}';
