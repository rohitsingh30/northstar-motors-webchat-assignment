import pytest

from webchat.domain.capabilities import CapabilityRegistry
from webchat.integrations.contracts import ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.semantics import ResolvedInput, TurnUnderstanding
from webchat.orchestration.policy import ToolPolicyGate
from webchat.orchestration.provider_loop import TurnReferences
from webchat.orchestration.state import WorkflowStateReducer, normalize_workflow_state
from webchat.orchestration.workflow_kernel import (
    test_drive_slot_resolution as _test_drive_slot_resolution,
)
from webchat.orchestration.workflow_kernel import (
    test_drive_transition as _test_drive_transition,
)


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError


def references(
    *,
    vehicles=None,
    page=None,
    offers=None,
    dealerships=None,
    choices=None,
    vehicle_search_state=None,
) -> TurnReferences:
    return TurnReferences(
        displayed_vehicles=vehicles or [],
        vehicle_search_state=vehicle_search_state,
        displayed_offers=offers or [],
        page_vehicles=page or [],
        displayed_dealerships=dealerships or [],
        displayed_choices=choices or [],
    )


@pytest.fixture
def policy() -> ToolPolicyGate:
    return ToolPolicyGate(UnifiedToolCatalog(NeverExecute()))


@pytest.mark.parametrize(
    ("kind", "field", "expected"),
    [
        ("test_drive", "email", "email"),
        ("workshop_booking", "registration", "registration"),
        ("sales_enquiry", "phone", "phone"),
        ("vehicle_interest", "firstName", "fullName"),
        ("callback", "fullName", "fullName"),
        ("dealership_message", "lastName", "lastName"),
        ("part_exchange", "condition", "condition"),
        ("booking_lookup", "lastName", "lastName"),
    ],
)
def test_every_protected_workflow_uses_the_shared_semantic_edit_field_contract(
    kind: str, field: str, expected: str
) -> None:
    assert CapabilityRegistry.secure_edit_field(kind, [field]) == expected


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


def test_policy_does_not_reinterpret_a_provider_catalogue_call(
    policy: ToolPolicyGate,
) -> None:
    call = ToolCall("service-choice", "list_service_types", {})
    decision = policy.validate(
        [call],
        state={},
        references=references(),
        latest_customer_message="Book a workshop appointment",
    )

    assert decision.calls == [call]


