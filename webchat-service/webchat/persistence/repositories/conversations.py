"""Conversation session, page context, and workflow-state persistence."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from ..database import Database
from .common import token_hash, utc_now


class ConversationRepository:
    def __init__(self, database: Database):
        self.database = database

    def create(self, token: str, page_context: dict[str, object], retention_days: int) -> dict:
        conversation_id = str(uuid.uuid4())
        created_at = utc_now()
        expires_at = (
            (datetime.now(UTC) + timedelta(days=retention_days))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO conversations "
                "(id, token_hash, session_hash, status, initial_page_context_json, "
                "last_page_context_json, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    uuid.uuid4().bytes,
                    token_hash(token),
                    json.dumps(page_context, separators=(",", ":")),
                    json.dumps(page_context, separators=(",", ":")),
                    created_at,
                    created_at,
                    expires_at,
                ),
            )
        return {"id": conversation_id, "created_at": created_at}

    def authorize(self, conversation_id: str, token: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT token_hash, session_hash, status, expires_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return bool(
            row
            and row["status"] == "active"
            and row["expires_at"] > utc_now()
            and (row["session_hash"] == token_hash(token) or row["token_hash"] == token_hash(token))
        )

    def has_session(self, token: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversations WHERE session_hash = ? AND expires_at > ? LIMIT 1",
                (token_hash(token), utc_now()),
            ).fetchone()
        return bool(row)

    def list_for_session(self, token: str) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id, c.created_at, c.updated_at, COUNT(m.id) AS message_count, "
                "COALESCE((SELECT text FROM messages first_message "
                "WHERE first_message.conversation_id = c.id AND first_message.role = 'user' "
                "ORDER BY first_message.sequence LIMIT 1), 'New conversation') AS title "
                "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
                "WHERE c.session_hash = ? AND c.status = 'active' AND c.expires_at > ? "
                "GROUP BY c.id ORDER BY c.updated_at DESC, c.rowid DESC",
                (token_hash(token), utc_now()),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, conversation_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET status = 'deleted', token_hash = ?, updated_at = ? "
                "WHERE id = ?",
                (uuid.uuid4().bytes, utc_now(), conversation_id),
            )

    def update_context(self, conversation_id: str, page_context: dict[str, object]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET last_page_context_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(page_context, separators=(",", ":")), utc_now(), conversation_id),
            )

    def get_contexts(self, conversation_id: str) -> dict[str, dict[str, object]]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT initial_page_context_json, last_page_context_json "
                "FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
        if not row:
            return {"initial": {}, "current": {}}
        current = json.loads(row["last_page_context_json"])
        initial = json.loads(row["initial_page_context_json"] or row["last_page_context_json"])
        return {"initial": initial, "current": current}

    def get_workflow_state(self, conversation_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT workflow_state_json FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
        if not row or not row["workflow_state_json"]:
            return {}
        value = json.loads(row["workflow_state_json"])
        return value if isinstance(value, dict) else {}

    def update_workflow_state(
        self, conversation_id: str, workflow_state: dict[str, object]
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND status = 'active'",
                (
                    json.dumps(workflow_state, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                ),
            )

    def expire_old(self) -> int:
        with self.database.transaction() as connection:
            changed = connection.execute(
                "UPDATE conversations SET status = 'expired', token_hash = randomblob(32), updated_at = ? "
                "WHERE status = 'active' AND expires_at <= ?",
                (utc_now(), utc_now()),
            ).rowcount
        return changed
