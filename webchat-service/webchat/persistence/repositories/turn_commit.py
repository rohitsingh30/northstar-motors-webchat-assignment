"""Atomic persistence boundary for successful grounded turns."""

from __future__ import annotations

import json
import uuid

from webchat.domain.conversation_state import ConversationState, PendingInteractionState
from webchat.orchestration.contracts.facts import ToolResultEnvelope

from ..database import Database
from .common import utc_now
from .interactions import ProtectedInteractionRepository
from .result_sets import ResultSetRepository


class StaleConversationStateError(RuntimeError):
    pass


class TurnCommitRepository:
    def __init__(self, database: Database):
        self.database = database
        self.result_sets = ResultSetRepository(database)
        self.interactions = ProtectedInteractionRepository(database)

    def commit_success(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        expected_state_version: int,
        state: ConversationState,
        messages: list[dict],
        result_sets: list[tuple[str, ToolResultEnvelope]] | None = None,
        interaction: PendingInteractionState | None = None,
        interaction_transition: tuple[str, str, str] | None = None,
        client_actions: list[dict] | None = None,
    ) -> list[str]:
        if state.stateVersion <= expected_state_version:
            raise ValueError("successful state must advance its version")
        message_ids: list[str] = []
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT state_version FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if not current or current["state_version"] != expected_state_version:
                raise StaleConversationStateError("conversation state changed before commit")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            for offset, message in enumerate(messages, start=1):
                message_id = str(message.get("id") or uuid.uuid4())
                connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, turn_id, sequence, role, text, view_type, "
                "view_payload_json, created_at, interaction_json, purpose, segments_json, "
                "blocks_json) VALUES (?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        conversation_id,
                        turn_id,
                        sequence + offset,
                        str(message["text"]),
                        message.get("viewType"),
                        json.dumps(message.get("view"), separators=(",", ":"))
                        if message.get("view") is not None
                        else None,
                        utc_now(),
                        message.get("interactionJson"),
                        message.get("purpose"),
                        json.dumps(message.get("segments"), separators=(",", ":"))
                        if message.get("segments") is not None
                        else None,
                        json.dumps(message.get("blocks"), separators=(",", ":"))
                        if message.get("blocks") is not None
                        else None,
                    ),
                )
                message_ids.append(message_id)
            for kind, result in result_sets or []:
                self.result_sets.add(
                    conversation_id,
                    turn_id,
                    kind,
                    result,
                    connection=connection,
                )
            if interaction:
                self.interactions.create(
                    conversation_id,
                    interaction,
                    superseding_turn_id=turn_id,
                    connection=connection,
                )
            if interaction_transition:
                interaction_id, expected_status, target_status = interaction_transition
                if not self.interactions.transition(
                    conversation_id,
                    interaction_id,
                    expected_status=expected_status,
                    status=target_status,
                    connection=connection,
                ):
                    raise StaleConversationStateError("protected interaction is no longer active")
            updated = connection.execute(
                "UPDATE conversations SET state_version = ?, state_json = ?, "
                "workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND state_version = ? AND status = 'active'",
                (
                    state.stateVersion,
                    state.model_dump_json(),
                    json.dumps(state.agentWorkflow, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                    expected_state_version,
                ),
            ).rowcount
            if updated != 1:
                raise StaleConversationStateError("conversation state changed before commit")
            connection.execute(
                "UPDATE turns SET status = 'completed', error_category = NULL, completed_at = ?, "
                "client_actions_json = ? "
                "WHERE id = ? AND conversation_id = ?",
                (
                    utc_now(),
                    json.dumps(client_actions or [], separators=(",", ":")),
                    turn_id,
                    conversation_id,
                ),
            )
        return message_ids

    def supersede_interaction(
        self,
        *,
        conversation_id: str,
        interaction_id: str,
        turn_id: str,
        expected_state_version: int,
        state: ConversationState,
    ) -> None:
        if state.stateVersion <= expected_state_version:
            raise ValueError("supersession state must advance its version")
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT state_version FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if not current or current["state_version"] != expected_state_version:
                raise StaleConversationStateError("conversation state changed before supersession")
            if not self.interactions.transition(
                conversation_id,
                interaction_id,
                expected_status="awaiting_confirmation",
                status="superseded",
                connection=connection,
            ):
                raise StaleConversationStateError("protected interaction is no longer active")
            changed = connection.execute(
                "UPDATE protected_interactions SET superseded_at_turn_id = ?, updated_at = ? "
                "WHERE id = ? AND conversation_id = ? AND status = 'superseded'",
                (turn_id, utc_now(), interaction_id, conversation_id),
            ).rowcount
            updated = connection.execute(
                "UPDATE conversations SET state_version = ?, state_json = ?, "
                "workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND state_version = ? AND status = 'active'",
                (
                    state.stateVersion,
                    state.model_dump_json(),
                    json.dumps(state.agentWorkflow, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                    expected_state_version,
                ),
            ).rowcount
            if changed != 1 or updated != 1:
                raise StaleConversationStateError("interaction supersession could not be committed")

    def commit_protected_prompt(
        self,
        *,
        conversation_id: str,
        message_id: str,
        text: str,
        view_type: str,
        view: dict,
        interaction_json: str,
        purpose: str,
        interaction: PendingInteractionState,
        expected_state_version: int,
        state: ConversationState,
        superseded_interaction: PendingInteractionState | None = None,
    ) -> None:
        """Atomically persist a private-endpoint review, its interaction, and state."""
        if state.stateVersion <= expected_state_version:
            raise ValueError("protected prompt state must advance its version")
        if interaction.originatingMessageId != message_id:
            raise ValueError("protected interaction must reference its prompt message")
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT state_version FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if not current or current["state_version"] != expected_state_version:
                raise StaleConversationStateError("conversation state changed before protected prompt")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            if (
                superseded_interaction is not None
                and superseded_interaction.interactionId != interaction.interactionId
            ):
                superseded_payload = {
                    "version": 1,
                    "draftId": superseded_interaction.activeDraftId,
                    "kind": superseded_interaction.workflowKind,
                    "status": "superseded",
                }
                connection.execute(
                    "UPDATE messages SET text = ?, view_type = 'superseded_confirmation', "
                    "view_payload_json = ?, interaction_json = NULL "
                    "WHERE id = ? AND conversation_id = ? AND view_type = 'confirmation'",
                    (
                        "Superseded — details changed.",
                        json.dumps(superseded_payload, separators=(",", ":")),
                        superseded_interaction.originatingMessageId,
                        conversation_id,
                    ),
                )
            connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, turn_id, sequence, role, text, view_type, "
                "view_payload_json, created_at, interaction_json, purpose, segments_json) "
                "VALUES (?, ?, NULL, ?, 'assistant', ?, ?, ?, ?, ?, ?, NULL)",
                (
                    message_id,
                    conversation_id,
                    sequence,
                    text,
                    view_type,
                    json.dumps(view, separators=(",", ":")),
                    utc_now(),
                    interaction_json,
                    purpose,
                ),
            )
            self.interactions.create(
                conversation_id,
                interaction,
                superseding_turn_id=message_id,
                connection=connection,
            )
            updated = connection.execute(
                "UPDATE conversations SET state_version = ?, state_json = ?, "
                "workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND state_version = ? AND status = 'active'",
                (
                    state.stateVersion,
                    state.model_dump_json(),
                    json.dumps(state.agentWorkflow, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                    expected_state_version,
                ),
            ).rowcount
            if updated != 1:
                raise StaleConversationStateError("protected prompt could not be committed")

    def transition_interaction_state(
        self,
        *,
        conversation_id: str,
        interaction_id: str,
        expected_status: str,
        status: str,
        expected_state_version: int,
        state: ConversationState,
    ) -> None:
        """Atomically transition the protected-interaction row and state document."""
        if state.stateVersion <= expected_state_version:
            raise ValueError("interaction transition state must advance its version")
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT state_version FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if not current or current["state_version"] != expected_state_version:
                raise StaleConversationStateError("conversation state changed before interaction")
            if not self.interactions.transition(
                conversation_id,
                interaction_id,
                expected_status=expected_status,
                status=status,
                connection=connection,
            ):
                raise StaleConversationStateError("protected interaction is no longer active")
            updated = connection.execute(
                "UPDATE conversations SET state_version = ?, state_json = ?, "
                "workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND state_version = ? AND status = 'active'",
                (
                    state.stateVersion,
                    state.model_dump_json(),
                    json.dumps(state.agentWorkflow, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                    expected_state_version,
                ),
            ).rowcount
            if updated != 1:
                raise StaleConversationStateError("interaction transition could not be committed")