def test_policy_compiles_resolved_customer_input_over_planner_paraphrase(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="interrupt_with_information_request",
        goalRelation="new",
        intentKinds=["business_information"],
        resolvedInputs=[
            ResolvedInput(
                field="question",
                value="Will you pick up the car?",
                sourceContextId="message:customer-1",
                sourceText="Will you pick up the car?",
            )
        ],
        confidence="medium",
    )

    decision = policy.validate(
        [
            ToolCall(
                "business-information",
                "get_business_information",
                {
                    "topic": "general",
                    "question": "Do you charge for vehicle collection?",
                },
                intent_kind="business_information",
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments["question"] == "Will you pick up the car?"


def test_policy_compiles_multi_make_understanding_without_dropping_a_make(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["vehicle_search"],
        resolvedInputs=[
            ResolvedInput(
                field="make",
                value=["BMW", "Volvo"],
                sourceContextId="message:customer-1",
                sourceText="BMW and Volvo",
            )
        ],
        confidence="high",
    )

    decision = policy.validate(
        [
            ToolCall(
                "multi-make",
                "search_vehicles",
                {"make": "BMW", "sort": "newest"},
                intent_kind="vehicle_search",
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {
        "makes": ["BMW", "Volvo"],
        "sort": "newest",
    }


def test_policy_maps_equivalent_semantic_location_field_to_selected_tool(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["dealership_discovery"],
        resolvedInputs=[
            ResolvedInput(
                field="dealershipTown",
                value="Bolton",
                sourceContextId="message:customer-1",
                sourceText="Bolton",
            )
        ],
        confidence="high",
    )

    decision = policy.validate(
        [
            ToolCall(
                "find-dealership",
                "list_dealerships",
                {},
                intent_kind="dealership_discovery",
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {"town": "Bolton"}


def test_explicit_service_catalogue_request_remains_informational(
    policy: ToolPolicyGate,
) -> None:
    call = ToolCall("catalogue", "list_service_types", {})
    decision = policy.validate(
        [call],
        state={},
        references=references(),
        latest_customer_message="What workshop services do you offer?",
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


@pytest.mark.parametrize(
    ("current", "correction", "expected"),
    [
        (
            {"maxPricePence": 2_500_000, "sort": "priceAsc"},
            {"minPricePence": 4_000_000, "sort": "priceDesc"},
            {"minPricePence": 4_000_000, "sort": "priceDesc"},
        ),
        (
            {"maxMileage": 10_000, "sort": "mileageAsc"},
            {"minMileage": 20_000, "sort": "mileageDesc"},
            {"minMileage": 20_000, "sort": "mileageDesc"},
        ),
        (
            {"minYear": 2025, "sort": "priceAsc"},
            {"maxYear": 2024},
            {"maxYear": 2024, "sort": "priceAsc"},
        ),
    ],
)
def test_directional_vehicle_corrections_replace_the_opposing_bound(
    policy: ToolPolicyGate,
    current: dict,
    correction: dict,
    expected: dict,
) -> None:
    decision = policy.validate(
        [ToolCall("correction", "refine_vehicle_search", correction)],
        state={},
        references=references(vehicle_search_state={"filters": current, "page": 1}),
    )

    assert decision.calls[0].arguments == expected


def test_contradictory_vehicle_ranges_are_rejected_before_execution(
    policy: ToolPolicyGate,
) -> None:
    with pytest.raises(ValueError, match="price limits conflict"):
        policy.validate(
            [
                ToolCall(
                    "contradiction",
                    "search_vehicles",
                    {"minPricePence": 3_000_000, "maxPricePence": 2_000_000},
                )
            ],
            state={},
            references=references(),
        )


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


def test_workshop_service_candidate_is_allowed_during_service_choice(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "service",
                "refine_workshop_slots",
                {"serviceTypeName": "brakes"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_service",
            "entities": {},
            "constraints": {},
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {"serviceTypeName": "brakes"}


def test_information_interruption_preserves_the_active_transaction() -> None:
    current = {
        "version": 3,
        "activeWorkflow": "sales_enquiry",
        "stage": "collecting",
        "entities": {"vehicleId": "veh-001"},
        "constraints": {"missingPublicFields": ["dealershipId"]},
    }

    result = WorkflowStateReducer().advance(
        current,
        "list_opening_hours",
        {"town": "Manchester"},
        "opening_hours",
        {"items": []},
    )

    assert result["activeWorkflow"] == "sales_enquiry"
    assert result["entities"] == {"vehicleId": "veh-001"}
    assert result["lastInterruptionTool"] == "list_opening_hours"


def test_competing_subject_browse_pauses_the_transaction() -> None:
    current = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-036"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    understanding = TurnUnderstanding(
        dialogueAct="interrupt_with_information_request",
        goalRelation="unrelated",
        intentKinds=["vehicle_search"],
        resolvedInputs=[
            ResolvedInput(
                field="make",
                value="Volvo",
                sourceContextId="message:customer",
                sourceText="Volvo SUVs",
            ),
            ResolvedInput(
                field="bodyStyle",
                value="SUV",
                sourceContextId="message:customer",
                sourceText="Volvo SUVs",
            ),
        ],
        confidence="high",
    )
    facts = {"items": [{"id": "veh-049", "make": "Volvo", "model": "XC40"}]}

    transition = WorkflowStateReducer.execution_transition(
        current,
        "vehicleId",
        facts,
        understanding,
    )
    result = WorkflowStateReducer().advance(
        current,
        "search_vehicles",
        {"make": "Volvo", "bodyStyle": "SUV"},
        "vehicle_list",
        facts,
        transition=transition,
    )

    assert transition == "branch"
    assert result["activeWorkflow"] == "vehicle_search"
    assert result["constraints"] == {"make": "Volvo", "bodyStyle": "SUV"}
    assert result["pausedWorkflow"] == current


def test_active_goal_vehicle_resolution_does_not_pause_its_transaction() -> None:
    current = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_vehicle",
        "entities": {"vehicleId": "veh-036"},
        "constraints": {"missingPublicFields": ["vehicleId"]},
    }
    understanding = TurnUnderstanding(
        dialogueAct="modify_goal",
        goalRelation="active",
        intentKinds=["test_drive"],
        confidence="high",
    )

    assert WorkflowStateReducer.execution_transition(
        current,
        "vehicleId",
        {"items": [{"id": "veh-049"}]},
        understanding,
    ) == "auto"


def test_same_selected_subject_detail_does_not_open_a_competing_branch() -> None:
    current = {
        "version": 3,
        "activeWorkflow": "callback",
        "stage": "collecting",
        "entities": {"dealershipId": "northstar-bolton"},
        "constraints": {"missingPublicFields": ["department"]},
    }
    understanding = TurnUnderstanding(
        dialogueAct="interrupt_with_information_request",
        goalRelation="active",
        intentKinds=["dealership_detail"],
        confidence="high",
    )

    assert WorkflowStateReducer.execution_transition(
        current,
        "dealershipId",
        {"items": [{"id": "northstar-bolton"}]},
        understanding,
    ) == "auto"


def test_candidate_branch_can_continue_or_resume_the_paused_transaction() -> None:
    paused_test_drive = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-036"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    branch = WorkflowStateReducer().advance(
        paused_test_drive,
        "search_vehicles",
        {"make": "Volvo", "bodyStyle": "SUV"},
        "vehicle_list",
        {"items": [{"id": "veh-049"}]},
        transition="branch",
    )

    refined = WorkflowStateReducer().advance(
        branch,
        "refine_vehicle_search",
        {"make": "Volvo", "bodyStyle": "SUV", "page": 2},
        "vehicle_list",
        {"items": [{"id": "veh-061"}]},
    )
    resumed = WorkflowStateReducer().advance(
        refined,
        "resume_paused_capability",
        {},
        None,
    )

    assert refined["activeWorkflow"] == "vehicle_search"
    assert refined["constraints"] == {"make": "Volvo", "bodyStyle": "SUV"}
    assert refined["pausedWorkflow"] == paused_test_drive
    assert resumed == paused_test_drive


def test_new_subject_replaces_a_paused_attempt_of_the_same_transaction() -> None:
    paused_test_drive = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-036"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    branch = WorkflowStateReducer().advance(
        paused_test_drive,
        "search_vehicles",
        {"make": "Volvo", "bodyStyle": "SUV"},
        "vehicle_list",
        {"items": [{"id": "veh-049"}]},
        transition="branch",
    )

    replacement = WorkflowStateReducer().advance(
        branch,
        "prepare_test_drive",
        {"vehicleId": "veh-049"},
        "draft",
        {
            "draftId": "draft-volvo",
            "summary": {"kind": "test_drive", "vehicleId": "veh-049"},
            "missingPublicFields": ["slotId"],
        },
    )

    assert replacement["activeWorkflow"] == "test_drive"
    assert replacement["entities"] == {"vehicleId": "veh-049"}
    assert "pausedWorkflow" not in replacement


def test_dealership_read_retains_exact_location_and_department_for_contact_action() -> None:
    result = WorkflowStateReducer().advance(
        {},
        "find_dealership_departments",
        {"town": "Manchester", "department": "sales"},
        "dealership_list",
        {
            "items": [
                {
                    "id": "northstar-manchester",
                    "town": "Manchester",
                    "departments": ["parts", "sales", "service"],
                }
            ]
        },
    )

    assert result["activeWorkflow"] == "dealership_information"
    assert result["entities"] == {"dealershipId": "northstar-manchester"}
    assert result["constraints"] == {"town": "Manchester", "department": "sales"}


def test_part_exchange_estimate_transitions_to_ai_owned_follow_up() -> None:
    result = WorkflowStateReducer().advance(
        {
            "version": 3,
            "activeWorkflow": "part_exchange",
            "stage": "viewing",
            "entities": {},
            "constraints": {},
        },
        "estimate_part_exchange",
        {},
        "part_exchange_estimate",
        {"estimateLowPence": 1_000_000},
    )

    assert result["activeWorkflow"] == "part_exchange"
    assert result["stage"] == "estimate_ready"


def test_part_exchange_estimate_activation_exposes_exact_secure_contract_to_composer() -> None:
    result = WorkflowStateReducer().advance(
        {},
        "request_part_exchange_estimate_form",
        {},
        "part_exchange_estimate_form",
        {"estimateFormRequested": True},
    )

    assert result["activeWorkflow"] == "part_exchange"
    assert result["stage"] == "awaiting_protected_input"
    assert result["constraints"] == {
        "missingPublicFields": [],
        "secureFields": ["registration", "mileage", "condition"],
        "secureInputReady": True,
        "collectedPublicValues": {},
    }


def test_booking_verification_activation_exposes_exact_secure_contract_and_mode() -> None:
    result = WorkflowStateReducer().advance(
        {},
        "request_workshop_booking_lookup_form",
        {"mode": "amend"},
        "private_booking_lookup",
        {"lookupFormRequested": True},
    )

    assert result["activeWorkflow"] == "existing_workshop_booking"
    assert result["stage"] == "awaiting_verification"
    assert result["constraints"] == {
        "missingPublicFields": [],
        "secureFields": ["reference", "lastName", "registration", "phone"],
        "secureInputReady": True,
        "collectedPublicValues": {},
        "mode": "amend",
    }


def test_verified_amendment_slot_is_enriched_from_trusted_choice(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "amend-slot",
                "prepare_workshop_amendment",
                {"slotId": "ws-slot-0002"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_amend",
            "stage": "choosing_time",
            "entities": {"serviceTypeId": "diagnostic-inspection"},
            "constraints": {"verifiedBooking": True},
        },
        references=references(
            choices=[
                {
                    "entityReference": "appointment:ws-slot-0002",
                    "startsAt": "2026-09-12T10:00:00+01:00",
                    "dealershipId": "northstar-stockport",
                    "dealershipName": "Northstar Stockport",
                    "serviceTypeId": "diagnostic-inspection",
                    "serviceTypeName": "Diagnostic inspection",
                }
            ]
        ),
        latest_customer_message="The second appointment",
    )

    assert decision.calls[0].arguments == {
        "slotId": "ws-slot-0002",
        "selectedStartsAt": "2026-09-12T10:00:00+01:00",
        "selectedDealershipId": "northstar-stockport",
        "selectedDealershipName": "Northstar Stockport",
        "selectedServiceTypeId": "diagnostic-inspection",
        "selectedServiceName": "Diagnostic inspection",
    }


def test_new_workshop_booking_keeps_trusted_slot_metadata_for_visual_summary(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "book-slot",
                "prepare_workshop_booking",
                {"slotId": "ws-slot-0002"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_time",
            "entities": {
                "serviceTypeId": "diagnostic-inspection",
                "dealershipId": "northstar-stockport",
            },
            "constraints": {"missingPublicFields": ["slotId"]},
        },
        references=references(
            choices=[
                {
                    "entityReference": "appointment:ws-slot-0002",
                    "startsAt": "2026-09-12T10:00:00+01:00",
                    "dealershipId": "northstar-stockport",
                    "dealershipName": "Northstar Stockport",
                    "serviceTypeId": "diagnostic-inspection",
                    "serviceTypeName": "Diagnostic inspection",
                }
            ]
        ),
    )

    assert decision.calls[0].arguments == {
        "slotId": "ws-slot-0002",
        "serviceTypeId": "diagnostic-inspection",
        "dealershipId": "northstar-stockport",
        "selectedStartsAt": "2026-09-12T10:00:00+01:00",
        "selectedDealershipId": "northstar-stockport",
        "selectedDealershipName": "Northstar Stockport",
        "selectedServiceTypeId": "diagnostic-inspection",
        "selectedServiceName": "Diagnostic inspection",
    }


def test_selected_workshop_slot_grounds_its_matching_service_and_dealership(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentKinds=["workshop_booking"],
        answeredQuestionId="question-appointment",
        resolvedReferences=["appointment:ws-slot-0235"],
        confidence="high",
    )
    selected = {
        "entityReference": "appointment:ws-slot-0235",
        "startsAt": "2026-09-07T14:00:00Z",
        "dealershipId": "northstar-liverpool",
        "dealershipName": "Northstar Liverpool",
        "serviceTypeId": "brake-inspection",
        "serviceTypeName": "Brake inspection",
    }

    decision = policy.validate(
        [
            ToolCall(
                "book-slot",
                "prepare_workshop_booking",
                {
                    "slotId": "ws-slot-0235",
                    "serviceTypeId": "brake-inspection",
                    "dealershipId": "northstar-liverpool",
                },
                intent_kind="workshop_booking",
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_slot",
            "entities": {},
            "constraints": {
                "serviceTypeName": "brakes",
                "missingPublicFields": ["slotId"],
            },
        },
        references=references(choices=[selected]),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {
        "slotId": "ws-slot-0235",
        "serviceTypeId": "brake-inspection",
        "dealershipId": "northstar-liverpool",
        "selectedStartsAt": "2026-09-07T14:00:00Z",
        "selectedDealershipId": "northstar-liverpool",
        "selectedDealershipName": "Northstar Liverpool",
        "selectedServiceTypeId": "brake-inspection",
        "selectedServiceName": "Brake inspection",
    }


def test_selected_workshop_slot_does_not_ground_a_conflicting_service(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentKinds=["workshop_booking"],
        answeredQuestionId="question-appointment",
        resolvedReferences=["appointment:ws-slot-0235"],
        confidence="high",
    )

    with pytest.raises(ValueError, match="serviceTypeId is not grounded"):
        policy.validate(
            [
                ToolCall(
                    "book-slot",
                    "prepare_workshop_booking",
                    {
                        "slotId": "ws-slot-0235",
                        "serviceTypeId": "mot",
                        "dealershipId": "northstar-liverpool",
                    },
                    intent_kind="workshop_booking",
                )
            ],
            state={
                "version": 3,
                "activeWorkflow": "workshop_booking",
                "stage": "choosing_slot",
                "entities": {},
                "constraints": {"missingPublicFields": ["slotId"]},
            },
            references=references(
                choices=[
                    {
                        "entityReference": "appointment:ws-slot-0235",
                        "startsAt": "2026-09-07T14:00:00Z",
                        "dealershipId": "northstar-liverpool",
                        "serviceTypeId": "brake-inspection",
                    }
                ]
            ),
            turn_understanding=understanding,
        )


def test_workshop_read_can_start_from_a_provider_supplied_service_candidate(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "service-follow-up",
                "refine_workshop_slots",
                {"serviceTypeName": "brakes"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop",
            "stage": "viewing",
            "entities": {},
            "constraints": {},
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {"serviceTypeName": "brakes"}


def test_workshop_service_id_must_come_from_the_trusted_choice_set(
    policy: ToolPolicyGate,
) -> None:
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_service",
        "entities": {},
        "constraints": {},
    }
    trusted = references(
        choices=[
            {
                "position": 1,
                "entityReference": "service:brake-inspection",
                "label": "Brake inspection",
            }
        ]
    )
    decision = policy.validate(
        [
            ToolCall(
                "service",
                "refine_workshop_slots",
                {"serviceTypeId": "brake-inspection"},
            )
        ],
        state=state,
        references=trusted,
    )
    assert decision.calls[0].arguments == {"serviceTypeId": "brake-inspection"}

    with pytest.raises(ValueError, match="untrusted choice"):
        policy.validate(
            [
                ToolCall(
                    "service",
                    "refine_workshop_slots",
                    {"serviceTypeId": "invented-service"},
                )
            ],
            state=state,
            references=trusted,
        )


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


def test_declared_comparison_intent_cannot_execute_a_search_or_page_filter(
    policy: ToolPolicyGate,
) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        policy.validate(
            [
                ToolCall(
                    "wrong-operation",
                    "select_page_vehicles",
                    {"vehicleIds": ["veh-001"], "sort": "newest"},
                    intent_kind="vehicle_comparison",
                )
            ],
            state={},
            references=references(page=[{"vehicleId": "veh-001"}]),
        )


def test_comparison_resolution_state_retains_completed_slots_across_clarification() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "compare_vehicle_models",
        {"queries": ["BMW", "MINI Cooper"]},
        "vehicle_list",
        {
            "resolution": {
                "status": "ambiguous",
                "queries": ["BMW", "MINI Cooper"],
                "pendingQuery": "BMW",
                "pendingIndex": 0,
                "resolvedVehicleIds": [None, "veh-041"],
                "selectionBasis": "Newest currently available representative.",
            }
        },
    )

    assert state["activeWorkflow"] == "vehicle_comparison"
    assert state["stage"] == "choosing_vehicle"
    assert state["constraints"]["resolvedVehicleIds"] == [None, "veh-041"]
    assert state["constraints"]["comparisonSelectionBasis"].startswith("Newest")


def test_comparison_follow_up_injects_server_owned_selection_provenance(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "compare",
                "compare_vehicles",
                {"vehicleIds": ["veh-010", "veh-041"]},
                intent_kind="vehicle_comparison",
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "vehicle_comparison",
            "stage": "choosing_vehicle",
            "entities": {},
            "constraints": {
                "resolvedVehicleIds": [None, "veh-041"],
                "comparisonSelectionBasis": "Newest currently available representative.",
            },
        },
        references=references(vehicles=[{"vehicleId": "veh-010"}]),
    )

    assert decision.calls[0].arguments["selectionBasis"].startswith("Newest")


def test_completed_comparison_persists_both_trusted_subjects_and_provenance() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "compare_vehicles",
        {
            "vehicleIds": ["veh-010", "veh-041"],
            "selectionBasis": "Newest currently available representative.",
        },
        "vehicle_comparison",
        {
            "comparison": {
                "vehicleIds": ["veh-010", "veh-041"],
                "selectionBasis": "Newest currently available representative.",
            }
        },
    )

    assert state["stage"] == "viewing"
    assert state["entities"]["vehicleIds"] == ["veh-010", "veh-041"]
    assert state["constraints"]["comparisonSelectionBasis"].startswith("Newest")


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


def test_singular_hours_tool_cannot_select_one_of_many_dealerships(
    policy: ToolPolicyGate,
) -> None:
    with pytest.raises(ValueError, match="dealership selection is ambiguous"):
        policy.validate(
            [ToolCall("hours", "get_opening_hours", {"id": "dealer-manchester"})],
            state={},
            references=references(
                dealerships=[
                    {"dealershipId": "dealer-bolton", "town": "Bolton"},
                    {"dealershipId": "dealer-manchester", "town": "Manchester"},
                ]
            ),
        )


def test_hours_list_accepts_an_ai_resolved_trusted_dealership_id(
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

    assert decision.calls[0].arguments == {"dealershipId": "dealer-manchester"}


def test_policy_preserves_the_ai_resolved_location_without_parsing_customer_words(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "hours",
                "list_opening_hours",
                {"dealershipId": "dealer-stockport"},
            )
        ],
        state={},
        references=references(
            dealerships=[
                {"dealershipId": "dealer-bolton", "town": "Bolton"},
                {"dealershipId": "dealer-stockport", "town": "Stockport"},
            ]
        ),
        latest_customer_message="Actually, what time does Stockport close?",
    )

    assert decision.calls[0].arguments == {"dealershipId": "dealer-stockport"}


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


def test_dealership_read_binds_department_as_durable_contact_context(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["dealership_detail"],
        resolvedInputs=[
            ResolvedInput(
                field="town",
                value="Manchester",
                sourceContextId="message:customer-1",
                sourceText="Manchester",
            ),
            ResolvedInput(
                field="department",
                value="sales",
                sourceContextId="message:customer-1",
                sourceText="sales",
            ),
        ],
        confidence="high",
    )

    decision = policy.validate(
        [
            ToolCall(
                "dealer",
                "list_dealerships",
                {"town": "Manchester"},
                intent_kind="dealership_detail",
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {
        "town": "Manchester",
        "department": "sales",
    }


def test_single_dealership_department_query_requires_a_location_without_context(
    policy: ToolPolicyGate,
) -> None:
    with pytest.raises(ValueError, match="dealership selection requires clarification"):
        policy.validate(
            [ToolCall("departments", "find_dealership_departments", {})],
            state={},
            references=references(),
        )


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
        policy.validate(
            [call],
            state={},
            references=references(),
            latest_customer_message="Change time",
        )

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
        latest_customer_message="Change time",
    )
    assert decision.calls == [call]

    with pytest.raises(ValueError, match="only a confirmed workshop booking"):
        policy.validate(
            [call],
            state={
                "version": 3,
                "activeWorkflow": "existing_workshop_booking",
                "stage": "verified",
                "entities": {},
                "constraints": {"bookingStatus": "cancelled"},
            },
            references=references(),
            latest_customer_message="Change time",
        )


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
        "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
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
        "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
    }


def test_date_refined_workshop_state_requires_one_trusted_slot_choice() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "refine_workshop_slots",
        {
            "serviceTypeId": "brake-inspection",
            "dateFrom": "2026-09-05",
            "dateTo": "2026-09-05",
        },
        "slot_list",
    )

    assert state["constraints"] == {
        "serviceTypeId": "brake-inspection",
        "dateFrom": "2026-09-05",
        "dateTo": "2026-09-05",
        "missingPublicFields": ["slotId"],
    }


def test_workshop_refinement_does_not_leak_state_metadata_into_tool_arguments(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "date",
                "refine_workshop_slots",
                {"dateFrom": "2026-09-05", "dateTo": "2026-09-05"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_time",
            "entities": {"serviceTypeId": "brake-inspection"},
            "constraints": {
                "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
            },
        },
        references=references(),
    )

    assert decision.calls[0].arguments == {
        "serviceTypeId": "brake-inspection",
        "dateFrom": "2026-09-05",
        "dateTo": "2026-09-05",
    }


def test_workshop_available_options_explicitly_clear_failed_schedule_only(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "options",
                "refine_workshop_slots",
                {"schedulePreferenceMode": "clear"},
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_alternative_schedule",
            "entities": {"serviceTypeId": "full-service"},
            "constraints": {
                "dealershipTown": "Bolton",
                "dateFrom": "2026-09-06",
                "dateTo": "2026-09-06",
                "timeOfDay": "morning",
            },
        },
        references=references(),
        latest_customer_message="what options are available",
    )

    assert decision.calls[0].arguments == {
        "serviceTypeId": "full-service",
        "dealershipTown": "Bolton",
        "schedulePreferenceMode": "clear",
    }


def test_typed_workshop_location_change_preserves_existing_schedule(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "alternative-location",
                "refine_workshop_slots",
                {"dealershipId": "northstar-stockport"},
                intent_kind="workshop_booking",
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_dealership",
            "entities": {
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-bolton",
            },
            "constraints": {
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-bolton",
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
                "schedulingPreferences": {
                    "dateFrom": "2026-09-07",
                    "dateTo": "2026-09-07",
                    "timeOfDay": "morning",
                },
                "missingPublicFields": ["dealershipId"],
            },
        },
        references=references(
            dealerships=[
                {
                    "dealershipId": "northstar-stockport",
                    "town": "Stockport",
                }
            ]
        ),
    )

    assert decision.calls[0].arguments == {
        "serviceTypeId": "tyre-fitting",
        "dealershipId": "northstar-stockport",
        "dateFrom": "2026-09-07",
        "dateTo": "2026-09-07",
        "timeOfDay": "morning",
    }


def test_selected_test_drive_vehicle_is_injected_into_schedule_continuation(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "slots",
                "list_test_drive_slots",
                {
                    "dateFrom": "2026-09-06",
                    "dateTo": "2026-09-06",
                    "timeOfDay": "morning",
                    "schedulePreferenceMode": "replace",
                },
                intent_kind="test_drive",
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "choosing_schedule_preferences",
            "entities": {"vehicleId": "veh-049"},
            "constraints": {
                "selectionEvidence": {
                    "vehicleId": "veh-049",
                    "basis": "named_vehicle",
                    "matchedIdentity": "BMW 1 Series",
                }
            },
        },
        references=references(vehicles=[{"vehicleId": "veh-049"}]),
        latest_customer_message="tomorrow morning",
    )

    assert decision.calls[0].arguments["vehicleId"] == "veh-049"
    assert decision.calls[0].arguments["schedulePreferenceMode"] == "replace"


def test_workshop_service_chooser_has_a_distinct_state_stage() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "list_workshop_slots",
        {},
        "service_list",
    )

    assert state["activeWorkflow"] == "workshop_booking"
    assert state["stage"] == "choosing_service"
    assert state["lastRenderer"] == "service_list"


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


def test_workflow_values_not_present_in_typed_semantics_are_discarded_for_collection(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["dealership_message"],
        confidence="high",
        resolvedInputs=[
            ResolvedInput(
                field="department",
                value="parts",
                sourceContextId="message:customer-1",
                sourceText="parts team",
            ),
            ResolvedInput(
                field="message",
                value="whether they stock roof bars",
                sourceContextId="message:customer-1",
                sourceText="whether they stock roof bars",
            ),
        ],
    )

    decision = policy.validate(
        [
            ToolCall(
                "message",
                "prepare_dealership_message",
                {
                    "dealershipTown": "Stockport",
                    "department": "parts",
                    "subject": "Roof bars stock enquiry",
                    "message": "Do you stock roof bars?",
                },
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {
        "dealershipTown": "Stockport",
        "department": "parts",
        "message": "whether they stock roof bars",
    }


def test_ungrounded_callback_reason_cannot_block_workflow_entry(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["callback"],
        confidence="high",
    )

    decision = policy.validate(
        [
            ToolCall(
                "callback",
                "prepare_callback",
                {"reason": "Customer requested a callback"},
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {}


def test_workflow_values_are_accepted_from_typed_semantic_evidence(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentKinds=["callback"],
        confidence="high",
        resolvedInputs=[
            ResolvedInput(
                field="department",
                value="sales",
                sourceContextId="message:customer-1",
                sourceText="sales team",
            ),
            ResolvedInput(
                field="preferredTime",
                value="tomorrow afternoon",
                sourceContextId="message:customer-1",
                sourceText="tomorrow afternoon",
            ),
            ResolvedInput(
                field="reason",
                value="an electric car",
                sourceContextId="message:customer-1",
                sourceText="an electric car",
            ),
        ],
    )
    decision = policy.validate(
        [
            ToolCall(
                "callback",
                "prepare_callback",
                {
                    "dealershipTown": "Manchester",
                    "department": "sales",
                    "preferredTime": "tomorrow afternoon",
                    "reason": "an electric car",
                },
            )
        ],
        state={},
        references=references(),
        turn_understanding=understanding,
    )

    assert decision.calls[0].arguments == {
        "dealershipTown": "Manchester",
        "department": "sales",
        "preferredTime": "tomorrow afternoon",
        "reason": "an electric car",
    }


def test_test_drive_slot_lookup_reuses_persisted_scheduling_preferences(
    policy: ToolPolicyGate,
) -> None:
    state = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "collecting",
        "entities": {},
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-08",
                "dateTo": "2026-09-08",
                "timeOfDay": "afternoon",
            }
        },
    }

    decision = policy.validate(
        [ToolCall("slots", "list_test_drive_slots", {"vehicleId": "veh-021"})],
        state=state,
        references=references(vehicles=[{"vehicleId": "veh-021"}]),
    )

    assert decision.calls[0].arguments == {
        "vehicleId": "veh-021",
        "dateFrom": "2026-09-08",
        "dateTo": "2026-09-08",
        "timeOfDay": "afternoon",
    }


def test_test_drive_selector_change_without_new_schedule_browses_live_availability(
    policy: ToolPolicyGate,
) -> None:
    state = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_slot",
        "entities": {"vehicleId": "veh-042"},
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-06",
                "dateTo": "2026-09-06",
                "timeOfDay": "afternoon",
            }
        },
    }

    decision = policy.validate(
        [
            ToolCall(
                "slots",
                "list_test_drive_slots",
                {
                    "dealershipId": "northstar-manchester",
                    "schedulePreferenceMode": "replace",
                },
            )
        ],
        state=state,
        references=references(vehicles=[{"vehicleId": "veh-042"}]),
    )

    assert decision.calls[0].arguments == {
        "vehicleId": "veh-042",
        "dealershipId": "northstar-manchester",
        "schedulePreferenceMode": "clear",
    }


def test_described_test_drive_vehicle_rebinds_before_any_slot_continuation(
    policy: ToolPolicyGate,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="modify_goal",
        goalRelation="active",
        intentKinds=["test_drive"],
        resolvedInputs=[
            ResolvedInput(
                field="make",
                value="MINI",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
            ResolvedInput(
                field="model",
                value="Cooper",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
            ResolvedInput(
                field="dealershipTown",
                value="Manchester",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
        ],
        ambiguity="none",
        confidence="high",
    )
    state = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {
            "vehicleId": "veh-042",
            "dealershipId": "northstar-stockport",
        },
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "afternoon",
            },
            "collectedPublicValues": {"vehicleId": "veh-042"},
            "selectionEvidence": {
                "vehicleId": "veh-042",
                "basis": "persisted_selection",
                "matchedIdentity": "MINI Countryman",
            },
        },
    }

    with pytest.raises(ValueError, match="vehicleId must be resolved again"):
        policy.validate(
            [ToolCall("slots", "list_test_drive_slots", {}, intent_kind="test_drive")],
            state=state,
            references=references(vehicles=[{"vehicleId": "veh-042"}]),
            turn_understanding=understanding,
        )

    decision = policy.validate(
        [ToolCall("prepare", "prepare_test_drive", {}, intent_kind="test_drive")],
        state=state,
        references=references(vehicles=[{"vehicleId": "veh-042"}]),
        turn_understanding=understanding,
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        (
            "prepare_test_drive",
            {
                "make": "MINI",
                "model": "Cooper",
                "dealershipTown": "Manchester",
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "afternoon",
            },
        ),
        (
            "search_vehicles",
            {
                "make": "MINI",
                "model": "Cooper",
                "dealershipTown": "Manchester",
            },
        ),
    ]
    assert all(call.arguments.get("vehicleId") != "veh-042" for call in decision.calls)


def test_test_drive_draft_compiles_its_live_slot_dependency(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("draft", "prepare_test_drive", {"vehicleId": "veh-021"})],
        state={},
        references=references(vehicles=[{"vehicleId": "veh-021"}]),
        latest_customer_message="I'd like to arrange a viewing for this vehicle.",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        (
            "prepare_test_drive",
            {
                "vehicleId": "veh-021",
                "selectionEvidence": {
                    "vehicleId": "veh-021",
                    "basis": "ai_resolved_trusted_reference",
                    "matchedIdentity": "veh-021",
                },
            },
        ),
        ("list_test_drive_slots", {"vehicleId": "veh-021"}),
    ]


def test_policy_validates_but_does_not_reinterpret_an_ai_vehicle_resolution(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("draft", "prepare_test_drive", {"vehicleId": "veh-014"})],
        state={},
        references=references(
            vehicles=[
                {"position": 1, "vehicleId": "veh-014", "make": "BMW", "model": "3 Series"},
                {"position": 2, "vehicleId": "veh-049", "make": "BMW", "model": "1 Series"},
            ]
        ),
        latest_customer_message="book a test drive for bmw",
    )

    assert decision.calls[0].arguments["vehicleId"] == "veh-014"
    assert decision.calls[0].arguments["selectionEvidence"]["basis"] == (
        "ai_resolved_trusted_reference"
    )


def test_policy_does_not_replace_the_ai_resolved_vehicle_from_customer_words(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("draft", "prepare_test_drive", {"vehicleId": "veh-014"})],
        state={},
        references=references(
            vehicles=[
                {"position": 1, "vehicleId": "veh-014", "make": "BMW", "model": "3 Series"},
                {"position": 2, "vehicleId": "veh-049", "make": "BMW", "model": "1 Series"},
            ]
        ),
        latest_customer_message="bmw 1",
    )

    assert decision.calls[0].name == "prepare_test_drive"
    assert decision.calls[0].arguments == {
        "vehicleId": "veh-014",
        "selectionEvidence": {
            "vehicleId": "veh-014",
            "basis": "ai_resolved_trusted_reference",
            "matchedIdentity": "BMW 3 Series",
        },
    }
    assert decision.calls[1].name == "list_test_drive_slots"
    assert decision.calls[1].arguments == {"vehicleId": "veh-014"}


