ALTER TABLE conversations ADD COLUMN initial_page_context_json TEXT;
UPDATE conversations
SET initial_page_context_json = last_page_context_json
WHERE initial_page_context_json IS NULL;
