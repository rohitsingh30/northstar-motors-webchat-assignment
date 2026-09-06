from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import replace

from webchat.domain.capabilities import CapabilityRegistry
from webchat.domain.conversation_state import (
    ConversationStateReducer,
    PendingInteractionState,
    StateEvent,
    TrustedEntity,
)
from webchat.domain.field_guidance import field_prompt
from webchat.domain.interactions import (
    PendingInteraction,
    composition_continuation,
    interaction_from_view,
    preceding_interaction,
)
from webchat.domain.protected_interactions import explicit_decision, resolve_latest
from webchat.domain.vehicle_safety import immediate_vehicle_safety_guidance
from webchat.integrations.contracts import (
    LlmProvider,
    PlanningValidationError,
    ProviderUnavailableError,
)
from webchat.integrations.dealership import DealershipError
from webchat.orchestration.appointments import appointment_collection_payload
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.context import (
    ConversationHistoryBuilder,
    TurnContext,
)
from webchat.orchestration.contracts.semantics import (
    REFERENCE_FIELD_NAMESPACES,
    TurnUnderstanding,
)
from webchat.orchestration.fact_normalization import FactNormalizer
from webchat.orchestration.grounding import (
    GroundingValidator,
    has_incomplete_sentence_ending,
    normalize_explicit_card_owned_messages,
    workflow_continuation_suggestions,
)
from webchat.orchestration.presentation.clarifications import (
    clarification_suggestion_result,
    reference_clarification_presentation,
)
from webchat.orchestration.presentation.suggestions import (
    workshop_booking_status_prompt,
    workshop_booking_status_suggestions,
)
from webchat.orchestration.provider_loop import (
    ProviderToolLoop,
    TurnReferences,
)
from webchat.orchestration.references import current_choice_reference_context
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.actions import StructuredActionHandler
from webchat.orchestration.tools.result import ToolResult
from webchat.persistence.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnCommitRepository,
    TurnRepository,
)

logger = logging.getLogger(__name__)

_PLANNING_CLARIFICATION = (
    "I’m not completely sure how to handle that request. Could you rephrase it or tell me "
    "the outcome you want?"
)

_QUESTION_PURPOSES = {"clarification", "follow_up", "workflow_prompt", "next_step"}
_OPEN_CONTINUATION = "What else can I help you with?"
_OPTION_CONTINUATION = "Which of these options would you like help with next?"
_TERMINAL_FAREWELL = "Goodbye. Thanks for contacting Northstar Motors."
_UNCLEAR_RESPONSE_FALLBACK = "I couldn’t present that response clearly."
_GROUNDING_ATTEMPTS = 2

_CARD_REFERENCE_NAMESPACES = {
    "vehicle_preview": "vehicle",
    "vehicle_comparison": "vehicle",
    "offer": "offer",
    "dealership": "dealership",
    "service": "service",
    "booking": "booking",
}


def _card_owner_message_index(messages, cards) -> int:
    """Place visual evidence before a trailing question instead of inside it."""

    if not messages:
        return 0
    if any(card.type == "vehicle_comparison" for card in cards):
        comparison_index = next(
            (index for index, message in enumerate(messages) if message.purpose == "comparison"),
            None,
        )
        if comparison_index is not None:
            return comparison_index
    return next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].purpose not in _QUESTION_PURPOSES
        ),
        len(messages) - 1,
    )


def _dialogue_question_disposition(
    state,
    workflow_state: dict,
    understanding: TurnUnderstanding | None,
    normalized_results: list,
    state_reducer: WorkflowStateReducer,
) -> str:
    """Compile question lifecycle from validated execution, not model wording alone."""

    if state.dialogue.activeQuestion is None:
        return "retain"

    current_workflow = str(state.agentWorkflow.get("activeWorkflow") or "")
    next_workflow = str(workflow_state.get("activeWorkflow") or "")
    if current_workflow != next_workflow:
        return "close"

    effects = [
        state_reducer.turn_effect(state.agentWorkflow, result.tool)
        for result in normalized_results
    ]
    if effects and all(effect == "interrupt" for effect in effects):
        if _results_refresh_active_reference_question(
            state.dialogue.activeQuestion,
            workflow_state,
            normalized_results,
        ):
            return "refresh"
        return "preserve"
    if _results_refresh_active_reference_question(
        state.dialogue.activeQuestion,
        workflow_state,
        normalized_results,
    ):
        return "refresh"
    if understanding is not None and understanding.dialogueAct == "answer_open_question":
        return "consume"
    if (
        not effects
        and understanding is not None
        and understanding.dialogueAct == "interrupt_with_information_request"
    ):
        return "preserve"
    return "retain"


def _candidate_references_for_namespace(results: list, namespace: str) -> list[str]:
    """Project one bounded, ordered candidate set from normalized trusted results."""

    declared = [
        reference
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.choiceMode is not None
        for reference in result.responseObligation.candidateReferences
        if reference.startswith(f"{namespace}:")
    ]
    discovered = [
        entity.reference
        for result in results
        for entity in result.entities
        if entity.type == namespace
    ]
    return list(dict.fromkeys(declared or discovered))[:12]


def _result_has_candidate_surface(result, namespace: str) -> bool:
    """Return whether a result visibly presents choices in one reference namespace.

    Entity facts alone do not replace an open choice: a single detail read may mention the same
    entity while merely interrupting the workflow. A trusted choice collection or a list-backed
    card is the evidence that the customer-facing candidate surface itself changed.
    """

    if any(
        collection.presentation.purpose in {"choice", "clarification"}
        for collection in result.availableCollections
    ):
        return True
    return any(
        _CARD_REFERENCE_NAMESPACES.get(card.type) == namespace
        and isinstance(card.data.get("items"), list)
        and bool(card.data["items"])
        for card in result.availableCards
    )


def _results_refresh_active_reference_question(
    question,
    workflow_state: dict,
    normalized_results: list,
) -> bool:
    """Recognize a new visible candidate set for the field the workflow is awaiting."""

    if question is None or question.kind != "reference_choice":
        return False
    constraints = dict(workflow_state.get("constraints") or {})
    fields = [
        str(field)
        for field in (*question.expectedFields, *constraints.get("missingPublicFields", []))
        if str(field)
    ]
    namespace = next(
        (
            REFERENCE_FIELD_NAMESPACES[field]
            for field in fields
            if field in REFERENCE_FIELD_NAMESPACES
        ),
        None,
    ) or _single_reference_namespace(question.candidateReferences)
    if namespace is None:
        return False
    candidates = _candidate_references_for_namespace(normalized_results, namespace)
    return bool(candidates) and any(
        _result_has_candidate_surface(result, namespace) for result in normalized_results
    )


def _single_reference_namespace(references: list[str]) -> str | None:
    namespaces = {
        reference.split(":", 1)[0]
        for reference in references
        if isinstance(reference, str) and ":" in reference
    }
    return namespaces.pop() if len(namespaces) == 1 else None