@pytest.mark.parametrize(
    "customer_message",
    (
        "show appointment for 3 series",
        "show appointment for series 3",
        "book appintment for 3 Series",
    ),
)
def test_policy_result_is_independent_of_customer_phrasing(
    policy: ToolPolicyGate, customer_message: str,
) -> None:
    decision = policy.validate(
        [ToolCall("draft", "prepare_test_drive", {})],
        state={},
        references=references(
            vehicles=[
                {
                    "position": 1,
                    "vehicleId": "veh-014",
                    "make": "BMW",
                    "model": "3 Series",
                },
                {
                    "position": 2,
                    "vehicleId": "veh-042",
                    "make": "Land Rover",
                    "model": "Discovery Sport",
                },
                {
                    "position": 3,
                    "vehicleId": "veh-049",
                    "make": "Volvo",
                    "model": "V60",
                },
            ]
        ),
        latest_customer_message=customer_message,
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("prepare_test_drive", {}),
        ("search_vehicles", {}),
    ]


def test_unresolved_test_drive_keeps_inventory_discovery_explicit(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall("draft", "prepare_test_drive", {}),
            ToolCall("search", "search_vehicles", {}),
        ],
        state={},
        references=references(
            vehicles=[
                {
                    "position": 1,
                    "vehicleId": "veh-014",
                    "make": "BMW",
                    "model": "3 Series",
                },
                {
                    "position": 2,
                    "vehicleId": "veh-021",
                    "make": "Land Rover",
                    "model": "Discovery Sport",
                },
            ]
        ),
        latest_customer_message="show appointment for 3 series",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("prepare_test_drive", {}),
        ("search_vehicles", {}),
    ]


