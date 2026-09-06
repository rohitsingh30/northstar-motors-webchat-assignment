"""Server-owned lifecycle persistence for protected interactions."""

from __future__ import annotations

import json

from webchat.domain.conversation_state import PendingInteractionState

from ..database import Database
from .common import utc_now


class ProtectedInteractionRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _from_row(row) -> PendingInteractionState:
        trusted = json.loads(row["trusted_entity_json"]) if row["trusted_entity_json"] else None
        return PendingInteractionState(
            interactionId=row["id"],
            kind=row["kind"],
            trustedEntity=trusted,
            originatingMessageId=row["originating_message_id"],
            createdAtStateVersion=row["created_at_state_version"],
            status=row["status"],
            activeDraftId=row["active_draft_id"],
            workflowKind=row["workflow_kind"],
            supersededByInteractionId=row["superseded_by_interaction_id"],
            supersededAtTurnId=row["superseded_at_turn_id"],
        )

    def latest(self, conversation_id: str) -> PendingInteractionState | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM protected_interactions WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def active(self, conversation_id: str) -> PendingInteractionState | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM protected_interactions "
                "WHERE conversation_id = ? AND status = 'awaiting_confirmation' "
                "ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def create(
        self,
        conversation_id: str,
        interaction: PendingInteractionState,
        *,
        superseding_turn_id: str,
        connection=None,
    ) -> None:
        if interaction.status != "awaiting_confirmation":
            raise ValueError("new protected interactions must await confirmation")

        def execute(active_connection) -> None:
            active_connection.execute(
                "UPDATE protected_interactions SET status = 'superseded', "
                "superseded_by_interaction_id = ?, superseded_at_turn_id = ?, updated_at = ? "
                "WHERE conversation_id = ? AND status = 'awaiting_confirmation'",
                (
                    interaction.interactionId,
                    superseding_turn_id,
                    utc_now(),
                    conversation_id,
                ),
            )
            active_connection.execute(
                "INSERT INTO protected_interactions "
                "(id, conversation_id, originating_message_id, kind, status, "
                "created_at_state_version, trusted_entity_json, active_draft_id, workflow_kind, "
                "superseded_by_interaction_id, superseded_at_turn_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    interaction.interactionId,
                    conversation_id,
                    interaction.originatingMessageId,
                    interaction.kind,
                    interaction.status,
                    interaction.createdAtStateVersion,
                    interaction.trustedEntity.model_dump_json()
                    if interaction.trustedEntity
                    else None,
                    interaction.activeDraftId,
                    interaction.workflowKind,
                    interaction.supersededByInteractionId,
                    interaction.supersededAtTurnId,
                    utc_now(),
                    utc_now(),
                ),
            )

        if connection is not None:
            execute(connection)
            return
        with self.database.transaction() as owned:
            execute(owned)

    def transition(
        self,
        conversation_id: str,
        interaction_id: str,
        *,
        expected_status: str,
        status: str,
        connection=None,
    ) -> bool:
        def execute(active_connection) -> bool:
            changed = active_connection.execute(
                "UPDATE protected_interactions SET status = ?, updated_at = ? "
                "WHERE id = ? AND conversation_id = ? AND status = ?",
                (status, utc_now(), interaction_id, conversation_id, expected_status),
            ).rowcount
            return changed == 1

        if connection is not None:
            return execute(connection)
        with self.database.transaction() as owned:
            return execute(owned)
