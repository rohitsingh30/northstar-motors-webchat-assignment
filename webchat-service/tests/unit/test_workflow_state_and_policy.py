import pytest

from webchat.integrations.contracts import ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.policy import ToolPolicyGate
from webchat.orchestration.provider_loop import TurnReferences
from webchat.orchestration.state import WorkflowStateReducer, normalize_workflow_state


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError


def references(
    *, vehicles=None, page=None, offers=None, dealerships=None, vehicle_search_state=None
) -> TurnReferences:
    return TurnReferences(
        displayed_vehicles=vehicles or [],
        vehicle_search_state=vehicle_search_state,
        displayed_offers=offers or [],
        page_vehicles=page or [],
        displayed_dealerships=dealerships or [],
    )


@pytest.fixture
def policy() -> ToolPolicyGate:
    return ToolPolicyGate(UnifiedToolCatalog(NeverExecute()))


def test_page_filter_can_use_only_trusted_page_vehicle_ids(policy: ToolPolicyGate) -> None:
    call = ToolCall(
        "page",
        "select_page_vehicles",
        {"vehicleIds": ["veh-002", "veh-003"], "sort": "mileageAsc", "limit": 1},
    )

    decision = policy.validate(
        [call],
        state={},
        references=references(page=[{"vehicleId": "veh-002"}, {"vehicleId": "veh-003"}]),
    )

    assert decision.calls == [call]


def test_vehicle_search_refinement_reuses_server_owned_filters(policy: ToolPolicyGate) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "sort",
                "refine_vehicle_search",
                {"sort": "priceAsc"},
            )
        ],
        state={},
        references=references(
            vehicle_search_state={
                "filters": {
                    "bodyStyle": "SUV",
                    "fuelType": "Petrol",
                    "transmission": "Automatic",
                    "maxMileage": 35_000,
                },
                "page": 2,
            }
        ),
    )

    assert decision.calls[0].arguments == {
        "bodyStyle": "SUV",
        "fuelType": "Petrol",
        "transmission": "Automatic",
        "maxMileage": 35_000,
        "sort": "priceAsc",
    }


def test_vehicle_search_exclusion_replaces_conflicting_positive_filter(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "exclude",
                "refine_vehicle_search",
                {"excludedMakes": ["Land Rover"]},
            )
        ],
        state={},
        references=references(
            vehicle_search_state={
                "filters": {
                    "make": "Land Rover",
                    "bodyStyle": "SUV",
                    "fuelType": "Hybrid",
                    "maxPricePence": 4_500_000,
                },
                "page": 1,
            }
        ),
    )

    assert decision.calls[0].arguments == {
        "bodyStyle": "SUV",
        "fuelType": "Hybrid",
        "maxPricePence": 4_500_000,
        "excludedMakes": ["Land Rover"],
    }


def test_positive_filter_replaces_previous_exclusion(policy: ToolPolicyGate) -> None:
    decision = policy.validate(
        [ToolCall("include", "refine_vehicle_search", {"make": "Volvo"})],
        state={},
        references=references(
            vehicle_search_state={
                "filters": {
                    "bodyStyle": "SUV",
                    "excludedMakes": ["Volvo", "BMW"],
                },
                "page": 1,
            }
        ),
    )

    assert decision.calls[0].arguments == {"bodyStyle": "SUV", "make": "Volvo"}