def test_policy_never_authors_an_entity_clarification_from_customer_phrasing(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
            [ToolCall("draft", "prepare_test_drive", {})],
            state={},
            references=references(
                vehicles=[
                    {
                        "position": 1,
                        "vehicleId": "veh-014",
                        "year": 2024,
                        "make": "BMW",
                        "model": "3 Series",
                    },
                    {
                        "position": 2,
                        "vehicleId": "veh-026",
                        "year": 2026,
                        "make": "BMW",
                        "model": "3 Series",
                    },
                ]
            ),
            latest_customer_message="show appointments for the 3 series",
        )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("prepare_test_drive", {}),
        ("search_vehicles", {}),
    ]


def test_policy_does_not_turn_an_ordinal_into_a_workflow_transition(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("search", "search_vehicles", {}, intent_kind="vehicle_search")],
        state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "choosing_vehicle",
            "entities": {},
            "constraints": {},
        },
        references=references(
            vehicles=[
                {"position": 1, "vehicleId": "veh-049", "make": "BMW", "model": "1 Series"},
                {"position": 2, "vehicleId": "veh-025", "make": "BMW", "model": "1 Series"},
            ]
        ),
        latest_customer_message="option 1",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("search_vehicles", {})
    ]


def test_policy_does_not_turn_a_description_into_a_workflow_transition(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "search",
                "search_vehicles",
                {},
                intent_kind="vehicle_search",
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "no_availability",
            "entities": {"vehicleId": "veh-014"},
            "constraints": {},
        },
        references=references(
            vehicles=[
                {
                    "position": 1,
                    "vehicleId": "veh-014",
                    "make": "BMW",
                    "model": "3 Series",
                    "bodyStyle": "Saloon",
                },
                {
                    "position": 2,
                    "vehicleId": "veh-042",
                    "make": "Land Rover",
                    "model": "Discovery Sport",
                    "variant": "Dynamic S",
                    "bodyStyle": "SUV",
                },
                {
                    "position": 3,
                    "vehicleId": "veh-049",
                    "make": "Volvo",
                    "model": "V60",
                    "bodyStyle": "Estate",
                },
            ]
        ),
        latest_customer_message="I'm interested in the SUV option",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("search_vehicles", {})
    ]


