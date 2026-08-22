ALTER TABLE conversations ADD COLUMN session_hash BLOB;
UPDATE conversations SET session_hash = token_hash WHERE session_hash IS NULL;
CREATE INDEX conversations_session_updated
ON conversations(session_hash, status, updated_at DESC);
