CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    token_hash BLOB NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'deleted', 'expired')),
    summary TEXT,
    last_page_context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    client_message_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    correlation_id TEXT NOT NULL UNIQUE,
    error_category TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (conversation_id, client_message_id)
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text TEXT NOT NULL,
    view_type TEXT,
    view_payload_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, sequence)
);

CREATE INDEX messages_conversation_sequence
    ON messages(conversation_id, sequence);