def test_suv_preference_does_not_select_an_arbitrary_displayed_suv(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("search", "search_vehicles", {"bodyStyle": "SUV"})],
        state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "choosing_vehicle",
            "entities": {},
            "constraints": {},
        },
        references=references(
            vehicles=[
                {
                    "position": 1,
                    "vehicleId": "veh-042",
                    "make": "Land Rover",
                    "model": "Discovery Sport",
                    "bodyStyle": "SUV",
                },
                {
                    "position": 2,
                    "vehicleId": "veh-043",
                    "make": "Volvo",
                    "model": "XC60",
                    "bodyStyle": "SUV",
                },
            ]
        ),
        latest_customer_message="show me the SUV options",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        ("search_vehicles", {"bodyStyle": "SUV"}),
    ]


def test_empty_broad_test_drive_result_does_not_request_date_or_time() -> None:
    state = WorkflowStateReducer().advance(
        {},
        "list_test_drive_slots",
        {"vehicleId": "veh-049"},
        "test_drive_slot_picker",
        {
            "resolution": {
                "kind": "test_drive_slots",
                "status": "empty",
                "scope": "broad",
                "continuation": "offer_vehicle_alternatives",
                "vehicleId": "veh-049",
                "count": 0,
            }
        },
    )

    assert state["stage"] == "no_availability"
    assert state["constraints"]["missingPublicFields"] == []
    assert state["constraints"]["continuation"] == "offer_vehicle_alternatives"