class Orchestrator:
    """Own the turn lifecycle across AI planning, policy, tools, and grounded composition."""

    def __init__(
        self,
        messages: MessageRepository,
        turns: TurnRepository,
        provider: LlmProvider,
        tools=None,
        conversations: ConversationRepository | None = None,
        *,
        timeout_seconds: float,
        workflows=None,
        workflow_repository=None,
        protected_interactions=None,
        turn_commit: TurnCommitRepository | None = None,
    ):
        self.messages = messages
        self.turns = turns
        self.provider = provider
        self.conversations = conversations
        self.timeout_seconds = timeout_seconds
        self.workflows = workflows
        self.workflow_repository = workflow_repository
        self.protected_interactions = protected_interactions
        self.turn_commit = turn_commit
        self.conversation_state_reducer = ConversationStateReducer()
        self.grounding = GroundingValidator()
        self.fact_normalizer = FactNormalizer()
        self.state_reducer = WorkflowStateReducer()
        self.history_builder = ConversationHistoryBuilder(conversations)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tools = None
        self.tools = tools

    @property
    def tools(self):
        return self._tools

    @tools.setter
    def tools(self, tools) -> None:
        """Keep tool-dependent collaborators aligned when tests replace the executor."""
        if tools is not None and not isinstance(tools, UnifiedToolCatalog):
            tools = UnifiedToolCatalog(tools)
        self._tools = tools
        self.action_handler = StructuredActionHandler(tools) if tools is not None else None
        self.provider_loop = ProviderToolLoop(
            self.provider,
            tools,
            self.state_reducer,
            self.timeout_seconds,
        )

    async def run(
        self,
        conversation_id: str,
        client_message_id: str,
        text: str,
        action: dict | None = None,
    ) -> tuple[str, str, list]:
        async with self._locks[conversation_id]:
            request_fingerprint = _request_fingerprint(text, action)
            existing = self.turns.get_by_client_id(conversation_id, client_message_id)
            if existing:
                if (
                    existing.status == "failed"
                    and existing.request_fingerprint == request_fingerprint
                ):
                    self.turns.restart(existing.id)
                    return await self._execute_with_failure_boundary(
                        conversation_id,
                        existing.id,
                        client_message_id,
                        action,
                    )
                return existing.id, existing.status, self.messages.list_for_turn(existing.id)

            turn = self.turns.create(conversation_id, client_message_id, request_fingerprint)
            self.messages.add(conversation_id, "user", text, turn.id)
            return await self._execute_with_failure_boundary(
                conversation_id,
                turn.id,
                client_message_id,
                action,
            )

    async def _execute_with_failure_boundary(
        self,
        conversation_id: str,
        turn_id: str,
        client_message_id: str,
        action: dict | None,
    ) -> tuple[str, str, list]:
        """Apply one failure contract to new turns and idempotent retries."""

        try:
            return await self._execute_turn(conversation_id, turn_id, client_message_id, action)
        except TimeoutError:
            self.turns.finish(turn_id, "failed", "LLM_TIMEOUT")
        except PlanningValidationError as error:
            logger.warning(
                "planning validation exhausted; completing with clarification",
                extra={"context": {"error_type": type(error).__name__}},
            )
            recovery = self._finish_active_workflow_recovery(conversation_id, turn_id)
            if recovery is not None:
                return recovery
            return self._finish_text_turn(conversation_id, turn_id, _PLANNING_CLARIFICATION)
        except ProviderUnavailableError:
            self.turns.finish(turn_id, "failed", "LLM_PROVIDER_UNAVAILABLE")
        # This is the provider/tool failure boundary; private exception details never reach users.
        except Exception as error:
            logger.exception("conversation turn failed: %s", type(error).__name__)
            self.turns.finish(turn_id, "failed", "LLM_INVALID_RESPONSE")
        return turn_id, "failed", self.messages.list_for_turn(turn_id)

    def _finish_active_workflow_recovery(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> tuple[str, str, list] | None:
        """Keep a recognized workflow actionable when hosted planning exhausts repair."""

        if self.conversations is None:
            return None
        state = self.conversations.get_state(conversation_id)
        workflow = dict(state.agentWorkflow)
        active_workflow = str(workflow.get("activeWorkflow") or "")
        if not active_workflow:
            return None
        conversation_messages = self.messages.list(conversation_id)
        question = state.dialogue.activeQuestion
        if question is not None:
            source = next(
                (
                    message
                    for message in conversation_messages
                    if message.id == question.originatingMessageId
                ),
                None,
            )
            view_type, view_payload = _persisted_message_view(source)
            self.messages.add(
                conversation_id,
                "assistant",
                question.prompt,
                turn_id,
                view_type,
                view_payload,
                purpose="workflow_prompt",
            )
            self.turns.finish(turn_id, "completed")
            return turn_id, "completed", self.messages.list_for_turn(turn_id)

        constraints = dict(workflow.get("constraints") or {})
        missing = [str(field) for field in constraints.get("missingPublicFields") or () if field]
        if not missing or self.turn_commit is None:
            return None
        field = missing[0]
        expected_fields = [
            str(value)
            for value in constraints.get("acceptedInputFields") or missing
            if value
        ][:8]
        namespace = REFERENCE_FIELD_NAMESPACES.get(field)
        displayed_choices = current_choice_reference_context(
            conversation_messages,
            choice_field=field,
        )
        candidate_references = (
            [
                str(item["entityReference"])
                for item in displayed_choices
                if isinstance(item, dict)
                and isinstance(item.get("entityReference"), str)
                and str(item["entityReference"]).startswith(f"{namespace}:")
            ][:12]
            if namespace is not None
            else []
        )
        message_id = str(uuid.uuid4())
        prompt = field_prompt(field)
        next_state = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type="DIALOGUE_QUESTION_OPENED",
                turnId=turn_id,
                data={
                    "questionId": f"question-{uuid.uuid4().hex[:16]}",
                    "kind": "reference_choice" if candidate_references else "input",
                    "prompt": prompt,
                    "goalIntent": CapabilityRegistry.goal_intent_for_active_workflow(
                        active_workflow
                    ),
                    "candidateReferences": candidate_references,
                    "candidateIntents": [],
                    "expectedFields": expected_fields,
                    "originatingMessageId": message_id,
                    "createdAtStateVersion": state.stateVersion + 1,
                },
            ),
        )
        choice_source = next(
            (
                message
                for message in reversed(conversation_messages)
                if _persisted_choice_view_owns_field(message, field)
            ),
            None,
        )
        view_type, view_payload = _persisted_message_view(choice_source)
        self.turn_commit.commit_success(
            conversation_id=conversation_id,
            turn_id=turn_id,
            expected_state_version=state.stateVersion,
            state=next_state,
            messages=[
                {
                    "id": message_id,
                    "role": "assistant",
                    "text": prompt,
                    "viewType": view_type,
                    "view": json.loads(view_payload) if view_payload else None,
                    "purpose": "workflow_prompt",
                    "interactionJson": None,
                    "segments": None,
                    "blocks": None,
                }
            ],
        )
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    async def _execute_turn(
        self,
        conversation_id: str,
        turn_id: str,
        client_message_id: str,
        action: dict | None,
    ) -> tuple[str, str, list]:
        conversation_messages = self.messages.list(conversation_id)
        latest_customer_text = next(
            (message.text for message in reversed(conversation_messages) if message.role == "user"),
            "",
        )
        if safety_guidance := immediate_vehicle_safety_guidance(latest_customer_text):
            return self._finish_text_turn(conversation_id, turn_id, safety_guidance)
        context = self.history_builder.build(conversation_id, conversation_messages, action)
        navigation_result = self._deterministic_navigation_result(
            conversation_id,
            turn_id,
            conversation_messages,
            action,
        )
        if navigation_result is not None:
            return navigation_result
        confirmation_result = await self._deterministic_confirmation_result(
            conversation_id,
            turn_id,
            client_message_id,
            conversation_messages,
            action,
        )
        if confirmation_result is not None:
            return await self._finish_result_with_ai(
                conversation_id, turn_id, context, confirmation_result, "protected_confirmation"
            )
        action_result = await self._structured_action_result(
            action, conversation_id, conversation_messages, context
        )
        if action_result is not None:
            return await self._finish_result_with_ai(
                conversation_id,
                turn_id,
                context,
                action_result.result,
                action_result.tool_name,
                workflow_state=action_result.workflow_state,
            )

        loop = await self.provider_loop.run(
            context.history,
            context.workflow_state,
            conversation_id,
            _turn_references(context),
        )
        protected_edit = self._protected_edit_activation(
            conversation_id,
            loop.turn_understanding,
            context,
        )
        if protected_edit is not None:
            field, view = protected_edit
            return self._finish_text_turn(
                conversation_id,
                turn_id,
                field_prompt(field),
                turn_understanding=loop.turn_understanding,
                view_type="secure_input",
                view=view,
                purpose="workflow_prompt",
            )
        if (context.history.planning_context.pending_interaction or {}).get(
            "kind"
        ) == "protected_confirmation":
            self._supersede_active_confirmation(conversation_id, turn_id)
        reply = loop.reply
        if reply is None:
            if loop.terminal_results:
                return self._finish_application_fallback(
                    conversation_id,
                    turn_id,
                    list(loop.normalized_results),
                    list(loop.terminal_results),
                    workflow_state=loop.workflow_state,
                    turn_understanding=loop.turn_understanding,
                )
            raise ValueError("provider tool loop returned no response")
        if reply.tool_calls:
            raise ValueError("provider tool loop exceeded limit")
        if reply.interaction_decision:
            result = await self._interaction_decision_result(
                reply.interaction_decision,
                conversation_id,
                conversation_messages,
                context,
            )
            if isinstance(result, ToolResult):
                return self._finish_tool_turn(conversation_id, turn_id, result)
            return self._finish_text_turn(conversation_id, turn_id, result)
        if reply.interaction_proposal is not None:
            return await self._propose_client_interaction(
                conversation_id,
                turn_id,
                context,
                reply.interaction_proposal,
            )
        if reply.response_draft is not None:
            try:
                response_draft = await self._validated_grounded_draft(
                    context.history,
                    reply.response_draft,
                    list(loop.normalized_results),
                    context.history.planning_context.workflow_state,
                    list(loop.terminal_results),
                )
            except Exception as error:
                if not loop.terminal_results:
                    raise
                logger.exception(
                    "AI grounding failed after successful tool execution; using fallback: %s",
                    type(error).__name__,
                )
                return self._finish_application_fallback(
                    conversation_id,
                    turn_id,
                    list(loop.normalized_results),
                    list(loop.terminal_results),
                    workflow_state=loop.workflow_state,
                    turn_understanding=loop.turn_understanding,
                )
            return self._finish_grounded_turn(
                conversation_id,
                turn_id,
                response_draft,
                list(loop.normalized_results),
                list(loop.terminal_results),
                workflow_state=loop.workflow_state,
                turn_understanding=loop.turn_understanding,
            )
        if not reply.text.strip() or len(reply.text) > 8000:
            if loop.terminal_results:
                return self._finish_application_fallback(
                    conversation_id,
                    turn_id,
                    list(loop.normalized_results),
                    list(loop.terminal_results),
                    workflow_state=loop.workflow_state,
                    turn_understanding=loop.turn_understanding,
                )
            raise ValueError("provider returned invalid text")
        suggestion_result = clarification_suggestion_result(
            reply.text,
            reply.suggestions,
            reply.interaction,
        )
        if reply.suggestions and suggestion_result is None:
            raise ValueError("provider returned invalid clarification suggestions")
        if suggestion_result is not None:
            return self._finish_tool_turn(conversation_id, turn_id, suggestion_result)
        if loop.terminal_results:
            return self._finish_legacy_composed_turn(
                conversation_id,
                turn_id,
                reply.text,
                list(loop.normalized_results),
                list(loop.terminal_results),
                workflow_state=loop.workflow_state,
                turn_understanding=loop.turn_understanding,
            )
        reference_presentation = reference_clarification_presentation(
            reply.interaction,
            context.history.planning_context,
        )
        if (
            reply.interaction is not None
            and reply.interaction.kind == "reference_choice"
            and reference_presentation is None
        ):
            raise ValueError("reference clarification lacks a trusted display projection")
        response_text = reference_presentation[0] if reference_presentation else reply.text
        response_interaction = (
            reply.interaction.model_copy(update={"prompt": response_text})
            if reference_presentation and reply.interaction is not None
            else reply.interaction
        )
        response_view = reference_presentation[1] if reference_presentation else None
        return self._finish_text_turn(
            conversation_id,
            turn_id,
            response_text,
            response_interaction,
            reply.turn_understanding,
            view_type="choice_list" if response_view is not None else None,
            view=response_view,
        )

    def _deterministic_navigation_result(
        self,
        conversation_id: str,
        turn_id: str,
        conversation_messages: list,
        action: dict | None,
    ):
        if not self.protected_interactions or not self.conversations or not self.turn_commit:
            return None
        interaction = self.protected_interactions.active(conversation_id)
        if interaction is None or interaction.kind != "open_vehicle_detail":
            return None
        state = self.conversations.get_state(conversation_id)
        latest_text = next(
            (message.text for message in reversed(conversation_messages) if message.role == "user"),
            "",
        )
        decision = resolve_latest(
            interaction,
            state_version=state.stateVersion,
            text=latest_text,
            action_type=str((action or {}).get("type") or "") or None,
        )
        if decision is None:
            # Any non-decision turn interrupts this one-shot navigation permission. It cannot be
            # revived by a later "yes".
            next_state = self.conversation_state_reducer.apply(
                state,
                StateEvent(
                    type="INTERACTION_SUPERSEDED",
                    turnId=turn_id,
                    data={"interactionId": interaction.interactionId},
                ),
            )
            self.turn_commit.supersede_interaction(
                conversation_id=conversation_id,
                interaction_id=interaction.interactionId,
                turn_id=turn_id,
                expected_state_version=state.stateVersion,
                state=next_state,
            )
            return None

        if decision.decision == "cancel":
            next_state = self.conversation_state_reducer.apply(
                state,
                StateEvent(
                    type="INTERACTION_CANCELLED",
                    turnId=turn_id,
                    data={"interactionId": interaction.interactionId},
                ),
            )
            messages = [{"purpose": "transition", "text": "Okay — I’ll keep you here in the chat."}]
            self.turn_commit.commit_success(
                conversation_id=conversation_id,
                turn_id=turn_id,
                expected_state_version=state.stateVersion,
                state=next_state,
                messages=messages,
                interaction_transition=(
                    interaction.interactionId,
                    "awaiting_confirmation",
                    "cancelled",
                ),
            )
            return turn_id, "completed", self.messages.list_for_turn(turn_id)

        confirmed = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_CONFIRMED",
                turnId=turn_id,
                data={"interactionId": interaction.interactionId},
            ),
        )
        executed = self.conversation_state_reducer.apply(
            confirmed,
            StateEvent(
                type="CLIENT_ACTION_ISSUED",
                turnId=turn_id,
                data={"interactionId": interaction.interactionId},
            ),
        )
        assert interaction.trustedEntity is not None
        vehicle_id = interaction.trustedEntity.reference.removeprefix("vehicle:")
        client_action = {
            "actionId": f"{interaction.interactionId}:execute",
            "type": "open_vehicle_detail",
            "interactionId": interaction.interactionId,
            "vehicleId": vehicle_id,
            "sameSiteUrl": f"/?vehicle={vehicle_id}",
        }
        self.turn_commit.commit_success(
            conversation_id=conversation_id,
            turn_id=turn_id,
            expected_state_version=state.stateVersion,
            state=executed,
            messages=[
                {"purpose": "transition", "text": "Okay — opening the full vehicle details."}
            ],
            interaction_transition=(
                interaction.interactionId,
                "awaiting_confirmation",
                "executed",
            ),
            client_actions=[client_action],
        )
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    async def _propose_client_interaction(
        self,
        conversation_id: str,
        turn_id: str,
        context: TurnContext,
        proposal,
    ):
        if not self.conversations or not self.turn_commit:
            raise ValueError("protected interaction persistence is unavailable")
        vehicle_id = proposal.entityReference.removeprefix("vehicle:")
        candidates = [*context.displayed_vehicles, *context.page_vehicles]
        matches = [item for item in candidates if item.get("vehicleId") == vehicle_id]
        if not matches:
            raise ValueError("vehicle navigation target is not in trusted context")
        navigation_result = ToolResult(
            "Vehicle navigation requires confirmation.",
            "suggestion_list",
            {
                "version": 1,
                "suggestions": [
                    {
                        "label": "Open vehicle",
                        "message": "Yes, open the vehicle",
                        "action": {"type": "confirm_active_interaction"},
                    },
                    {
                        "label": "Stay in chat",
                        "message": "No, stay in the chat",
                        "action": {"type": "cancel_active_interaction"},
                    },
                ],
            },
            {"navigationConfirmationRequested": True},
        )
        normalized = self.fact_normalizer.normalize(
            result_id=f"result-{uuid.uuid4().hex[:16]}",
            call_id=f"call-{uuid.uuid4().hex[:12]}",
            intent_id="intent-1",
            tool="propose_vehicle_navigation",
            result=navigation_result,
        )
        context.history.planning_context = replace(
            context.history.planning_context,
            trusted_tool_facts={
                "results": [normalized.ai_projection()],
                "navigationConfirmationActivation": True,
            },
        )
        composed = await self.provider.generate_turn(context.history)
        if composed.response_draft is None:
            raise ValueError("conversational agent did not compose the navigation prompt")
        response_draft = await self._validated_grounded_draft(
            context.history,
            composed.response_draft,
            [normalized],
            context.history.planning_context.workflow_state,
            [navigation_result],
        )
        resolved = self.grounding.resolve(
            response_draft,
            [normalized],
            workflow_state=context.history.planning_context.workflow_state,
        )
        state = self.conversations.get_state(conversation_id)
        message_ids = [str(uuid.uuid4()) for _ in resolved.messages]
        message_id = message_ids[-1]
        interaction_id = f"interaction-{uuid.uuid4().hex[:16]}"
        interaction = PendingInteractionState(
            interactionId=interaction_id,
            kind="open_vehicle_detail",
            trustedEntity=TrustedEntity(type="vehicle", reference=f"vehicle:{vehicle_id}"),
            originatingMessageId=message_id,
            createdAtStateVersion=state.stateVersion + 1,
            status="awaiting_confirmation",
        )
        next_state = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_PROPOSED",
                turnId=turn_id,
                data=interaction.model_dump(mode="json"),
            ),
        )
        self.turn_commit.commit_success(
            conversation_id=conversation_id,
            turn_id=turn_id,
            expected_state_version=state.stateVersion,
            state=next_state,
            messages=[
                {
                    "id": current_message_id,
                    "purpose": message.purpose,
                    "text": message.text,
                    "blocks": list(message.blocks),
                    "segments": list(message.segments),
                    "viewType": "grounded_presentation"
                    if index == len(resolved.messages) - 1
                    else None,
                    "view": (
                        {
                            "version": 1,
                            "cards": [],
                            "quickReplies": [
                                {
                                    "label": suggestion.label,
                                    "message": suggestion.message,
                                    **({"action": suggestion.action} if suggestion.action else {}),
                                }
                                for suggestion in resolved.suggestions
                            ],
                        }
                        if index == len(resolved.messages) - 1
                        else None
                    ),
                }
                for index, (current_message_id, message) in enumerate(
                    zip(message_ids, resolved.messages, strict=True)
                )
            ],
            interaction=interaction,
        )
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    async def _deterministic_confirmation_result(
        self,
        conversation_id: str,
        turn_id: str,
        client_message_id: str,
        conversation_messages: list,
        action: dict | None,
    ) -> ToolResult | None:
        """Resolve only an exact decision for the latest persisted confirmation."""
        latest_text = next(
            (message.text for message in reversed(conversation_messages) if message.role == "user"),
            "",
        )
        intent = _confirmation_intent(latest_text, action)
        persisted = (
            self.protected_interactions.active(conversation_id)
            if self.protected_interactions is not None
            else None
        )
        persisted_confirmation = (
            persisted
            if persisted is not None and persisted.kind == "protected_confirmation"
            else None
        )
        if persisted_confirmation is not None:
            state = self.conversations.get_state(conversation_id)
            resolution = resolve_latest(
                persisted_confirmation,
                state_version=state.stateVersion,
                text=latest_text,
                action_type=str((action or {}).get("type") or "") or None,
            )
            if resolution is None:
                # Natural corrections need hosted semantic resolution before the application can
                # decide whether to retain this review for protected replacement or supersede it
                # as an interruption. The shared post-resolution boundary makes that decision.
                return None
            intent = resolution.decision
            draft_id = persisted_confirmation.activeDraftId
            workflow_kind = persisted_confirmation.workflowKind
        else:
            if intent is None:
                return None
            pending = preceding_interaction(conversation_messages)
            if pending is None or pending[0].kind != "protected_confirmation":
                return None
            legacy_interaction, _message = pending
            draft_id = legacy_interaction.draft_id
            workflow_kind = legacy_interaction.workflow_kind
        if not draft_id or not workflow_kind:
            return None
        if intent == "confirm":
            if self.workflows is None:
                return None
            try:
                result = await self.workflows.confirm(
                    conversation_id,
                    draft_id,
                    client_message_id,
                    workflow_kind,
                )
            except DealershipError as error:
                if persisted_confirmation is not None:
                    self._mark_interaction_unavailable(
                        conversation_id, turn_id, persisted_confirmation, error
                    )
                if not error.recovery:
                    return ToolResult(
                        str(error),
                        None,
                        None,
                        {
                            "operationUnavailable": True,
                            "errorCode": error.code,
                            "retryable": error.retryable,
                        },
                    )
                recovery = dict(error.recovery)
                view_type = str(recovery.get("viewType") or "suggestion_list")
                view = recovery.get("view")
                if not isinstance(view, dict):
                    view = {"version": 1}
                if view_type in {"slot_list", "test_drive_slot_picker"}:
                    view = appointment_collection_payload(view)
                return ToolResult(
                    str(error),
                    view_type,
                    view,
                    {
                        "operationUnavailable": True,
                        "errorCode": error.code,
                        "journey": recovery.get("journey"),
                        "items": list(view.get("items") or []),
                    },
                    alternative_offer=(
                        recovery.get("alternativeOffer")
                        if isinstance(recovery.get("alternativeOffer"), dict)
                        else None
                    ),
                )
            if persisted_confirmation is not None:
                self._complete_persisted_interaction(
                    conversation_id, turn_id, persisted_confirmation, "executed"
                )
            return ToolResult(
                _confirmation_receipt_text(str(result.get("kind") or "")),
                "receipt",
                result,
                {"confirmationResolved": True, "intent": intent},
            )
        if self.workflow_repository is None:
            return None
        draft = self.workflow_repository.cancel(conversation_id, draft_id)
        if persisted_confirmation is not None:
            self._complete_persisted_interaction(
                conversation_id, turn_id, persisted_confirmation, "cancelled"
            )
        result = {
            "kind": "request_cancelled",
            "status": "cancelled",
            "requestKind": draft.get("kind"),
        }
        return ToolResult(
            "Okay — the request has been cancelled.",
            "receipt",
            result,
            {"confirmationResolved": True, "intent": intent},
        )

    def _complete_persisted_interaction(
        self,
        conversation_id: str,
        turn_id: str,
        interaction: PendingInteractionState,
        status: str,
    ) -> None:
        if not self.conversations or not self.turn_commit:
            return
        state = self.conversations.get_state(conversation_id)
        event = "INTERACTION_CANCELLED" if status == "cancelled" else "INTERACTION_CONFIRMED"
        next_state = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type=event,
                turnId=turn_id,
                data={"interactionId": interaction.interactionId},
            ),
        )
        if status == "executed":
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="INTERACTION_EXECUTED",
                    turnId=turn_id,
                    data={"interactionId": interaction.interactionId},
                ),
            )
        self.turn_commit.transition_interaction_state(
            conversation_id=conversation_id,
            interaction_id=interaction.interactionId,
            expected_status="awaiting_confirmation",
            status=status,
            expected_state_version=state.stateVersion,
            state=next_state,
        )

    def _mark_interaction_unavailable(
        self,
        conversation_id: str,
        turn_id: str,
        interaction: PendingInteractionState,
        error: DealershipError,
    ) -> None:
        """Expire the stale confirmation and return its workflow to AI-owned selection."""

        if not self.conversations or not self.turn_commit:
            return
        state = self.conversations.get_state(conversation_id)
        if (
            state.pendingInteraction is None
            or state.pendingInteraction.interactionId != interaction.interactionId
            or state.pendingInteraction.status != "awaiting_confirmation"
        ):
            raise ValueError("protected interaction is no longer active")
        next_state = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_UNAVAILABLE",
                turnId=turn_id,
                data={"interactionId": interaction.interactionId},
            ),
        )
        recovery = dict(error.recovery)
        view_type = str(recovery.get("viewType") or "")
        journey = str(recovery.get("journey") or "")
        current = dict(
            state.agentWorkflow or self.conversations.get_workflow_state(conversation_id)
        )
        entities = dict(current.get("entities") or {})
        next_workflow = None
        if view_type == "slot_list" and journey == "workshop":
            arguments = {
                key: entities[key] for key in ("serviceTypeId", "dealershipId") if entities.get(key)
            }
            if current.get("activeWorkflow") == "workshop_amend":
                arguments["workflowMode"] = "amendment"
            next_workflow = self.state_reducer.advance(
                current,
                "refine_workshop_slots",
                arguments,
                view_type,
                {"items": list((recovery.get("view") or {}).get("items") or [])},
            )
        elif view_type == "test_drive_slot_picker" and journey == "test_drive":
            vehicle_id = str(recovery.get("vehicleId") or entities.get("vehicleId") or "")
            arguments = {"vehicleId": vehicle_id} if vehicle_id else {}
            next_workflow = self.state_reducer.advance(
                current,
                "list_test_drive_slots",
                arguments,
                view_type,
                {"items": list((recovery.get("view") or {}).get("items") or [])},
            )
        if next_workflow is not None:
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="AGENT_WORKFLOW_REPLACED",
                    turnId=turn_id,
                    data={"workflow": next_workflow},
                ),
            )
        self.turn_commit.transition_interaction_state(
            conversation_id=conversation_id,
            interaction_id=interaction.interactionId,
            expected_status="awaiting_confirmation",
            status="unavailable",
            expected_state_version=state.stateVersion,
            state=next_state,
        )

    async def _interaction_decision_result(
        self,
        decision: str,
        conversation_id: str,
        conversation_messages: list,
        context: TurnContext,
    ) -> ToolResult | str:
        pending = preceding_interaction(conversation_messages)
        if pending is None:
            raise ValueError("provider decided an interaction that is not pending")
        interaction, assistant_message = pending
        if interaction.kind == "input":
            raise ValueError("provider cannot accept or decline an input interaction")
        if decision == "decline":
            return (
                "No action has been taken."
                if interaction.kind == "protected_confirmation"
                else "Okay — I won't continue with that."
            )
        if decision != "accept":
            raise ValueError("provider returned an unknown interaction decision")
        if interaction.kind == "single_action":
            execution = await self._structured_action_result(
                interaction.actions[0],
                conversation_id,
                conversation_messages,
                context,
            )
            return (
                execution.result
                if execution is not None
                else "That option is no longer available. Please choose another action."
            )
        return _repeat_pending_view(interaction, assistant_message)

    def _protected_edit_activation(
        self,
        conversation_id: str,
        understanding: TurnUnderstanding | None,
        context: TurnContext,
    ) -> tuple[str, dict] | None:
        """Turn a semantic review correction into one protected collector activation."""

        pending = context.history.planning_context.pending_interaction or {}
        if pending.get("kind") != "protected_confirmation" or understanding is None:
            return None
        kind = str(pending.get("workflow_kind") or "")
        if understanding.dialogueAct != "modify_goal":
            return None
        field = CapabilityRegistry.secure_edit_field(
            kind,
            [item.field for item in understanding.requestedInputChanges],
        )
        if field is None or self.protected_interactions is None:
            return None
        active = self.protected_interactions.active(conversation_id)
        draft_id = str(pending.get("draft_id") or "")
        if (
            active is None
            or active.kind != "protected_confirmation"
            or active.activeDraftId != draft_id
            or active.workflowKind != kind
        ):
            return None
        workflow = dict(context.workflow_state or {})
        summary = {
            **dict(workflow.get("entities") or {}),
            **dict((workflow.get("constraints") or {}).get("collectedPublicValues") or {}),
        }
        return field, {
            "version": 1,
            "sourceViewType": "draft",
            "draftId": draft_id,
            "kind": kind,
            "status": "collecting",
            "summary": summary,
            "missingPublicFields": [],
            "secureFields": [field],
            "secureInputReady": True,
            "editField": field,
        }

    def _supersede_active_confirmation(self, conversation_id: str, turn_id: str) -> None:
        """Retire a review after semantic resolution proves the turn is not an edit."""

        if self.protected_interactions is None or self.turn_commit is None:
            return
        interaction = self.protected_interactions.active(conversation_id)
        if interaction is None or interaction.kind != "protected_confirmation":
            return
        state = self.conversations.get_state(conversation_id)
        next_state = self.conversation_state_reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_SUPERSEDED",
                turnId=turn_id,
                data={"interactionId": interaction.interactionId},
            ),
        )
        self.turn_commit.supersede_interaction(
            conversation_id=conversation_id,
            interaction_id=interaction.interactionId,
            turn_id=turn_id,
            expected_state_version=state.stateVersion,
            state=next_state,
        )

    async def _structured_action_result(
        self,
        action: dict | None,
        conversation_id: str,
        conversation_messages: list,
        context: TurnContext,
    ):
        if self.action_handler is None or not action:
            return None
        execution = await self.action_handler.execute(
            action,
            conversation_id,
            conversation_messages,
            context.workflow_state,
        )
        if execution is None:
            return None
        result = execution.result
        workflow_state = execution.workflow_state or self.state_reducer.advance(
            context.workflow_state,
            execution.tool_name,
            execution.arguments,
            result.view_type,
            result.facts,
            transition=execution.transition,
        )
        return replace(execution, workflow_state=workflow_state)

    def _finish_text_turn(
        self,
        conversation_id: str,
        turn_id: str,
        text: str,
        interaction: PendingInteraction | None = None,
        turn_understanding: TurnUnderstanding | None = None,
        view_type: str | None = None,
        view: dict | None = None,
        purpose: str | None = None,
    ):
        messages = [
            {
                "id": str(uuid.uuid4()),
                "text": text.strip(),
                "viewType": view_type,
                "view": view,
                "interactionJson": interaction.as_json() if interaction else None,
                "purpose": purpose or ("clarification" if interaction else "answer"),
            }
        ]
        _ensure_conversation_continuation(messages)
        if (
            self.conversations is not None
            and self.turn_commit is not None
            and (
                turn_understanding is not None
                or interaction is not None
                and interaction.kind in {"reference_choice", "intent_choice"}
            )
        ):
            state = self.conversations.get_state(conversation_id)
            question_disposition = _dialogue_question_disposition(
                state,
                state.agentWorkflow,
                turn_understanding,
                [],
                self.state_reducer,
            )
            if interaction is not None and interaction.kind in {
                "reference_choice",
                "intent_choice",
            }:
                question_disposition = "retain"
            if question_disposition == "retain" and interaction is None:
                retained_view = _retained_question_choice_view(
                    state,
                    self.messages.list(conversation_id),
                )
                if retained_view is not None:
                    messages[0].update(
                        {
                            "text": state.dialogue.activeQuestion.prompt,
                            "viewType": retained_view[0],
                            "view": retained_view[1],
                            "purpose": "workflow_prompt",
                        }
                    )
            next_state = state
            if turn_understanding is not None:
                next_state = self.conversation_state_reducer.apply(
                    next_state,
                    StateEvent(
                        type="DIALOGUE_TURN_RESOLVED",
                        turnId=turn_id,
                        data={
                            "understanding": turn_understanding.model_dump(mode="json"),
                            "questionDisposition": question_disposition,
                        },
                    ),
                )
            message_id = str(messages[0]["id"])
            if interaction is not None and interaction.kind in {
                "reference_choice",
                "intent_choice",
            }:
                next_state = self.conversation_state_reducer.apply(
                    next_state,
                    StateEvent(
                        type="DIALOGUE_QUESTION_OPENED",
                        turnId=turn_id,
                        data={
                            "questionId": interaction.question_id,
                            "kind": interaction.kind,
                            "prompt": interaction.prompt,
                            "goalIntent": interaction.goal_intent,
                            "candidateReferences": interaction.candidate_references,
                            "candidateIntents": interaction.candidate_intents,
                            "originatingMessageId": message_id,
                            "createdAtStateVersion": next_state.stateVersion + 1,
                        },
                    ),
                )
            self.turn_commit.commit_success(
                conversation_id=conversation_id,
                turn_id=turn_id,
                expected_state_version=state.stateVersion,
                state=next_state,
                messages=messages,
            )
            return turn_id, "completed", self.messages.list_for_turn(turn_id)
        for message in messages:
            self.messages.add(
                conversation_id,
                "assistant",
                message["text"],
                turn_id,
                message.get("viewType"),
                json.dumps(message.get("view"), separators=(",", ":"))
                if message.get("view") is not None
                else None,
                interaction_json=message.get("interactionJson"),
                purpose=message.get("purpose"),
            )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    async def _finish_result_with_ai(
        self,
        conversation_id: str,
        turn_id: str,
        context: TurnContext,
        result: ToolResult,
        tool_name: str,
        *,
        workflow_state: dict | None = None,
    ):
        """Compose every non-navigation tool result through the same conversational agent."""

        if (
            self.conversations is not None
            and result.view_type == "receipt"
            and isinstance(result.facts, dict)
            and result.facts.get("confirmationResolved") is True
        ):
            self.conversations.update_workflow_state(conversation_id, {})
        operational_receipt = (
            result.view_type == "receipt"
            and isinstance(result.facts, dict)
            and result.facts.get("confirmationResolved") is True
        )
        if operational_receipt:
            return self._finish_tool_turn(conversation_id, turn_id, result)
        if workflow_state is None:
            workflow_state = (
                self.conversations.get_workflow_state(conversation_id)
                if self.conversations is not None
                else context.workflow_state
            )
        normalized = self.fact_normalizer.normalize(
            result_id=f"result-{uuid.uuid4().hex[:16]}",
            call_id=f"call-{uuid.uuid4().hex[:12]}",
            intent_id="intent-1",
            tool=tool_name,
            result=result,
        )
        context.history.planning_context = replace(
            context.history.planning_context,
            workflow_state=workflow_state,
            trusted_tool_facts={
                "results": [normalized.ai_projection()],
                "secureInputActivation": _is_secure_input_result(result),
                "selectionPromptActivation": bool(
                    result.view_type == "service_list"
                    and isinstance(result.view_payload, dict)
                    and result.view_payload.get("selectionOnly") is True
                ),
                "collectionPresentationActivation": _is_structured_collection_result(result),
                "applicationContinuation": composition_continuation(result.interaction),
            },
        )
        try:
            reply = await self.provider.generate_turn(context.history)
        except Exception as error:
            logger.exception(
                "AI composition failed after a trusted result; using fallback: %s",
                type(error).__name__,
            )
            return self._finish_application_fallback(
                conversation_id,
                turn_id,
                [normalized],
                [result],
                workflow_state=workflow_state,
            )
        if reply.response_draft is not None:
            try:
                response_draft = await self._validated_grounded_draft(
                    context.history,
                    reply.response_draft,
                    [normalized],
                    workflow_state,
                    [result],
                )
            except Exception as error:
                logger.exception(
                    "AI grounding failed after a trusted result; using fallback: %s",
                    type(error).__name__,
                )
                return self._finish_application_fallback(
                    conversation_id,
                    turn_id,
                    [normalized],
                    [result],
                    workflow_state=workflow_state,
                )
            return self._finish_grounded_turn(
                conversation_id,
                turn_id,
                response_draft,
                [normalized],
                [result],
                workflow_state=workflow_state,
            )
        if reply.text.strip():
            return self._finish_legacy_composed_turn(
                conversation_id,
                turn_id,
                reply.text,
                [normalized],
                [result],
                workflow_state=workflow_state,
            )
        return self._finish_application_fallback(
            conversation_id,
            turn_id,
            [normalized],
            [result],
            workflow_state=workflow_state,
        )

    def _finish_application_fallback(
        self,
        conversation_id: str,
        turn_id: str,
        normalized_results: list,
        terminal_results: list,
        *,
        workflow_state: dict | None = None,
        turn_understanding: TurnUnderstanding | None = None,
    ):
        """Commit trusted results with application-owned wording if AI presentation fails."""

        return self._finish_legacy_composed_turn(
            conversation_id,
            turn_id,
            _application_fallback_text(
                terminal_results,
                workflow_state or {},
            ),
            normalized_results,
            terminal_results,
            workflow_state=workflow_state,
            turn_understanding=turn_understanding,
        )

    async def _validated_grounded_draft(
        self,
        history,
        draft,
        normalized_results: list,
        workflow_state: dict,
        terminal_results: list,
    ):
        """Apply at most one trust-boundary repair against unchanged facts."""

        candidate = draft
        for attempt in range(_GROUNDING_ATTEMPTS):
            try:
                self.grounding.resolve(
                    candidate,
                    normalized_results,
                    workflow_state=workflow_state,
                    question_owned=_typed_question_owner(
                        normalized_results,
                        terminal_results,
                        workflow_state,
                    ),
                )
                return candidate
            except ValueError as error:
                failure = _safe_grounding_failure(error)
                logger.warning(
                    "grounded response rejected",
                    extra={
                        "context": {
                            "repair_attempt": attempt,
                            "failure": failure,
                        }
                    },
                )
                if attempt == _GROUNDING_ATTEMPTS - 1:
                    raise
                trusted = dict(history.planning_context.trusted_tool_facts or {})
                previous = list(trusted.get("groundingValidationFailures") or [])
                trusted["groundingValidationFailure"] = failure
                trusted["groundingValidationFailures"] = [*previous, failure]
                history.planning_context = replace(
                    history.planning_context,
                    trusted_tool_facts=trusted,
                )
                repaired = await self.provider.generate_turn(history)
                if repaired.response_draft is None:
                    raise ValueError("composer repair did not return a grounded response")
                candidate = repaired.response_draft
        raise ValueError("grounded response repair exhausted")

    def _finish_grounded_turn(
        self,
        conversation_id: str,
        turn_id: str,
        draft,
        normalized_results: list,
        terminal_results: list,
        *,
        workflow_state: dict | None = None,
        turn_understanding: TurnUnderstanding | None = None,
    ):
        if workflow_state is None:
            workflow_state = (
                self.conversations.get_workflow_state(conversation_id)
                if self.conversations is not None
                else {}
            )
        resolved = self.grounding.resolve(
            draft,
            normalized_results,
            workflow_state=workflow_state,
        )
        collection_card_references = _structured_collection_card_references(normalized_results)
        conversation_messages = self.messages.list(conversation_id)
        visible_presentations = (
            set()
            if _turn_requests_result_presentation(turn_understanding)
            else _visible_read_only_presentations(conversation_messages)
        )

        def claim_read_only_presentation(card_type: str, data: dict) -> bool:
            fingerprint = _read_only_presentation_fingerprint(card_type, data)
            if fingerprint is None:
                return True
            if fingerprint in visible_presentations:
                return False
            visible_presentations.add(fingerprint)
            return True

        # Trusted tools decide when a visual summary materially helps. The AI writes the
        # conversation, but omitting a card reference must neither hide authoritative results nor
        # fail the whole turn. Collection-backed cards are rendered through their trusted view.
        mandatory_cards = [
            card
            for result in normalized_results
            for card in result.availableCards
            if card.reference not in collection_card_references
        ]
        collection_suggestion_references = _structured_collection_suggestion_references(
            normalized_results
        )
        selected_cards = [
            card for card in resolved.cards if card.reference not in collection_card_references
        ]
        selected_references = {card.reference for card in selected_cards}
        selected_cards.extend(
            card for card in mandatory_cards if card.reference not in selected_references
        )
        selected_cards = [
            card for card in selected_cards if claim_read_only_presentation(card.type, card.data)
        ]
        card_payloads = [card.model_dump(mode="json") for card in selected_cards]
        selected_suggestions = [
            item
            for item in resolved.suggestions
            if item.reference not in collection_suggestion_references
        ]
        quick_replies = [
            {
                "label": item.label,
                "message": item.message,
                **({"action": item.action} if item.action else {}),
            }
            for item in selected_suggestions[:4]
        ]
        secure_input = _latest_secure_input(terminal_results)
        slot_context = _latest_slot_context(terminal_results, normalized_results)
        collection_views = _structured_collection_views(
            normalized_results,
            terminal_results,
        )
        collection_views = {
            reference: view
            for reference, view in collection_views.items()
            if claim_read_only_presentation(view[0], view[1])
        }
        response_messages = list(
            normalize_explicit_card_owned_messages(
                resolved.messages,
                selected_cards,
                normalized_results,
            )
            if _turn_requests_result_presentation(turn_understanding)
            else resolved.messages
        )
        card_owner_index = _card_owner_message_index(response_messages, selected_cards)
        committed_messages = []
        message_interaction = _latest_terminal_interaction(terminal_results)
        for index, message in enumerate(response_messages):
            collection_view = collection_views.get(message.collection_reference)
            if collection_view is not None and message.collection_rendered_in_blocks:
                collection_view = (
                    collection_view[0],
                    {
                        **collection_view[1],
                        "collectionPresentationRendered": True,
                    },
                )
            is_last = index == len(response_messages) - 1
            special_view = (
                ("secure_input", secure_input)
                if is_last and secure_input is not None
                else (
                    "trusted_slot_context",
                    {**slot_context, "quickReplies": quick_replies},
                )
                if is_last and slot_context is not None
                else None
            )
            message_presentation = {
                "version": 1,
                "cards": card_payloads if index == card_owner_index else [],
                "quickReplies": quick_replies if is_last else [],
            }
            attach_presentation = (
                collection_view is None
                and special_view is None
                and (message_presentation["cards"] or message_presentation["quickReplies"])
            )
            committed_messages.append(
                {
                    "text": message.text,
                    "viewType": (
                        collection_view[0]
                        if collection_view is not None
                        else special_view[0]
                        if special_view is not None
                        else "grounded_presentation"
                        if attach_presentation
                        else None
                    ),
                    "view": (
                        collection_view[1]
                        if collection_view is not None
                        else special_view[1]
                        if special_view is not None
                        else message_presentation
                        if attach_presentation
                        else None
                    ),
                    "purpose": message.purpose,
                    "interactionJson": None,
                    "blocks": list(message.blocks),
                    "segments": list(message.segments),
                }
            )
        _apply_alternative_disclosure(
            committed_messages,
            _latest_alternative_offer(normalized_results),
        )
        _normalize_secure_input_prompt(committed_messages, secure_input)
        _normalize_persisted_message_completeness(committed_messages)
        if message_interaction is not None and committed_messages:
            committed_messages[-1]["interactionJson"] = message_interaction.as_json()
        if self.turn_commit and self.conversations:
            self._commit_grounded_turn(
                conversation_id,
                turn_id,
                committed_messages,
                normalized_results,
                terminal_results,
                workflow_state=workflow_state,
                turn_understanding=turn_understanding,
            )
        else:
            for message in committed_messages:
                self.messages.add(
                    conversation_id,
                    "assistant",
                    message["text"],
                    turn_id,
                    message.get("viewType"),
                    json.dumps(message.get("view"), separators=(",", ":"))
                    if message.get("view") is not None
                    else None,
                    purpose=message.get("purpose"),
                    interaction_json=message.get("interactionJson"),
                    segments_json=json.dumps(message.get("segments"), separators=(",", ":"))
                    if message.get("segments") is not None
                    else None,
                    blocks_json=json.dumps(message.get("blocks"), separators=(",", ":"))
                    if message.get("blocks") is not None
                    else None,
                )
            self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    def _finish_legacy_composed_turn(
        self,
        conversation_id: str,
        turn_id: str,
        text: str,
        normalized_results: list,
        terminal_results: list,
        *,
        workflow_state: dict | None = None,
        turn_understanding: TurnUnderstanding | None = None,
    ):
        """Limited-demo bridge: preserve AI/router prose and static domain presentations."""
        conversation_messages = self.messages.list(conversation_id)
        visible_presentations = (
            set()
            if _turn_requests_result_presentation(turn_understanding)
            else _visible_read_only_presentations(conversation_messages)
        )

        def claim_read_only_presentation(card_type: str, data: dict) -> bool:
            fingerprint = _read_only_presentation_fingerprint(card_type, data)
            if fingerprint is None:
                return True
            if fingerprint in visible_presentations:
                return False
            visible_presentations.add(fingerprint)
            return True

        collection_card_references = _structured_collection_card_references(normalized_results)
        collection_suggestion_references = _structured_collection_suggestion_references(
            normalized_results
        )
        cards = [
            card.model_dump(mode="json")
            for result in normalized_results
            for card in result.availableCards
            if card.reference not in collection_card_references
            and claim_read_only_presentation(card.type, card.data)
        ]
        suggestion_candidates = (
            workflow_continuation_suggestions(normalized_results, workflow_state or {})
            if (workflow_state or {}).get("activeWorkflow")
            else [item for result in normalized_results for item in result.availableSuggestions]
        )
        suggestions = [
            {
                "label": item.label,
                "message": item.message,
                **({"action": item.action} if item.action else {}),
            }
            for item in suggestion_candidates
            if item.reference not in collection_suggestion_references
        ]
        presentation = {"version": 1, "cards": cards, "quickReplies": suggestions}
        secure_input = _latest_secure_input(terminal_results)
        slot_context = _latest_slot_context(terminal_results, normalized_results)
        collection_messages = _structured_collection_messages(terminal_results)
        for message in collection_messages:
            view_type = message.get("viewType")
            view = message.get("view")
            if (
                isinstance(view_type, str)
                and isinstance(view, dict)
                and not claim_read_only_presentation(view_type, view)
            ):
                message["viewType"] = None
                message["view"] = None
        application_prompt_only = _structured_collection_only_turn(
            normalized_results, terminal_results
        )
        committed_messages = (
            []
            if application_prompt_only
            else [
                {
                    "text": text.strip(),
                    "viewType": "grounded_presentation" if cards or suggestions else None,
                    "view": presentation if cards or suggestions else None,
                    "purpose": "answer",
                    "interactionJson": None,
                    "segments": None,
                }
            ]
        )
        committed_messages.extend(collection_messages)
        if committed_messages and secure_input is not None:
            committed_messages[-1]["viewType"] = "secure_input"
            committed_messages[-1]["view"] = secure_input
        elif committed_messages and slot_context is not None:
            committed_messages[-1]["viewType"] = "trusted_slot_context"
            committed_messages[-1]["view"] = {
                **slot_context,
                "quickReplies": suggestions,
            }
        _apply_alternative_disclosure(
            committed_messages,
            _latest_alternative_offer(normalized_results),
        )
        _normalize_secure_input_prompt(committed_messages, secure_input)
        missing_public_fields = list(
            dict((workflow_state or {}).get("constraints") or {}).get("missingPublicFields") or []
        )
        if committed_messages and missing_public_fields:
            committed_messages[-1]["purpose"] = "workflow_prompt"
            if not str(committed_messages[-1].get("text") or "").rstrip().endswith("?"):
                committed_messages[-1]["text"] = _application_fallback_text(
                    terminal_results,
                    workflow_state or {},
                )
        message_interaction = _latest_terminal_interaction(terminal_results)
        if message_interaction is not None and committed_messages:
            committed_messages[-1]["interactionJson"] = message_interaction.as_json()
        _ensure_conversation_continuation(
            committed_messages,
            suggestions=suggestions,
        )
        if self.turn_commit and self.conversations:
            self._commit_grounded_turn(
                conversation_id,
                turn_id,
                committed_messages,
                normalized_results,
                terminal_results,
                workflow_state=workflow_state,
                turn_understanding=turn_understanding,
            )
        else:
            for message in committed_messages:
                self.messages.add(
                    conversation_id,
                    "assistant",
                    message["text"],
                    turn_id,
                    message.get("viewType"),
                    json.dumps(message.get("view"), separators=(",", ":"))
                    if message.get("view") is not None
                    else None,
                    purpose=message.get("purpose"),
                    interaction_json=message.get("interactionJson"),
                    segments_json=json.dumps(message.get("segments"), separators=(",", ":"))
                    if message.get("segments") is not None
                    else None,
                    blocks_json=json.dumps(message.get("blocks"), separators=(",", ":"))
                    if message.get("blocks") is not None
                    else None,
                )
            self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    def _commit_grounded_turn(
        self,
        conversation_id: str,
        turn_id: str,
        messages: list[dict],
        normalized_results: list,
        terminal_results: list,
        *,
        workflow_state: dict | None = None,
        turn_understanding: TurnUnderstanding | None = None,
    ) -> None:
        state = self.conversations.get_state(conversation_id)
        effective_workflow_state = (
            workflow_state if workflow_state is not None else state.agentWorkflow
        )
        question_disposition = _dialogue_question_disposition(
            state,
            effective_workflow_state,
            turn_understanding,
            normalized_results,
            self.state_reducer,
        )
        expected_version = state.stateVersion
        agenda = [
            {
                "intentId": result.intentId,
                "kind": _result_kind(result.tool),
                "order": index,
                "status": "ready",
                "resultIds": [],
            }
            for index, result in enumerate(normalized_results, start=1)
        ]
        next_state = state
        if turn_understanding is not None:
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="DIALOGUE_TURN_RESOLVED",
                    turnId=turn_id,
                    data={
                        "understanding": turn_understanding.model_dump(mode="json"),
                        "questionDisposition": question_disposition,
                    },
                ),
            )
        if workflow_state is not None and workflow_state != state.agentWorkflow:
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="AGENT_WORKFLOW_REPLACED",
                    turnId=turn_id,
                    data={"workflow": workflow_state},
                ),
            )
        next_state = self.conversation_state_reducer.apply(
            next_state,
            StateEvent(
                type="PLAN_VALIDATED",
                turnId=turn_id,
                data={
                    "agenda": agenda,
                    "activeTopic": _result_kind(normalized_results[0].tool)
                    if normalized_results
                    else "conversation",
                },
            ),
        )
        for result in normalized_results:
            kind = _result_kind(result.tool)
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="TOOL_RESULT_RECEIVED",
                    turnId=turn_id,
                    data={
                        "intentId": result.intentId,
                        "resultId": result.resultId,
                        "resultKind": kind if kind in _RESULT_SET_KINDS else "",
                    },
                ),
            )
        protected_interaction = None
        legacy_interaction = _latest_terminal_interaction(terminal_results)
        if legacy_interaction is not None and legacy_interaction.kind == "protected_confirmation":
            if not messages:
                raise ValueError("protected confirmation requires an assistant message")
            message_id = str(messages[-1].get("id") or uuid.uuid4())
            messages[-1]["id"] = message_id
            protected_interaction = PendingInteractionState(
                interactionId=f"interaction-{uuid.uuid4().hex[:16]}",
                kind="protected_confirmation",
                originatingMessageId=message_id,
                createdAtStateVersion=next_state.stateVersion + 1,
                status="awaiting_confirmation",
                activeDraftId=legacy_interaction.draft_id,
                workflowKind=legacy_interaction.workflow_kind,
            )
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="INTERACTION_PROPOSED",
                    turnId=turn_id,
                    data=protected_interaction.model_dump(mode="json"),
                ),
            )
        dialogue_question = _workflow_dialogue_question(
            messages,
            normalized_results,
            workflow_state or {},
            question_disposition,
            next_state.stateVersion + 1,
            active_question=state.dialogue.activeQuestion,
        )
        if protected_interaction is None and dialogue_question is not None:
            next_state = self.conversation_state_reducer.apply(
                next_state,
                StateEvent(
                    type="DIALOGUE_QUESTION_OPENED",
                    turnId=turn_id,
                    data=dialogue_question,
                ),
            )
        self.turn_commit.commit_success(
            conversation_id=conversation_id,
            turn_id=turn_id,
            expected_state_version=expected_version,
            state=next_state,
            messages=messages,
            result_sets=[(_result_kind(result.tool), result) for result in normalized_results],
            interaction=protected_interaction,
        )

    def _finish_tool_turn(self, conversation_id: str, turn_id: str, result):
        self._persist_tool_result(conversation_id, turn_id, result)
        if (
            not str(result.text or "").rstrip().endswith("?")
            and _latest_terminal_interaction([result]) is None
        ):
            receipt = result.view_payload if isinstance(result.view_payload, dict) else {}
            kind = str(receipt.get("kind") or "")
            booking_status = receipt.get("status") if kind.startswith("workshop_") else None
            suggestions = workshop_booking_status_suggestions(booking_status)
            prompt = workshop_booking_status_prompt(booking_status)
            self.messages.add(
                conversation_id,
                "assistant",
                prompt or _OPEN_CONTINUATION,
                turn_id,
                "grounded_presentation" if suggestions else None,
                (
                    json.dumps(
                        {
                            "version": 1,
                            "cards": [],
                            "quickReplies": [
                                {
                                    "label": suggestion["label"],
                                    "message": suggestion["text"],
                                }
                                for suggestion in suggestions
                            ],
                        },
                        separators=(",", ":"),
                    )
                    if suggestions
                    else None
                ),
                purpose="follow_up",
            )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    def _persist_tool_result(self, conversation_id: str, turn_id: str, result) -> None:
        payload = dict(result.view_payload) if result.view_payload else None
        interaction = result.interaction or interaction_from_view(
            result.text,
            result.view_type,
            payload,
        )
        text = str(result.text or "").strip()
        if has_incomplete_sentence_ending(text) and not (result.view_type or interaction):
            text = _UNCLEAR_RESPONSE_FALLBACK
        self.messages.add(
            conversation_id,
            "assistant",
            text,
            turn_id,
            result.view_type,
            json.dumps(payload) if payload else None,
            interaction.as_json() if interaction else None,
            purpose=("follow_up" if str(result.text or "").rstrip().endswith("?") else "answer"),
        )


