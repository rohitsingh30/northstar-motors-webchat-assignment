"""Conversation message and closed-view persistence."""

from __future__ import annotations

import json
import uuid

from webchat.domain.models import Message

from ..database import Database
from .common import utc_now


class MessageRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _from_row(row) -> Message:
        return Message(**dict(row))

    def list(self, conversation_id: str) -> list[Message]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence",
                (conversation_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_turn(self, turn_id: str) -> list[Message]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE turn_id = ? ORDER BY sequence", (turn_id,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def add(
        self,
        conversation_id: str,
        role: str,
        text: str,
        turn_id: str | None,
        view_type: str | None = None,
        view_payload_json: str | None = None,
        interaction_json: str | None = None,
    ) -> Message:
        message_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, turn_id, sequence, role, text, view_type, "
                "view_payload_json, created_at, interaction_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    turn_id,
                    sequence,
                    role,
                    text,
                    view_type,
                    view_payload_json,
                    created_at,
                    interaction_json,
                ),
            )
        return Message(
            message_id,
            conversation_id,
            turn_id,
            sequence,
            role,
            text,
            view_type,
            view_payload_json,
            created_at,
            interaction_json,
        )

    def replace_draft_with_receipt(
        self,
        conversation_id: str,
        draft_id: str,
        text: str,
        receipt: dict,
    ) -> bool:
        """Replace the persisted draft card so restored chats cannot revive completed forms."""
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id, view_payload_json FROM messages "
                "WHERE conversation_id = ? AND view_type IN ('draft', 'confirmation') "
                "ORDER BY sequence DESC",
                (conversation_id,),
            ).fetchall()
            fallback_row = None
            for row in rows:
                try:
                    payload = json.loads(row["view_payload_json"] or "{}")
                except (TypeError, ValueError):
                    continue
                if payload.get("draftId") == draft_id:
                    fallback_row = row
                    break
                if fallback_row is None and payload.get("kind") == receipt.get("kind"):
                    fallback_row = row
            if fallback_row is not None:
                connection.execute(
                    "UPDATE messages SET text = ?, view_type = 'receipt', view_payload_json = ? "
                    "WHERE id = ?",
                    (text, json.dumps(receipt, separators=(",", ":")), fallback_row["id"]),
                )
                return True
        return False
