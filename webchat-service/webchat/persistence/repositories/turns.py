"""Idempotent customer-turn lifecycle persistence."""

from __future__ import annotations

import uuid

from webchat.domain.models import Turn

from ..database import Database
from .common import utc_now


class TurnRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _from_row(row) -> Turn:
        return Turn(**dict(row))

    def get_by_client_id(self, conversation_id: str, client_message_id: str) -> Turn | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE conversation_id = ? AND client_message_id = ?",
                (conversation_id, client_message_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def create(
        self,
        conversation_id: str,
        client_message_id: str,
        request_fingerprint: str,
    ) -> Turn:
        turn = Turn(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            status="running",
            correlation_id=str(uuid.uuid4()),
            error_category=None,
            started_at=utc_now(),
            completed_at=None,
            request_fingerprint=request_fingerprint,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO turns "
                "(id, conversation_id, client_message_id, status, correlation_id, started_at, "
                "request_fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.id,
                    turn.conversation_id,
                    turn.client_message_id,
                    turn.status,
                    turn.correlation_id,
                    turn.started_at,
                    turn.request_fingerprint,
                ),
            )
        return turn

    def restart(self, turn_id: str) -> None:
        """Retry the identical failed request without creating a second user message."""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = 'running', error_category = NULL, "
                "completed_at = NULL, started_at = ? WHERE id = ? AND status = 'failed'",
                (utc_now(), turn_id),
            )

    def count_started_since(self, started_at: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM turns WHERE started_at >= ?",
                (started_at,),
            ).fetchone()
        return int(row["count"])

    def finish(self, turn_id: str, status: str, error_category: str | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = ?, error_category = ?, completed_at = ? WHERE id = ?",
                (status, error_category, utc_now(), turn_id),
            )
