from typing import Any

from webchat.orchestration.contracts.response import (
    BulletSegment,
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    TextSegment,
)

from ..contracts import LlmProvider, ProviderReply, ToolCall
from .planner import DeterministicToolPlanner


class FakeLlmProvider:
    """Deterministic fake provider with the hosted provider's direct-tool boundary."""

    def __init__(self, planner: LlmProvider | None = None) -> None:
        self._planner = planner if planner is not None else DeterministicToolPlanner()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        trusted = getattr(getattr(messages, "planning_context", None), "trusted_tool_facts", None)
        if trusted:
            workflow_state = getattr(
                getattr(messages, "planning_context", None), "workflow_state", {}
            )
            return ProviderReply(
                response_draft=_grounded_demo_draft(trusted, workflow_state),
                text="",
            )
        reply = await self._planner.generate_turn(messages)
        branches = (
            bool(reply.tool_calls)
            + bool(reply.text.strip())
            + bool(reply.interaction_decision)
            + bool(reply.interaction_proposal)
            + bool(reply.response_draft)
        )
        if len(reply.tool_calls) > 4 or branches != 1:
            raise ValueError("fake provider returned an invalid direct proposal")
        return reply


def _grounded_demo_draft(
    trusted: dict[str, Any], workflow_state: dict[str, Any] | None = None
) -> GroundedResponseDraft:
    """Compose a deterministic test response from references, never raw tool prose.

    Hosted mode uses the same model for planning and grounded composition. The test provider is
    deliberately limited, so this copy is intentionally modest while still exercising the exact
    same response schema and server-side grounding boundary.
    """
    results = [item for item in trusted.get("results", []) if isinstance(item, dict)]
    tools = {str(item.get("tool") or "") for item in results}
    cards = [
        str(card["reference"])
        for item in results
        for card in item.get("availableCards", [])
        if isinstance(card, dict) and card.get("reference")
    ]
    suggestions = [
        str(suggestion["reference"])
        for item in results
        for suggestion in item.get("availableSuggestions", [])
        if isinstance(suggestion, dict) and suggestion.get("reference")
    ]
    collections = [
        str(collection["reference"])
        for item in results
        for collection in item.get("availableCollections", [])
        if isinstance(collection, dict) and collection.get("reference")
    ]
    unavailable = any(item.get("status") == "unavailable" for item in results)
    constraints = dict((workflow_state or {}).get("constraints") or {})
    requested = list(constraints.get("missingPublicFields") or [])[:1]
    if not requested and constraints.get("secureInputReady") is True:
        requested = list(constraints.get("secureFields") or [])[:1]
    broad_appointment = "preferredDayOrDate" in requested
    navigation_confirmation = bool(trusted.get("navigationConfirmationActivation"))
    selection_only = any(
        fact.get("field") == "selectionOnly" and fact.get("value") is True
        for item in results
        for fact in item.get("facts", [])
        if isinstance(fact, dict)
    )
    obligations = [
        item["responseObligation"]
        for item in results
        if isinstance(item.get("responseObligation"), dict)
    ]
    if navigation_confirmation:
        text = "Would you like me to open the full vehicle details?"
    elif unavailable:
        text = (
            "I don’t have confirmed Northstar information that answers that. "
            "Would you like help contacting a dealership?"
        )
    elif tools.intersection(
        {"search_vehicles", "compare_vehicles", "compare_vehicle_models", "get_vehicle"}
    ):
        text = "Here are the current matching vehicle details. You can refer to an option by number or description."
    elif tools.intersection({"list_offers", "get_offer"}):
        text = "Here are the current published offer details."
    elif tools.intersection({"list_dealerships", "list_workshop_locations"}):
        text = "Here are the matching Northstar locations."
    elif tools.intersection({"list_opening_hours", "get_opening_hours"}):
        text = "Here are the current opening hours."
    elif tools.intersection({"list_service_types", "get_service_information"}):
        text = "Here is the current workshop service information."
    elif selection_only and "list_workshop_slots" in tools:
        text = "Which workshop service would you like to book?"
    elif tools.intersection({"list_test_drive_slots", "list_workshop_slots"}):
        text = "I found the current appointment availability."
    elif any(tool.startswith(("prepare_", "request_")) for tool in tools):
        text = "I can help with that securely."
    else:
        text = "Here are the current Northstar details."
    messages = _obligation_messages(results, obligations)
    if not messages and not cards and not broad_appointment:
        for result in results[:4]:
            facts = [item for item in result.get("facts", []) if isinstance(item, dict)]
            value_facts = [
                item for item in facts if str(item.get("field") or "").endswith(".value")
            ]
            label_by_prefix = {
                str(item.get("field"))[: -len(".label")]: item
                for item in facts
                if str(item.get("field") or "").endswith(".label")
            }
            if not value_facts:
                continue
            segments = [TextSegment(type="text", text=text)]
            for value_fact in value_facts[:3]:
                prefix = str(value_fact.get("field"))[: -len(".value")]
                label_fact = label_by_prefix.get(prefix)
                segments.append(BulletSegment(type="bullet"))
                if label_fact:
                    segments.extend(
                        [
                            FactSegment(type="fact", factId=str(label_fact["factId"])),
                            TextSegment(type="text", text=": "),
                        ]
                    )
                segments.extend(
                    [
                        FactSegment(type="fact", factId=str(value_fact["factId"])),
                    ]
                )
            messages.append(GroundedMessageDraft(purpose="answer", segments=segments))
    if not messages and not requested:
        messages = [
            GroundedMessageDraft(
                purpose="follow_up" if navigation_confirmation else "answer",
                segments=[TextSegment(type="text", text=text)],
            )
        ]
    for index, reference in enumerate(collections):
        if index < len(messages):
            messages[index] = messages[index].model_copy(update={"collectionReference": reference})
        else:
            messages.append(
                GroundedMessageDraft(
                    purpose="answer",
                    segments=[
                        TextSegment(
                            type="text",
                            text="Here are the current Northstar options.",
                        )
                    ],
                    collectionReference=reference,
                )
            )
    if requested:
        messages = messages[:3]
        questions = {
            "preferredDayOrDate": "What day or date would suit you?",
            "approximateTime": "What time of day would suit you?",
            "slotId": "Which available appointment time suits you?",
            "registration": "What’s your vehicle registration?",
            "mileage": "What’s the vehicle’s current mileage?",
            "condition": "How would you describe the vehicle’s condition?",
            "firstName": "What’s your first and last name?",
            "lastName": "What surname is the booking under?",
            "email": "What email address should Northstar use?",
            "phone": "What’s the best phone number to reach you on?",
            "reference": "What’s your booking reference?",
        }
        required_context = list(
            dict.fromkeys(
                str(fact_id)
                for obligation in obligations
                for fact_id in obligation.get("requiredFactIds") or []
            )
        )
        prompt_segments = []
        if required_context:
            prompt_segments.extend(
                [
                    TextSegment(type="text", text="The selected location is "),
                    FactSegment(type="fact", factId=required_context[0]),
                    TextSegment(type="text", text=". "),
                ]
            )
        prompt_segments.append(
            TextSegment(type="text", text=questions.get(requested[0], requested[0]))
        )
        messages.append(
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=prompt_segments,
            )
        )
    return GroundedResponseDraft(
        messages=messages,
        cardReferences=list(dict.fromkeys(cards)),
        suggestionReferences=list(dict.fromkeys(suggestions)),
    )


