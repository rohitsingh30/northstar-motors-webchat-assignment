CREATE TABLE workflow_drafts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'collecting', 'awaiting_confirmation', 'executing', 'succeeded', 'failed', 'cancelled', 'expired'
    )),
    fields_json TEXT NOT NULL,
    material_hash TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX workflow_drafts_conversation ON workflow_drafts(conversation_id, updated_at);

CREATE TABLE operation_attempts (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES workflow_drafts(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    client_action_id TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('prepared', 'sent', 'succeeded', 'failed', 'unknown')),
    result_json TEXT,
    error_code TEXT,
    retryable INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (conversation_id, client_action_id)
);

CREATE TABLE verified_booking_grants (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    booking_record_id TEXT NOT NULL,
    booking_reference TEXT NOT NULL,
    booking_snapshot_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
