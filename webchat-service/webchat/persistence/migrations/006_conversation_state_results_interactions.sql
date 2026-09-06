ALTER TABLE conversations ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE conversations ADD COLUMN state_json TEXT NOT NULL DEFAULT '{"schemaVersion":1,"stateVersion":0}';
ALTER TABLE messages ADD COLUMN purpose TEXT;
ALTER TABLE turns ADD COLUMN client_actions_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE trusted_result_sets (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX trusted_result_sets_conversation_created
ON trusted_result_sets(conversation_id, created_at DESC);

CREATE TABLE protected_interactions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    originating_message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'awaiting_confirmation', 'confirmed', 'cancelled', 'superseded',
        'executed', 'unavailable', 'failed'
    )),
    created_at_state_version INTEGER NOT NULL,
    trusted_entity_json TEXT,
    active_draft_id TEXT,
    workflow_kind TEXT,
    superseded_by_interaction_id TEXT,
    superseded_at_turn_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX protected_interactions_conversation_created
ON protected_interactions(conversation_id, created_at DESC);

CREATE UNIQUE INDEX protected_interactions_one_active
ON protected_interactions(conversation_id)
WHERE status = 'awaiting_confirmation';