def _ensure_conversation_continuation(
    messages: list[dict],
    *,
    suggestions: list[dict] | None = None,
) -> None:
    """End a successful customer-facing sequence with an explicit next move.

    This is the persistence-side safety net shared by direct responses, legacy providers, and
    application fallbacks. Grounded responses apply the same invariant before trust resolution.
    """

    _normalize_persisted_message_completeness(messages)
    if not messages:
        return
    final_text = str(messages[-1].get("text") or "").strip()
    if final_text == _TERMINAL_FAREWELL:
        return
    if messages[-1].get("interactionJson") or messages[-1].get("viewType") == "secure_input":
        return
    if final_text.endswith("?"):
        if messages[-1].get("purpose") == "answer":
            messages[-1]["purpose"] = "follow_up"
        return

    available = list(suggestions or [])
    if len(available) >= 4:
        selected = available[:4]
    elif len(available) >= 2:
        selected = available[:2]
    else:
        selected = available[:1]

    # Legacy/fallback presentations used to leave chips attached to the factual answer. Move them
    # to the explicit question that owns them while leaving cards on their original message.
    for message in messages:
        if message.get("viewType") != "grounded_presentation":
            continue
        view = message.get("view")
        if not isinstance(view, dict) or not view.get("quickReplies"):
            continue
        next_view = {**view, "quickReplies": []}
        if next_view.get("cards"):
            message["view"] = next_view
        else:
            message["viewType"] = None
            message["view"] = None

    prompt = _OPTION_CONTINUATION if selected else _OPEN_CONTINUATION
    messages.append(
        {
            "id": str(uuid.uuid4()),
            "text": prompt,
            "viewType": "grounded_presentation" if selected else None,
            "view": ({"version": 1, "cards": [], "quickReplies": selected} if selected else None),
            "purpose": "follow_up",
            "interactionJson": None,
            "segments": None,
            "blocks": None,
        }
    )