def test_exhausted_filtered_test_drive_search_does_not_ask_customer_to_guess_again() -> None:
    resolution = _test_drive_slot_resolution(
        {
            "vehicleId": "veh-049",
            "dateFrom": "2026-09-08",
            "dateTo": "2026-09-08",
            "timeOfDay": "morning",
        },
        count=0,
        schedule_search_exhausted=True,
    )

    assert resolution["continuation"] == "offer_vehicle_alternatives"
    transition = _test_drive_transition(resolution)
    assert transition["stage"] == "no_availability"
    assert transition["missingPublicFields"] == []


def test_transactional_read_preserves_subject_relationships_from_preparation() -> None:
    reducer = WorkflowStateReducer()
    prepared = reducer.advance(
        {},
        "prepare_test_drive",
        {"vehicleId": "veh-027"},
        "draft",
        {
            "draftId": "draft-027",
            "summary": {"vehicleId": "veh-027"},
            "vehicle": {
                "id": "veh-027",
                "dealershipId": "northstar-liverpool",
            },
            "missingPublicFields": [],
            "secureFields": [],
            "secureInputReady": False,
        },
    )

    refined = reducer.advance(
        prepared,
        "list_test_drive_slots",
        {"vehicleId": "veh-027"},
        "test_drive_slot_picker",
        {
            "resolution": {
                "kind": "test_drive_slots",
                "status": "ready",
                "scope": "broad",
                "continuation": "request_schedule_preferences",
                "vehicleId": "veh-027",
                "count": 0,
            }
        },
    )

    assert refined["entities"] == {
        "vehicleId": "veh-027",
        "dealershipId": "northstar-liverpool",
    }