def test_workshop_refinement_preserves_service_when_location_changes(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "location",
                "refine_workshop_slots",
                {"dealershipTown": "Bolton"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_time",
            "entities": {"serviceTypeId": "interim-service"},
            "constraints": {},
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {
        "serviceTypeId": "interim-service",
        "dealershipTown": "Bolton",
    }


def test_workshop_refinement_replaces_service_without_retaining_old_id(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "service",
                "refine_workshop_slots",
                {"serviceTypeName": "tyre"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_time",
            "entities": {"serviceTypeId": "interim-service"},
            "constraints": {"dealershipTown": "Stockport"},
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {
        "dealershipTown": "Stockport",
        "serviceTypeName": "tyre",
    }


def test_page_filter_rejects_ids_outside_page_context(policy: ToolPolicyGate) -> None:
    with pytest.raises(ValueError, match="untrusted ID"):
        policy.validate(
            [
                ToolCall(
                    "page",
                    "select_page_vehicles",
                    {"vehicleIds": ["veh-999"], "sort": "priceAsc"},
                )
            ],
            state={},
            references=references(page=[{"vehicleId": "veh-002"}]),
        )


def test_comparison_accepts_only_displayed_or_page_vehicle_ids(policy: ToolPolicyGate) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "compare",
                "compare_vehicles",
                {"vehicleIds": ["veh-001", "veh-025"]},
            )
        ],
        state={},
        references=references(vehicles=[{"vehicleId": "veh-001"}, {"vehicleId": "veh-025"}]),
    )

    assert decision.calls[0].arguments["vehicleIds"] == ["veh-001", "veh-025"]


def test_opening_hours_list_preserves_multi_dealership_scope(
    policy: ToolPolicyGate,
) -> None:
    call = ToolCall("hours", "list_opening_hours", {"day": "Monday"})
    decision = policy.validate(
        [call],
        state={},
        references=references(
            dealerships=[
                {"dealershipId": "dealer-bolton", "town": "Bolton"},
                {"dealershipId": "dealer-manchester", "town": "Manchester"},
            ]
        ),
    )

    assert decision.calls == [call]
    assert decision.clarification == ""


def test_singular_hours_tool_cannot_select_one_of_many_dealerships(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("hours", "get_opening_hours", {"id": "dealer-manchester"})],
        state={},
        references=references(
            dealerships=[
                {"dealershipId": "dealer-bolton", "town": "Bolton"},
                {"dealershipId": "dealer-manchester", "town": "Manchester"},
            ]
        ),
    )

    assert decision.calls == []
    assert decision.clarification == "Which dealership do you mean: Bolton, Manchester?"


def test_hours_list_cannot_collapse_many_dealerships_with_one_arbitrary_id(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "hours",
                "list_opening_hours",
                {"dealershipId": "dealer-manchester"},
            )
        ],
        state={},
        references=references(
            dealerships=[
                {"dealershipId": "dealer-bolton", "town": "Bolton"},
                {"dealershipId": "dealer-manchester", "town": "Manchester"},
            ]
        ),
    )

    assert decision.calls == []
    assert decision.clarification == "Which dealership do you mean: Bolton, Manchester?"


def test_opening_hours_follow_up_reuses_one_displayed_dealership(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("hours", "list_opening_hours", {"day": "Monday"})],
        state={},
        references=references(
            dealerships=[{"dealershipId": "dealer-manchester", "town": "Manchester"}]
        ),
    )

    assert decision.calls[0].arguments == {
        "day": "Monday",
        "dealershipId": "dealer-manchester",
    }


def test_single_dealership_department_query_reuses_one_displayed_location(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("departments", "find_dealership_departments", {})],
        state={},
        references=references(
            dealerships=[{"dealershipId": "dealer-stockport", "town": "Stockport"}]
        ),
    )

    assert decision.calls[0].arguments == {"dealershipId": "dealer-stockport"}


def test_single_dealership_department_query_requires_a_location_without_context(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("departments", "find_dealership_departments", {})],
        state={},
        references=references(),
    )

    assert decision.calls == []
    assert decision.clarification == "Which dealership do you mean?"


def test_catalogue_rejects_unknown_and_confirmed_write_tools(policy: ToolPolicyGate) -> None:
    with pytest.raises(ValueError, match="Unknown or disallowed"):
        policy.validate(
            [ToolCall("unsafe", "delete_everything", {})],
            state={},
            references=references(),
        )