def _normalize_persisted_message_completeness(messages: list[dict]) -> None:
    """Keep incomplete prose out of every legacy, fallback, and direct persistence path."""

    if not messages:
        return
    original = list(messages)
    normalized: list[dict] = []
    for message in original:
        text = str(message.get("text") or "").strip()
        if not has_incomplete_sentence_ending(text):
            normalized.append(message)
            continue
        if message.get("viewType") or message.get("interactionJson"):
            # A colon or other visual introduction is complete when the trusted view or
            # application-owned interaction supplies the object that follows it.
            normalized.append(message)
    if normalized:
        messages[:] = normalized
        return
    fallback = dict(original[-1])
    fallback.update(
        {
            "text": _UNCLEAR_RESPONSE_FALLBACK,
            "segments": None,
            "blocks": None,
        }
    )
    messages[:] = [fallback]


def _latest_terminal_interaction(results: list) -> PendingInteraction | None:
    """Carry the last application-owned prompt through grounded AI composition."""
    for result in reversed(results):
        interaction = result.interaction or interaction_from_view(
            result.text,
            result.view_type,
            result.view_payload,
        )
        if interaction is not None:
            return interaction
    return None


def _typed_question_owner(
    normalized_results: list,
    terminal_results: list,
    workflow_state: dict,
) -> bool:
    """Return whether trusted application state owns a customer-facing next question."""

    if _latest_terminal_interaction(terminal_results) is not None:
        return True
    if list(dict(workflow_state.get("constraints") or {}).get("missingPublicFields") or []):
        return True
    # A trusted result always owns a safe conversational continuation. Specific workflow,
    # collection, and suggestion contracts narrow that question; an otherwise informational
    # result owns the generic open follow-up added by presentation normalization.
    return bool(normalized_results)