def test_same_workflow_transition_preserves_the_paused_workflow_stack() -> None:
    paused = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_slot",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["slotId"]},
    }
    current = {
        "version": 3,
        "activeWorkflow": "callback",
        "stage": "collecting",
        "entities": {},
        "constraints": {"missingPublicFields": ["reason"]},
        "pausedWorkflow": paused,
    }

    state = WorkflowStateReducer().advance(
        current,
        "prepare_callback",
        {"reason": "It’s about parts."},
        "draft",
        {"summary": {"reason": "It’s about parts."}},
    )

    assert state["pausedWorkflow"] == paused


def test_explicit_transaction_handoff_consumes_source_instead_of_pausing_it() -> None:
    current = {
        "version": 3,
        "activeWorkflow": "part_exchange",
        "stage": "estimate_ready",
        "entities": {},
        "constraints": {},
    }

    state = WorkflowStateReducer().advance(
        current,
        "prepare_callback",
        {
            "department": "sales",
            "reason": "Will you pick up my car?",
        },
        "draft",
        {
            "draftId": "draft-callback",
            "summary": {
                "department": "sales",
                "reason": "Will you pick up my car?",
            },
            "missingPublicFields": ["dealershipId"],
            "secureFields": ["firstName", "lastName", "email", "phone"],
            "secureInputReady": False,
        },
        transition="handoff",
    )

    assert state["activeWorkflow"] == "callback"
    assert "pausedWorkflow" not in state
    assert state["constraints"]["collectedPublicValues"] == {
        "department": "sales",
        "reason": "Will you pick up my car?",
    }