def test_existing_booking_draft_requires_verified_workflow_state(
    policy: ToolPolicyGate,
) -> None:
    call = ToolCall("amend", "prepare_workshop_amendment", {"notes": "Change time"})

    with pytest.raises(ValueError, match="must be verified"):
        policy.validate([call], state={}, references=references())

    decision = policy.validate(
        [call],
        state={
            "version": 3,
            "activeWorkflow": "existing_workshop_booking",
            "stage": "verified",
            "entities": {},
            "constraints": {},
        },
        references=references(),
    )
    assert decision.calls == [call]


def test_legacy_state_is_upgraded_without_retaining_domain_goal() -> None:
    state = normalize_workflow_state(
        {
            "version": 2,
            "domain": "workshop",
            "goal": "book_service",
            "stage": "choosing_time",
            "entities": {"serviceTypeId": "mot"},
            "constraints": {"dealershipId": "dealer-bolton"},
        }
    )

    assert state == {
        "version": 3,
        "activeWorkflow": "workshop",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "mot"},
        "constraints": {"dealershipId": "dealer-bolton"},
    }


def test_state_is_derived_from_executed_search_arguments() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "search_vehicles",
        {"make": "BMW", "maxPricePence": 4_000_000, "page": 2},
        "vehicle_list",
        {"items": []},
    )

    assert state["version"] == 3
    assert state["activeWorkflow"] == "vehicle_search"
    assert state["constraints"] == {"make": "BMW", "maxPricePence": 4_000_000}
    assert state["lastTool"] == "search_vehicles"


def test_new_preference_start_clears_previous_search_constraints() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "vehicle_search",
        "stage": "viewing",
        "entities": {"vehicleId": "veh-001"},
        "constraints": {"maxPricePence": 3_500_000, "sort": "priceAsc"},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "show_vehicle_preferences",
        {"dimension": "startingPoint"},
        "suggestion_list",
    )

    assert state["entities"] == {}
    assert state["constraints"] == {"preferenceDimension": "startingPoint"}


def test_explicit_vehicle_reset_clears_every_previous_constraint() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "vehicle_search",
        "stage": "viewing",
        "entities": {},
        "constraints": {"fuelType": "Hybrid", "make": "BMW"},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "reset_vehicle_search",
        {},
        "vehicle_list",
    )

    assert state["activeWorkflow"] == "vehicle_search"
    assert state["constraints"] == {}


def test_specific_preference_chooser_preserves_current_search_constraints() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "vehicle_search",
        "stage": "viewing",
        "entities": {},
        "constraints": {"bodyStyle": "SUV"},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "show_vehicle_preferences",
        {"dimension": "fuelTypes", "reuseCurrentSearch": True},
        "suggestion_list",
    )

    assert state["constraints"] == {
        "bodyStyle": "SUV",
        "preferenceDimension": "fuelTypes",
    }


def test_specific_preference_chooser_starts_fresh_without_explicit_reuse() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "vehicle_search",
        "stage": "viewing",
        "entities": {},
        "constraints": {"bodyStyle": "SUV", "maxPricePence": 3_500_000},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "show_vehicle_preferences",
        {"dimension": "fuelTypes"},
        "suggestion_list",
    )

    assert state["constraints"] == {"preferenceDimension": "fuelTypes"}


def test_new_workshop_search_does_not_inherit_unrelated_vehicle_constraints() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "vehicle_search",
        "stage": "viewing",
        "entities": {"vehicleId": "veh-001"},
        "constraints": {"maxPricePence": 3_500_000, "sort": "priceAsc"},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "list_workshop_slots",
        {"serviceTypeName": "Interim service", "dealershipTown": "Stockport"},
        "slot_list",
    )

    assert state["activeWorkflow"] == "workshop_booking"
    assert state["entities"] == {}
    assert state["constraints"] == {
        "dealershipTown": "Stockport",
        "serviceTypeName": "Interim service",
    }


def test_workshop_refinement_state_contains_merged_replacement_arguments() -> None:
    previous = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "interim-service"},
        "constraints": {"dealershipTown": "Stockport"},
    }

    state = WorkflowStateReducer().advance(
        previous,
        "refine_workshop_slots",
        {"dealershipTown": "Stockport", "serviceTypeName": "tyre"},
        "slot_list",
    )

    assert state["stage"] == "choosing_time"
    assert state["entities"] == {}
    assert state["constraints"] == {
        "dealershipTown": "Stockport",
        "serviceTypeName": "tyre",
    }