def _obligation_messages(
    results: list[dict[str, Any]], obligations: list[dict[str, Any]]
) -> list[GroundedMessageDraft]:
    if not obligations:
        return []
    obligation = obligations[0]
    if obligation.get("kind") == "vehicle_resolution":
        return [
            GroundedMessageDraft(
                purpose="clarification",
                segments=[
                    TextSegment(
                        type="text",
                        text="Which of these current models would you like me to use?",
                    )
                ],
            )
        ]
    if obligation.get("kind") != "vehicle_comparison":
        return []
    result = next(
        (item for item in results if item.get("responseObligation") == obligation),
        None,
    )
    if result is None:
        return []
    facts = [item for item in result.get("facts", []) if isinstance(item, dict)]
    subjects = list(obligation.get("subjectReferences") or [])
    segments = [TextSegment(type="text", text="The clearest trade-offs are ")]
    dimensions_added = 0
    labels = {
        "pricePence": "price",
        "monthlyPricePence": "monthly cost",
        "mileage": "mileage",
        "fuelType": "fuel type",
        "transmission": "gearbox",
        "bodyStyle": "body style",
        "year": "year",
    }
    for field in obligation.get("allowedComparisonFields") or []:
        matching = [
            fact
            for subject in subjects
            for fact in facts
            if fact.get("entityReference") == subject
            and str(fact.get("field") or "").casefold().endswith(f".{str(field).casefold()}")
        ]
        if len(matching) < 2:
            continue
        if dimensions_added:
            segments.append(TextSegment(type="text", text=f" For {labels.get(field, field)}, "))
        else:
            segments.append(TextSegment(type="text", text=f"{labels.get(field, field)}: "))
        for index, fact in enumerate(matching):
            if index:
                segments.append(
                    TextSegment(
                        type="text",
                        text=" and " if index == len(matching) - 1 else ", ",
                    )
                )
            segments.append(FactSegment(type="fact", factId=str(fact["factId"])))
        segments.extend(
            [
                TextSegment(type="text", text="."),
            ]
        )
        dimensions_added += 1
        if dimensions_added >= max(2, int(obligation.get("minimumComparedDimensions") or 0)):
            break
    for fact_id in obligation.get("requiredFactIds") or []:
        segments.extend(
            [
                TextSegment(type="text", text=" The selected comparison basis is "),
                FactSegment(type="fact", factId=str(fact_id)),
                TextSegment(type="text", text="."),
            ]
        )
    has_test_drive_choices = any(
        str((item.get("action") or {}).get("type") or "") == "select_test_drive_vehicle"
        for item in result.get("availableSuggestions", [])
        if isinstance(item, dict)
    )
    return [
        GroundedMessageDraft(purpose="comparison", segments=segments),
        GroundedMessageDraft(
            purpose="follow_up",
            segments=[
                TextSegment(
                    type="text",
                    text=(
                        "Which compared vehicle would you like to book a test drive for?"
                        if has_test_drive_choices
                        else "Which compared vehicle would you like to explore further?"
                    ),
                )
            ],
        ),
    ]


__all__ = ["FakeLlmProvider", "LlmProvider", "ProviderReply", "ToolCall"]