def _workflow_dialogue_question(
    messages: list[dict],
    normalized_results: list,
    workflow_state: dict,
    question_disposition: str,
    created_at_state_version: int,
    *,
    active_question=None,
) -> dict | None:
    """Describe the workflow question from typed state, never from customer phrasing."""

    if not messages or messages[-1].get("purpose") not in _QUESTION_PURPOSES:
        return None
    # An informational interruption does not answer, close, or replace the current workflow
    # question. The reducer retains it and the commit boundary restores its visible prompt.
    if question_disposition == "preserve":
        return None
    if question_disposition == "refresh":
        if active_question is None or active_question.kind != "reference_choice":
            return None
        namespace = _single_reference_namespace(active_question.candidateReferences)
        if namespace is None:
            return None
        candidate_references = _candidate_references_for_namespace(
            normalized_results,
            namespace,
        )
        if not candidate_references:
            return None
        message_id = str(messages[-1].get("id") or uuid.uuid4())
        messages[-1]["id"] = message_id
        return {
            "questionId": f"question-{uuid.uuid4().hex[:16]}",
            "kind": "reference_choice",
            "prompt": str(messages[-1].get("text") or "").strip(),
            "goalIntent": active_question.goalIntent,
            "candidateReferences": candidate_references,
            "candidateIntents": [],
            "expectedFields": list(active_question.expectedFields),
            "originatingMessageId": message_id,
            "createdAtStateVersion": created_at_state_version,
        }
    constraints = dict(workflow_state.get("constraints") or {})
    missing_fields = [
        str(field) for field in constraints.get("missingPublicFields") or [] if str(field)
    ][:8]
    expected_fields = [
        str(field)
        for field in constraints.get("acceptedInputFields") or missing_fields
        if str(field)
    ][:8]
    active_workflow = str(workflow_state.get("activeWorkflow") or "")
    if not missing_fields or not active_workflow:
        return None
    goal_intent = CapabilityRegistry.goal_intent_for_active_workflow(active_workflow)
    expected_namespace = REFERENCE_FIELD_NAMESPACES.get(missing_fields[0])
    candidate_references = []
    if expected_namespace is not None:
        # A finite-choice result owns its answer set.  Persist that exact set instead of
        # reconstructing it from every entity that happened to be present in the turn.  The
        # entity projection remains the compatibility path for older non-obligation results.
        candidate_references = _candidate_references_for_namespace(
            normalized_results,
            expected_namespace,
        )
    message_id = str(messages[-1].get("id") or uuid.uuid4())
    messages[-1]["id"] = message_id
    return {
        "questionId": f"question-{uuid.uuid4().hex[:16]}",
        "kind": "reference_choice" if candidate_references else "input",
        "prompt": str(messages[-1].get("text") or "").strip(),
        "goalIntent": goal_intent,
        "candidateReferences": candidate_references,
        "candidateIntents": [],
        "expectedFields": expected_fields,
        "originatingMessageId": message_id,
        "createdAtStateVersion": created_at_state_version,
    }