def test_resolved_service_identity_is_saved_from_live_tool_facts() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "get_service_information",
        {"q": "tyres"},
        None,
        {"service": {"id": "tyre-fitting", "name": "Tyre fitting"}},
    )

    assert state["entities"] == {
        "serviceTypeId": "tyre-fitting",
        "serviceTypeName": "Tyre fitting",
    }


def test_draft_state_never_claims_confirmation() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "prepare_callback",
        {"reason": "Please call"},
        "draft",
        {},
    )

    assert state["activeWorkflow"] == "callback"
    assert state["stage"] == "collecting"


@pytest.mark.parametrize(
    "tool_name",
    [
        "prepare_callback",
        "prepare_sales_enquiry",
        "prepare_dealership_message",
        "prepare_part_exchange",
    ],
)
def test_dealership_forms_reuse_latest_trusted_location(
    policy: ToolPolicyGate, tool_name: str
) -> None:
    decision = policy.validate(
        [ToolCall("form", tool_name, {})],
        state={
            "version": 3,
            "activeWorkflow": "vehicle_search",
            "stage": "viewing",
            "entities": {},
            "constraints": {"make": "Volvo", "dealershipTown": "Stockport"},
        },
        references=references(),
    )

    assert decision.calls[0].arguments["dealershipTown"] == "Stockport"


def test_generic_message_form_reuses_location_without_inventing_message_content(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("form", "prepare_dealership_message", {})],
        state={
            "version": 3,
            "activeWorkflow": "dealership_information",
            "stage": "viewing",
            "entities": {},
            "constraints": {"town": "Stockport"},
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {"dealershipTown": "Stockport"}


def test_dealership_message_schema_distinguishes_content_from_workflow_intent() -> None:
    definition = UnifiedToolCatalog(NeverExecute()).get("prepare_dealership_message")
    properties = definition.input_schema["properties"]

    assert set(properties) == {
        "dealershipId",
        "dealershipTown",
        "department",
        "subject",
        "message",
        "preferredContactMethod",
        "firstName",
        "lastName",
        "email",
        "phone",
    }
    assert "Workflow intent" in properties["message"]["description"]


@pytest.mark.parametrize(
    ("tool_name", "field_name", "description_fragment"),
    [
        ("prepare_sales_enquiry", "message", "customer-authored"),
        ("prepare_callback", "reason", "not their generic request"),
        ("prepare_workshop_booking", "notes", "omit when no notes"),
        ("prepare_vehicle_interest", "notes", "omit when no notes"),
        ("prepare_workshop_amendment", "notes", "omit when no notes"),
    ],
)
def test_all_descriptive_draft_fields_declare_customer_context_provenance(
    tool_name: str, field_name: str, description_fragment: str
) -> None:
    definition = UnifiedToolCatalog(NeverExecute()).get(tool_name)

    assert description_fragment in definition.input_schema["properties"][field_name]["description"]


def test_explicit_form_location_is_not_overridden_by_previous_context(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "form",
                "prepare_callback",
                {"dealershipTown": "Liverpool", "reason": "Please call"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "vehicle_search",
            "stage": "viewing",
            "entities": {},
            "constraints": {"dealershipTown": "Stockport"},
        },
        references=references(),
    )

    assert decision.calls[0].arguments["dealershipTown"] == "Liverpool"


def test_callback_prefill_recovers_a_relevant_topic_omitted_by_the_provider(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("form", "prepare_callback", {})],
        state={},
        references=references(),
        latest_customer_message="please have a dealership call me",
        recent_customer_messages=("Will you pick up my car?", "yes"),
    )

    assert decision.calls[0].arguments == {
        "department": "sales",
        "reason": "Vehicle collection or home pickup",
    }