def test_explicit_transaction_handoff_preserves_an_existing_paused_ancestor() -> None:
    paused = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_slot",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["slotId"]},
    }
    current = {
        "version": 3,
        "activeWorkflow": "part_exchange",
        "stage": "estimate_ready",
        "entities": {},
        "constraints": {},
        "pausedWorkflow": paused,
    }

    state = WorkflowStateReducer().advance(
        current,
        "prepare_callback",
        {"department": "sales", "reason": "Will you pick up my car?"},
        "draft",
        {"summary": {"department": "sales", "reason": "Will you pick up my car?"}},
        transition="handoff",
    )

    assert state["activeWorkflow"] == "callback"
    assert state["pausedWorkflow"] == paused


def test_active_test_drive_preferences_resolve_slots_without_recreating_draft(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [
            ToolCall(
                "draft",
                "prepare_test_drive",
                {
                    "dateFrom": "2026-09-07",
                    "dateTo": "2026-09-07",
                    "timeOfDay": "afternoon",
                },
            )
        ],
        state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "collecting",
            "entities": {"vehicleId": "veh-040"},
            "constraints": {
                "draftId": "draft-001",
                "missingPublicFields": ["slotId"],
                "collectedPublicValues": {"vehicleId": "veh-040"},
            },
        },
        references=references(),
        latest_customer_message="Monday afternoon",
    )

    assert [(call.name, call.arguments) for call in decision.calls] == [
        (
            "list_test_drive_slots",
            {
                "vehicleId": "veh-040",
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "afternoon",
            },
        )
    ]


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
        latest_customer_message="Please call me in Liverpool",
    )

    assert decision.calls[0].arguments["dealershipTown"] == "Liverpool"


def test_policy_does_not_infer_callback_fields_omitted_by_the_provider(
    policy: ToolPolicyGate,
) -> None:
    decision = policy.validate(
        [ToolCall("form", "prepare_callback", {})],
        state={},
        references=references(),
        latest_customer_message="please have a dealership call me",
        recent_customer_messages=("Will you pick up my car?", "yes"),
    )

    assert decision.calls[0].arguments == {}