def _application_fallback_text(
    results: list,
    workflow_state: dict,
) -> str:
    """Render a safe next step from trusted results without model-authored business values."""

    latest = results[-1] if results else None
    if latest is None:
        return "I found the requested information."

    payload = latest.view_payload if isinstance(latest.view_payload, dict) else {}
    items = list(payload.get("items") or [])
    constraints = dict(workflow_state.get("constraints") or {})
    missing = set(constraints.get("missingPublicFields") or [])
    resolution = constraints.get("resolution")
    continuation = (
        str(resolution.get("continuation") or "") if isinstance(resolution, dict) else ""
    )

    # Workflow state, not a renderer/view type, owns the next question. Some valid transition
    # results intentionally have no public view, so gating these prompts on ``slot_list`` can turn
    # an active transaction into the unrelated global continuation.
    if continuation == "request_schedule_preferences":
        subject = (
            "The workshop service and location are selected."
            if workflow_state.get("activeWorkflow") == "workshop_booking"
            else "The vehicle and dealership are selected."
        )
        return f"{subject} What day or date would suit you, and approximately what time?"
    if continuation == "request_alternative_schedule":
        return (
            "There are no appointment times matching that preference. "
            "What different day or date and approximate time would work?"
        )
    if continuation == "choose_alternative_location":
        return (
            "No appointments are currently shown at the selected location. "
            "Which available alternative location would you prefer?"
        )
    if continuation == "request_vehicle_selection":
        return "Which available vehicle would you like to use for the appointment?"

    if latest.view_type in {"slot_list", "test_drive_slot_picker"}:
        if not items:
            if continuation == "offer_vehicle_alternatives":
                return (
                    "No online test-drive times are currently available for this vehicle. "
                    "You can choose another vehicle or ask the dealership to contact you."
                )
            if continuation == "offer_workshop_contact":
                return (
                    "No online workshop appointments are currently shown for that request. "
                    "You can choose another location or ask the dealership to contact you."
                )
            base = str(latest.text or "No appointments are currently available.").strip()
            return f"{base} Please choose one of the available alternatives."
        if continuation == "offer_schedule_alternatives":
            return (
                "Nothing matched the requested day or time. Here are the next available "
                "appointment times at the selected location. Which one would you like?"
            )
        if continuation == "offer_location_alternatives":
            first = items[0] if isinstance(items[0], dict) else {}
            location = str(
                first.get("dealershipName") or first.get("dealershipTown") or "another dealership"
            ).strip()
            if workflow_state.get("activeWorkflow") == "workshop_booking":
                return (
                    "The selected workshop has no appointment matching that preference. "
                    f"Here are matching appointments at {location}. Which one would you like?"
                )
            return (
                "No test-drive times are available at the requested dealership. "
                f"The next live option for this vehicle is at {location}. "
                "Would you like that appointment?"
            )
        if continuation == "offer_location_and_schedule_alternatives":
            return (
                "The requested workshop and schedule have no matching appointment. "
                "Here are the next available times at other workshops. Which one would you like?"
            )
        if {"preferredDayOrDate", "approximateTime"}.issubset(missing):
            first = items[0] if isinstance(items[0], dict) else {}
            location = str(first.get("dealershipTown") or first.get("dealershipName") or "").strip()
            prefix = (
                f"{location} has workshop availability."
                if location
                else "Workshop appointments are available."
            )
            return f"{prefix} What day or date would suit you, and approximately what time?"
        if "slotId" in missing:
            return "Here are the available appointment times. Which one would you like?"

    return str(latest.text or "I found the requested information.").strip()


def _persisted_message_view(message) -> tuple[str | None, str | None]:
    """Return a trusted persisted view only when its payload is still a JSON object."""

    if message is None or not message.view_type or not message.view_payload_json:
        return None, None
    try:
        payload = json.loads(message.view_payload_json)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return message.view_type, json.dumps(payload, separators=(",", ":"))


def _persisted_choice_view_owns_field(message, field: str) -> bool:
    """Accept a recovery view only when its own typed contract targets the missing field."""

    if message is None or not message.view_type or not message.view_payload_json:
        return False
    try:
        payload = json.loads(message.view_payload_json)
    except (TypeError, ValueError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("selectionOnly") is True
        and payload.get("choiceField") == field
    )


def _retained_question_choice_view(state, messages) -> tuple[str, dict] | None:
    """Replay the exact application-owned choices when a turn does not answer them."""

    question = state.dialogue.activeQuestion
    if question is None or not question.expectedFields:
        return None
    source = next(
        (message for message in messages if message.id == question.originatingMessageId),
        None,
    )
    field = str(question.expectedFields[0])
    if not _persisted_choice_view_owns_field(source, field):
        return None
    view_type, view_payload = _persisted_message_view(source)
    if view_type is None or view_payload is None:
        return None
    return view_type, json.loads(view_payload)


def _repeat_pending_view(interaction: PendingInteraction, message) -> ToolResult | str:
    prompt = (
        "Would you like to confirm this request, cancel it, or change a detail?"
        if interaction.kind == "protected_confirmation"
        else "Which available option would you like?"
    )
    if not message.view_type or not message.view_payload_json:
        return prompt
    try:
        payload = json.loads(message.view_payload_json)
    except (TypeError, ValueError):
        return prompt
    if not isinstance(payload, dict):
        return prompt
    return ToolResult(
        prompt,
        message.view_type,
        payload,
        {"interactionRepeated": True},
        interaction,
    )


_READ_ONLY_CARD_TYPES = frozenset(
    {
        "vehicle_preview",
        "vehicle_comparison",
        "offer",
        "dealership",
        "opening_hours",
        "service",
        "valuation",
    }
)
_VIEW_CARD_TYPES = {
    "vehicle_list": "vehicle_preview",
    "vehicle_details": "vehicle_preview",
    "vehicle_availability": "vehicle_preview",
    "vehicle_comparison": "vehicle_comparison",
    "offer_list": "offer",
    "dealership_list": "dealership",
    "workshop_location_list": "dealership",
    "opening_hours": "opening_hours",
    "service_list": "service",
    "part_exchange_estimate": "valuation",
}
def _read_only_presentation_fingerprint(card_or_view_type: str, data) -> str | None:
    """Identify an unchanged, non-interactive visual independently of its result reference."""

    card_type = _VIEW_CARD_TYPES.get(card_or_view_type, card_or_view_type)
    if card_type not in _READ_ONLY_CARD_TYPES or not isinstance(data, dict):
        return None

    def stable_visual(value):
        if isinstance(value, dict):
            return {
                key: stable_visual(item)
                for key, item in value.items()
                if key != "collectionPresentationRendered"
            }
        if isinstance(value, list):
            return [stable_visual(item) for item in value]
        return value

    canonical = json.dumps(
        {"type": card_type, "data": stable_visual(data)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _visible_read_only_presentations(messages: list) -> set[str]:
    """Return read-only visuals already retained in the conversation history."""

    visible: set[str] = set()
    for message in messages:
        if message.role != "assistant" or not message.view_payload_json:
            continue
        try:
            payload = json.loads(message.view_payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if message.view_type == "grounded_presentation":
            cards = payload.get("cards")
            if not isinstance(cards, list):
                continue
            candidates = (
                (card.get("type"), card.get("data")) for card in cards if isinstance(card, dict)
            )
        else:
            candidates = ((message.view_type, payload),)
        for card_type, data in candidates:
            if not isinstance(card_type, str):
                continue
            fingerprint = _read_only_presentation_fingerprint(card_type, data)
            if fingerprint is not None:
                visible.add(fingerprint)
    return visible


def _turn_requests_result_presentation(
    understanding: TurnUnderstanding | None,
) -> bool:
    """Use only typed conversational meaning to bypass visual reuse."""

    return understanding is not None and understanding.resultPresentation == "explicit_request"


def _turn_references(context: TurnContext) -> TurnReferences:
    return TurnReferences(
        context.displayed_vehicles,
        context.vehicle_search_state,
        context.displayed_offers,
        context.page_vehicles,
        context.displayed_dealerships,
        context.displayed_choices,
    )


_RESULT_SET_KINDS = {"vehicles", "offers", "dealerships", "services", "appointments"}


def _result_kind(tool: str) -> str:
    if tool in {
        "search_vehicles",
        "refine_vehicle_search",
        "get_vehicle",
        "get_vehicle_availability",
        "resolve_vehicle_availability",
        "compare_vehicles",
        "compare_vehicle_models",
        "select_page_vehicles",
    }:
        return "vehicles"
    if tool in {"list_offers", "get_offer"}:
        return "offers"
    if tool in {
        "list_dealerships",
        "list_workshop_locations",
        "list_dealership_departments",
        "list_opening_hours",
        "get_opening_hours",
        "list_holiday_opening_hours",
    }:
        return "dealerships"
    if tool in {"list_service_types", "get_service_information"}:
        return "services"
    if tool in {"list_test_drive_slots", "list_workshop_slots", "refine_workshop_slots"}:
        return "appointments"
    if tool.startswith(("prepare_", "request_", "estimate_part_exchange")):
        return "workflow"
    return "information"


def _is_secure_input_result(result) -> bool:
    return bool(
        result.view_type in {"draft", "private_booking_lookup", "part_exchange_estimate_form"}
        and isinstance(result.view_payload, dict)
        and result.view_payload.get("secureInputReady") is True
    )


def _latest_secure_input(results: list) -> dict | None:
    result = next((item for item in reversed(results) if _is_secure_input_result(item)), None)
    if result is None:
        return None
    payload = _safe_collector_view(result.view_type, result.view_payload) or {}
    return {"version": 1, "sourceViewType": result.view_type, **payload}


def _normalize_secure_input_prompt(messages: list[dict], secure_input: dict | None) -> None:
    """Keep protected collection conversational and limited to its next answer."""

    if not messages or secure_input is None:
        return
    message = messages[-1]
    if message.get("viewType") != "secure_input":
        return
    first_field = next(iter(secure_input.get("secureFields") or []), None)
    if first_field is None:
        return
    question = field_prompt(first_field)
    introductions = {
        "workshop_booking": "Great — that appointment is selected.",
        "booking_lookup": "I can check that securely.",
        "part_exchange_estimate": "I can help with that estimate.",
    }
    introduction = introductions.get(str(secure_input.get("kind") or ""))
    message["text"] = f"{introduction} {question}" if introduction else question
    message["blocks"] = None
    message["segments"] = None


def _latest_slot_context(results: list, normalized_results: list) -> dict | None:
    result_index = next(
        (
            index
            for index in range(len(results) - 1, -1, -1)
            if results[index].view_type in {"slot_list", "test_drive_slot_picker"}
            and isinstance(results[index].view_payload, dict)
        ),
        None,
    )
    if result_index is None:
        return None
    result = results[result_index]
    if isinstance(result.view_payload.get("collectionPresentation"), dict):
        # Appointment choices now use the same application-owned collection boundary as every
        # other finite choice. This remains only as a legacy payload adapter.
        return None
    items = list(result.view_payload.get("items") or [])[:12]
    normalized = (
        normalized_results[result_index] if result_index < len(normalized_results) else None
    )
    obligation = normalized.responseObligation if normalized is not None else None
    candidate_references = (
        list(obligation.candidateReferences)
        if obligation is not None and obligation.choiceMode is not None
        else []
    )
    return {
        "version": 1,
        "sourceViewType": result.view_type,
        "items": items,
        "choiceMode": obligation.choiceMode if obligation is not None else None,
        "candidateReferences": candidate_references,
    }


def _latest_alternative_offer(results: list) -> dict | None:
    offer = next(
        (
            result.alternativeOffer
            for result in reversed(results)
            if result.alternativeOffer is not None
        ),
        None,
    )
    return offer.model_dump(mode="json") if offer is not None else None


def _apply_alternative_disclosure(messages: list[dict], offer: dict | None) -> None:
    """Turn trusted substitution provenance into ordinary conversation once.

    ``AlternativeOffer`` is an orchestration contract, not a browser view. The application owns
    the two operational claims below so a successful substitute never depends on model inference,
    while the existing question and collection continue to own the customer's choice.
    """

    if not messages or offer is None:
        return
    sentences = [
        str(offer.get(field) or "").strip()
        for field in ("failureReason", "offeredOutcome")
        if str(offer.get(field) or "").strip()
    ]
    if not sentences:
        return
    disclosure = " ".join(sentences)
    collection_owner = next(
        (
            message
            for message in messages
            if isinstance(message.get("view"), dict)
            and isinstance(message["view"].get("collectionPresentation"), dict)
        ),
        None,
    )
    owner = collection_owner or messages[-1]
    current_text = str(owner.get("text") or "").strip()
    keep_question = owner is messages[-1] and current_text.endswith("?")
    question = current_text.rsplit("\n\n", 1)[-1] if keep_question else ""
    owner["text"] = f"{disclosure}\n\n{question}" if question else disclosure
    owner["blocks"] = [
        {
            "type": "paragraph",
            "segments": [{"type": "text", "text": disclosure}],
        },
        *(
            [{"type": "paragraph", "segments": [{"type": "text", "text": question}]}]
            if question
            else []
        ),
    ]
    owner["segments"] = None


def _structured_collection_messages(results: list) -> list[dict]:
    messages = []
    for result in results:
        if not _is_structured_collection_result(result):
            continue
        payload = dict(result.view_payload)
        collection = payload["collectionPresentation"]
        purpose = {
            "choice": "workflow_prompt",
            "clarification": "clarification",
            "information": "answer",
        }.get(str(collection.get("purpose") or ""), "answer")
        view_type = (
            "choice_list"
            if payload.get("collectionViewType") == "choice_list"
            else result.view_type
        )
        messages.append(
            {
                "text": result.text,
                "viewType": view_type,
                "view": payload,
                "purpose": purpose,
                "interactionJson": None,
                "segments": None,
            }
        )
    return messages


def _structured_collection_views(
    normalized_results: list,
    terminal_results: list,
) -> dict[str, tuple[str, dict]]:
    """Bind AI-selected collection references to their trusted application views."""

    envelopes = [result for result in normalized_results if result.availableCollections]
    terminals = [result for result in terminal_results if _is_structured_collection_result(result)]
    if len(envelopes) != len(terminals):
        raise ValueError("structured collection result mapping is inconsistent")
    views: dict[str, tuple[str, dict]] = {}
    for envelope, terminal in zip(envelopes, terminals, strict=True):
        if not isinstance(terminal.view_payload, dict) or not terminal.view_type:
            raise ValueError("structured collection is missing its trusted view")
        for collection in envelope.availableCollections:
            views[collection.reference] = (
                collection.viewType,
                dict(terminal.view_payload),
            )
    return views


def _structured_collection_only_turn(normalized_results: list, terminal_results: list) -> bool:
    return (
        bool(terminal_results)
        and len(normalized_results) == len(terminal_results)
        and all(_is_structured_collection_result(result) for result in terminal_results)
    )


def _is_structured_collection_result(result) -> bool:
    return bool(
        isinstance(result.view_payload, dict)
        and isinstance(result.view_payload.get("collectionPresentation"), dict)
    )


def _structured_collection_card_references(results: list) -> set[str]:
    return {
        card.reference
        for result in results
        for card in result.availableCards
        if isinstance(card.data.get("collectionPresentation"), dict)
    }


def _structured_collection_suggestion_references(results: list) -> set[str]:
    return {
        suggestion.reference
        for result in results
        if any(
            isinstance(card.data.get("collectionPresentation"), dict)
            for card in result.availableCards
        )
        for suggestion in result.availableSuggestions
    }


def _safe_collector_view(view_type: str, payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if view_type == "part_exchange_estimate_form":
        return {
            "version": int(payload.get("version") or 1),
            "kind": "part_exchange_estimate",
            "secureFields": list(payload.get("secureFields") or []),
            "secureInputReady": payload.get("secureInputReady") is True,
        }
    return payload


def _confirmation_intent(text: str, action: dict | None) -> str | None:
    action_type = str((action or {}).get("type") or "")
    return explicit_decision(text, action_type or None)


def _confirmation_receipt_text(kind: str) -> str:
    return {
        "test_drive": "Your test drive is booked.",
        "workshop_booking": "Your workshop booking is confirmed.",
        "workshop_amend": "Your workshop booking has been updated.",
        "workshop_cancel": "Your workshop booking has been cancelled.",
        "sales_enquiry": "Your sales enquiry has been sent.",
        "dealership_message": "Your message has been sent.",
        "vehicle_interest": "Your interest has been registered.",
        "callback": "Your callback has been requested.",
        "part_exchange": "Your part-exchange request has been sent.",
    }.get(kind, "Your request has been confirmed.")


def _request_fingerprint(text: str, action: dict | None) -> str:
    canonical = json.dumps(
        {"text": text, "action": action},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_grounding_failure(error: ValueError) -> str:
    """Map hard trust-boundary failures to a bounded composer repair instruction."""

    message = str(error).casefold()
    categories = (
        ("typed owner", "question_requires_typed_continuation"),
        ("navigation", "navigation_confirmation_contract_not_fulfilled"),
        ("private", "private_data_boundary_violated"),
        ("implementation context", "keep_page_and_application_context_internal"),
        ("test-drive availability", "test_drive_slot_evidence_required"),
        ("catalogue item identity", "keep_catalogue_items_in_the_trusted_card"),
        ("vehicle catalogue", "end_vehicle_discovery_with_a_useful_question"),
        ("finite choice", "do_not_duplicate_rendered_choice_details_in_prose"),
        ("critical business values", "untrusted_business_value"),
        ("link", "trusted_link_invalid"),
        ("reference", "trusted_reference_invalid"),
    )
    return next((code for marker, code in categories if marker in message), "grounding_invalid")
