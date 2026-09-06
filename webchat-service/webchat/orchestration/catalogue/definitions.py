"""Declarative metadata for every application-backed tool."""

from __future__ import annotations

from pydantic import BaseModel

from webchat.orchestration.tools.inputs import (
    AppointmentWorkflowDraftInput,
    BookingLookupForm,
    BusinessInformationQuery,
    CallbackAgentInput,
    ContactOptionsInput,
    DealershipMessageAgentInput,
    DealershipMessageDraftInput,
    DealershipQuery,
    DealershipScopeQuery,
    EmptyInput,
    Filters,
    HolidayOpeningHoursQuery,
    OfferSearch,
    OpeningHoursQuery,
    PageVehicleSelection,
    PartExchangeAgentInput,
    PartExchangeEstimate,
    PartExchangeEstimateForm,
    SalesEnquiryAgentInput,
    ServiceQuery,
    StableId,
    TestDriveAgentInput,
    TestDriveSlotAgentInput,
    VehicleComparison,
    VehicleComparisonRuntime,
    VehicleIdentityQuery,
    VehicleInterestAgentInput,
    VehicleModelComparison,
    VehiclePreferenceRequest,
    VehicleSearch,
    WorkflowDraftInput,
    WorkshopAmendmentAgentInput,
    WorkshopAmendmentInput,
    WorkshopAmendmentSlotContext,
    WorkshopBookingAgentInput,
    WorkshopSlotAgentInput,
)

from .contracts import Invocation, ResultMode, Risk, ToolDefinition


def _tool(
    name: str,
    title: str,
    description: str,
    input_model: type[BaseModel],
    risk: Risk = "read",
    mode: ResultMode = "render",
    invocation: Invocation = "planner",
    preconditions: tuple[str, ...] = (),
    reference_inputs: tuple[tuple[str, str], ...] = (),
    candidate_subject_field: str | None = None,
    retrieval_examples: tuple[str, ...] = (),
    trusted_input_model: type[BaseModel] | None = None,
    provider_input_model: type[BaseModel] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        id=name,
        title=title,
        description=description,
        input_model=input_model,
        provider_input_model=provider_input_model,
        trusted_input_model=trusted_input_model,
        executor_reference=name,
        risk=risk,
        invocation=invocation,
        result_mode=mode,
        preconditions=preconditions,
        reference_inputs=reference_inputs,
        candidate_subject_field=candidate_subject_field,
        retrieval_examples=retrieval_examples,
    )


CORE_TOOLS: tuple[ToolDefinition, ...] = (
    _tool(
        "cancel_active_capability",
        "Cancel the active unfinished request",
        "Cancel the current unfinished transactional capability when the customer clearly asks to stop, cancel, abandon, or forget it. This never cancels an already confirmed business operation.",
        EmptyInput,
        risk="draft",
        mode="workflow",
        preconditions=("active_capability",),
        retrieval_examples=("Stop or cancel my unfinished request.",),
    ),
    _tool(
        "resume_paused_capability",
        "Resume the paused request",
        "Resume the most recently paused transactional capability when the customer asks to continue it.",
        EmptyInput,
        risk="draft",
        mode="workflow",
        preconditions=("paused_capability",),
        retrieval_examples=("Continue or resume my previous request.",),
    ),
    _tool(
        "search_vehicles",
        "Search vehicle stock",
        "Start a new search across current vehicle inventory when the customer supplies at least "
        "one vehicle constraint or explicitly asks to browse stock. Constraints include make, "
        "model, colour, budget, mileage, fuel, transmission, body style, availability, dealership town, "
        "sort order, and explicit exclusions. A supplied constraint must be searched directly, "
        "including multiple acceptable makes, models, colours, fuels, transmissions, or body styles. "
        "not replaced by a preference chooser. For open-ended help with no constraints, use "
        "show_vehicle_preferences. For changes to displayed matching results, use "
        "refine_vehicle_search.",
        VehicleSearch,
        candidate_subject_field="vehicleId",
        retrieval_examples=(
            "Find vehicles matching these make, model, price, mileage, fuel, gearbox, body-style, availability, location, year, or ranking constraints.",
            "Find a hybrid SUV under forty thousand pounds.",
            "Find black cars across the available inventory.",
            "Start a fresh constrained vehicle search that does not inherit an earlier search.",
        ),
    ),
    _tool(
        "reset_vehicle_search",
        "Show all available vehicles",
        "Clear the complete current vehicle search and show all available stock. Use when the customer explicitly asks to reset, start over, remove every filter, show all cars, or show all available vehicles. Do not use refine_vehicle_search because refinements preserve omitted filters.",
        EmptyInput,
        candidate_subject_field="vehicleId",
        retrieval_examples=(
            "Show me all available cars and clear the filters.",
            "Reset my vehicle search and start over.",
        ),
    ),
    _tool(
        "refine_vehicle_search",
        "Refine the current vehicle search",
        "Modify, re-sort, exclude categories from, or paginate the current server-owned vehicle search while preserving every unspecified filter. Translate explicit negative preferences into excludedMakes, excludedModels, excludedFuelTypes, excludedTransmissions, or excludedBodyStyles. Use only for a follow-up to displayed matching results; use search_vehicles for a new search.",
        VehicleSearch,
        candidate_subject_field="vehicleId",
        preconditions=("current_vehicle_search",),
        retrieval_examples=(
            "Show more of the current matching vehicles.",
            "Sort the matching vehicles by cheapest price, lowest mileage, or newest first.",
            "Keep the current search constraints but exclude a make, model, fuel, transmission, or body style.",
        ),
    ),
    _tool(
        "select_page_vehicles",
        "Filter visible page vehicles",
        "Filter or rank only vehicles currently rendered on the website page, and only when the "
        "customer explicitly refers to the visible page, these cars, or vehicles shown here. "
        "vehicleIds must come from trusted page context. Never use this for a general or new stock "
        "search; use search_vehicles instead.",
        PageVehicleSelection,
        candidate_subject_field="vehicleId",
        preconditions=("trusted_page_vehicle_ids",),
        retrieval_examples=(
            "Of the cars visible on this page, show only the automatic ones.",
            "Filter these displayed vehicles by price.",
        ),
    ),
    _tool(
        "get_vehicle_facets",
        "Read vehicle search choices",
        "Load authoritative makes, models, body styles, fuels, transmissions, budgets, and mileage "
        "values as supporting evidence when another operation needs live catalogue choices. This "
        "does not present a customer-facing preference chooser; use show_vehicle_preferences for "
        "that.",
        EmptyInput,
        mode="evidence",
        retrieval_examples=(
            "Load live vehicle facets to resolve or support a vehicle preference.",
        ),
    ),
    _tool(
        "show_vehicle_preferences",
        "Show vehicle preference choices",
        "Show application-owned vehicle-search choice chips only when the customer asks for help "
        "choosing a car and has not supplied a usable make, model, budget, mileage, fuel, "
        "transmission, body style, availability, location, or sort constraint. Never replace an "
        "explicit constraint with this chooser; search_vehicles or refine_vehicle_search owns that "
        "request. For a named dimension, set reuseCurrentSearch=true only when the customer "
        "explicitly wants to refine displayed results; otherwise the chooser starts fresh.",
        VehiclePreferenceRequest,
        candidate_subject_field="vehicleId",
        retrieval_examples=(
            "I want a car but do not know where to start or what matters to me.",
            "Help me choose which kind of vehicle preference to decide first.",
        ),
    ),
    _tool(
        "get_vehicle",
        "Get vehicle details",
        "Get current specifications, price, mileage, and other details for one vehicle the "
        "customer identifies from trusted displayed context. Do not use for a named vehicle that "
        "has not been resolved to a trusted ID. A singular request such as 'show me the vehicle' "
        "uses this read-only card when trusted context identifies one current conversationally "
        "focused vehicle; it is not a request to open the external/full vehicle view.",
        StableId,
        preconditions=("trusted_vehicle_id",),
        reference_inputs=(("id", "vehicle"),),
        retrieval_examples=(
            "Tell me more about this displayed car.",
            "Show the details for the vehicle I selected.",
        ),
    ),
    _tool(
        "get_vehicle_availability",
        "Check vehicle availability",
        "Check whether one vehicle identified from trusted displayed context is currently "
        "available, reserved, or sold. Do not use for general inventory discovery.",
        StableId,
        preconditions=("trusted_vehicle_id",),
        reference_inputs=(("id", "vehicle"),),
        retrieval_examples=(
            "Is this displayed car still available?",
            "Has the vehicle I selected been sold or reserved?",
        ),
    ),
    _tool(
        "resolve_vehicle_availability",
        "Check a described vehicle's availability",
        "Resolve one customer-described vehicle against current inventory across every stock "
        "state, then report whether it is available, reserved, or sold. Use when the customer "
        "identifies a vehicle by make, model, variant, year, colour, or location but no trusted "
        "vehicle ID exists yet. This answers a factual stock-status question; it never starts a "
        "sales enquiry. If several vehicles match, return the trusted candidates for selection.",
        VehicleIdentityQuery,
        preconditions=("unresolved_vehicle_description",),
        retrieval_examples=(
            "Is the named vehicle available, reserved, or sold?",
            "Can I buy this described make and model right now?",
            "Check the current stock status of a vehicle described by year, colour, and model.",
        ),
    ),
    _tool(
        "compare_vehicles",
        "Compare trusted vehicles",
        "Compare two or three specific vehicles the customer identifies from trusted displayed "
        "context. Use only for references such as these cars, options one and two, or explicit "
        "trusted selections. For customer-supplied make and model names, use "
        "compare_vehicle_models instead.",
        VehicleComparisonRuntime,
        preconditions=("trusted_vehicle_ids",),
        provider_input_model=VehicleComparison,
        retrieval_examples=(
            "Compare these two displayed cars.",
            "Compare options one and three from the current results.",
        ),
    ),
    _tool(
        "compare_vehicle_models",
        "Compare named vehicle models",
        "Resolve and compare two or three make or model descriptions written by the customer "
        "against live available stock. Use this for named models even when unrelated vehicles are "
        "visible; use compare_vehicles only when the customer selects specific displayed vehicles.",
        VehicleModelComparison,
        retrieval_examples=(
            "Compare the BMW 1 Series with the Audi A3.",
            "What is the difference between these two named vehicle models?",
        ),
    ),
    _tool(
        "list_offers",
        "Browse published offers",
        "Browse current published offers when the customer asks generally, asks for multiple "
        "offers, or supplies make, model, or PCP/PCH filters. A broad request returns live vehicle makes so "
        "the assistant can ask what the customer is considering. Set showAll=true only when the "
        "customer explicitly asks to see every offer. Do not collapse a plural request to one "
        "previously displayed offer, and do not use for finance-term definitions.",
        OfferSearch,
        retrieval_examples=(
            "Show me current offers.",
            "What PCP offers are available?",
            "Show every published offer.",
        ),
    ),
    _tool(
        "get_offer",
        "Get offer details",
        "Get details for exactly one offer the customer identifies from trusted displayed offer "
        "context. Use only for a singular reference such as this offer or option two; use "
        "list_offers for general, filtered, or plural requests.",
        StableId,
        preconditions=("trusted_offer_id",),
        reference_inputs=(("id", "offer"),),
        retrieval_examples=(
            "Tell me more about this displayed offer.",
            "Show the details for offer number two.",
        ),
    ),
    _tool(
        "list_dealerships",
        "Find dealerships",
        "Find Northstar dealership locations, addresses, phone numbers, and contact details. This "
        "directly fulfils a request to view dealership contact details; do not replace it with the "
        "contact-method chooser. With a customer-supplied town, return that resolved location; "
        "without a town, preserve plural or all-location scope. Supports spelling variation through "
        "the application resolver.",
        DealershipQuery,
        candidate_subject_field="dealershipId",
        retrieval_examples=(
            "Show me dealership contact details.",
            "Where is the nearest dealership?",
            "What are the dealership addresses and phone numbers?",
        ),
    ),
    _tool(
        "show_dealership_contact_options",
        "Choose an unspecified dealership contact method",
        "Show application-owned contact-method choices only when the customer asks generally how "
        "to contact a Northstar dealership, or accepts an earlier contact offer, without naming a "
        "method. Never use when the customer already chose a callback, message, contact details, "
        "phone number, address, or opening hours; call the corresponding concrete tool instead.",
        ContactOptionsInput,
        provider_input_model=EmptyInput,
        retrieval_examples=(
            "The customer says yes to the assistant's offer to help contact a dealership.",
            "How can I contact a dealership? I have not chosen a contact method.",
        ),
    ),
    _tool(
        "list_dealership_departments",
        "List departments at all dealerships",
        "List which sales, service, and parts departments are available across all Northstar dealerships. Use for plural, all-location, or each-dealership requests; never collapse this operation to one location.",
        EmptyInput,
        retrieval_examples=(
            "What departments do the dealerships have?",
            "What departments do all the dealerships have?",
            "List departments at each Northstar location.",
        ),
    ),
    _tool(
        "find_dealership_departments",
        "Find departments at one dealership",
        "Find sales, service, and parts departments for one customer-supplied town or the sole dealership in current context. If several dealerships are displayed and none is named, location clarification is required.",
        DealershipScopeQuery,
        preconditions=(
            "single_dealership_or_location",
            "trusted_optional_dealership_id",
        ),
        retrieval_examples=(
            "What departments does this dealership have?",
            "Which departments are at the Stockport dealership?",
        ),
    ),
    _tool(
        "get_dealership",
        "Get dealership details",
        "Get contact and location details for exactly one dealership already established as the "
        "sole trusted dealership in current context. Never select one ID from several displayed "
        "dealerships; use list_dealerships with a customer-supplied town or clarify.",
        StableId,
        preconditions=("trusted_dealership_id", "sole_displayed_dealership"),
        reference_inputs=(("id", "dealership"),),
        retrieval_examples=("Show more details for this one displayed dealership.",),
    ),
    _tool(
        "get_opening_hours",
        "Get the complete schedule for one displayed dealership",
        "Get the complete regular weekly opening-hours schedule for exactly one dealership already "
        "established as the sole trusted dealership. Use only when no weekday or department filter "
        "was requested. Use list_opening_hours for a supplied town, weekday, department, plural "
        "scope, or filtered request.",
        StableId,
        preconditions=("trusted_dealership_id", "sole_displayed_dealership"),
        reference_inputs=(("id", "dealership"),),
        retrieval_examples=(
            "Show the complete weekly schedule for this one displayed dealership.",
        ),
    ),
    _tool(
        "list_opening_hours",
        "Find opening hours",
        "Find regular opening hours across dealerships or filter them by a customer-supplied town, "
        "weekday, or department. Use this whenever one of those filters is supplied or the request "
        "has plural or all-location scope. Do not use for bank-holiday or other holiday exceptions; "
        "use list_holiday_opening_hours. With one dealership in context, preserve that location for "
        "a filtered request. With several displayed dealerships, never choose one arbitrary ID. "
        "Use calendar context to translate relative days such as tomorrow.",
        OpeningHoursQuery,
        preconditions=(
            "single_dealership_or_all",
            "trusted_optional_dealership_id",
        ),
        retrieval_examples=(
            "Show opening hours for all dealerships.",
            "What time does the Stockport dealership open on Saturday?",
            "What are the service department opening hours?",
        ),
    ),
    _tool(
        "list_holiday_opening_hours",
        "Find holiday opening hours",
        "Show published bank-holiday and other holiday opening-hour exceptions without mixing in regular weekday hours. Return all dealerships unless the customer supplies a town or exactly one dealership is established by trusted context.",
        HolidayOpeningHoursQuery,
        preconditions=(
            "single_dealership_or_all",
            "trusted_optional_dealership_id",
        ),
        retrieval_examples=(
            "Are you open on the bank holiday?",
            "Show the published holiday opening hours.",
            "What are the Christmas or public-holiday hours?",
        ),
    ),
    _tool(
        "get_business_information",
        "Answer confirmed business policy",
        "Look up authoritative Northstar finance, privacy, part-exchange, or general business policy "
        "for the customer's exact factual question. Requests to get, receive, understand, or explain "
        "information remain read-only even when they mention a displayed vehicle or a transactional "
        "topic such as finance. The semantic resolver must still resolve any referenced vehicle or "
        "other entity as conversational context; this tool's reference-free schema does not permit "
        "that part of the customer's meaning to be discarded. Use for policy or explanatory information, not for starting an "
        "enquiry, callback, message, booking, or valuation workflow. An unavailable result means the "
        "policy is not confirmed.",
        BusinessInformationQuery,
        retrieval_examples=(
            "What is your privacy policy?",
            "Explain Northstar's finance or part-exchange policy.",
            "Give me finance information relevant to the vehicle I am viewing.",
        ),
    ),
    _tool(
        "get_service_information",
        "Get details for one named workshop service",
        "Answer an information question about one workshop service named by the customer, including "
        "whether it is supported, its live price or cost, duration, and description. Use this even "
        "when the customer's singular description is broad, ambiguous, misspelled, or unavailable; "
        "the live catalogue, not semantic planning, owns that resolution. Use list_service_types "
        "only for an explicitly plural or browse-all request. Do not start or refine an appointment search unless "
        "the customer also asks to book or find availability.",
        ServiceQuery,
        retrieval_examples=(
            "Do you offer wheel alignment?",
            "Do you offer a service?",
            "What does an interim service cost and how long does it take?",
            "How much is tyre fitting?",
            "Do you provide car cleaning?",
        ),
    ),
    _tool(
        "list_service_types",
        "Browse workshop services",
        "List every currently supported Northstar workshop service when the customer asks broadly "
        "what services are available. This is an information catalogue, not the start of a booking. "
        "Do not use when the customer names a specific service; use get_service_information. Do not "
        "use when the customer asks to book; use list_workshop_slots.",
        EmptyInput,
        candidate_subject_field="serviceTypeId",
        retrieval_examples=(
            "What workshop service types do you support?",
            "What workshop services do you offer?",
            "Show the complete workshop service catalogue.",
        ),
    ),
    _tool(
        "list_workshop_locations",
        "Find workshop locations",
        "List dealership locations that provide workshop services when the customer specifically "
        "asks where workshops or service centres are located. Do not use for general dealership "
        "addresses or contact details, and do not start a booking unless the customer asks to book.",
        EmptyInput,
        candidate_subject_field="dealershipId",
        retrieval_examples=(
            "Where are your workshop locations?",
            "Which dealerships have a service centre?",
        ),
    ),
    _tool(
        "list_test_drive_slots",
        "Find test-drive times for the active request",
        "Find current test-drive times after the test-drive capability is active and one trusted, "
        "currently available vehicle has been selected. Do not use this to start a new test-drive "
        "request; use prepare_test_drive first. Do not invent or arbitrarily select a vehicle. "
        "When a previous day/time had no match and the customer asks what is available, set "
        "schedulePreferenceMode=clear so the failed preference is not silently reapplied.",
        Filters,
        preconditions=("trusted_vehicle_id",),
        provider_input_model=TestDriveSlotAgentInput,
        invocation="internal",
        retrieval_examples=(
            "For my active test-drive request, show times for this selected vehicle.",
        ),
    ),
    _tool(
        "list_workshop_slots",
        "Start a new workshop booking or availability search",
        "Start a new workshop booking or availability journey in service, dealership, appointment "
        "order. Without a service it returns service choices; without a dealership it returns live "
        "workshop locations; only then does it return appointment times. Do not use for information "
        "about a named service, and do not change an existing confirmed booking before verification.",
        Filters,
        preconditions=("trusted_optional_dealership_id",),
        provider_input_model=WorkshopSlotAgentInput,
        retrieval_examples=(
            "I want to book a new workshop appointment.",
            "Find workshop times for a service, optionally at a location or date.",
            "Book an MOT.",
        ),
    ),
    _tool(
        "refine_workshop_slots",
        "Refine the active workshop availability search",
        "Continue from a displayed workshop-service choice, or change the service, location, or date "
        "of the active workshop availability search while preserving every unspecified constraint. "
        "A newly supplied service replaces the previous service. Do not answer service-information "
        "questions and do not amend an existing confirmed booking unless verification and amendment "
        "state are already active. After a schedule preference has no matches, use "
        "schedulePreferenceMode=clear when the customer asks what options are actually available; "
        "use replace when they provide a different day/time.",
        Filters,
        preconditions=("current_workshop_search", "trusted_optional_dealership_id"),
        provider_input_model=WorkshopSlotAgentInput,
        retrieval_examples=(
            "Find a different location or date for that workshop service.",
            "Is there workshop availability in this town for it?",
            "Change my current workshop booking search from interim service to tyre fitting.",
        ),
    ),
    _tool(
        "request_workshop_booking_lookup_form",
        "Verify an existing workshop booking",
        "Always start here when the customer asks to find, view, amend, change, or cancel an existing "
        "workshop booking that has not yet been verified. This starts secure conversational "
        "verification; the customer may provide all four verification details together. Do not call "
        "an amendment or cancellation preparation tool before verification succeeds.",
        BookingLookupForm,
        retrieval_examples=(
            "Find my existing workshop booking.",
            "I need to change an existing workshop booking that has not been verified yet.",
            "I need to cancel an existing workshop booking that has not been verified yet.",
        ),
    ),
    _tool(
        "request_part_exchange_estimate_form",
        "Start an indicative part-exchange valuation",
        "Use when the customer asks to value, appraise, or get an indicative estimate for their "
        "vehicle. Start the protected input channel that collects registration, mileage, and "
        "condition without exposing those values to the model. This produces an estimate only; it "
        "does not start a dealership follow-up enquiry.",
        PartExchangeEstimateForm,
        provider_input_model=EmptyInput,
        retrieval_examples=(
            "Value my car.",
            "Give me an indicative part-exchange estimate.",
            "How much might my vehicle be worth in part exchange?",
        ),
    ),
    _tool(
        "estimate_part_exchange",
        "Calculate a protected part-exchange estimate",
        "Internal calculation performed only after the protected input channel has collected "
        "registration, mileage, and condition. This operation is never selected from customer text.",
        PartExchangeEstimate,
        invocation="internal",
    ),
    _tool(
        "request_offer_enquiry_form",
        "Start offer sales enquiry",
        "Start the conversational sales-enquiry collector for an authoritative selected offer without persisting or submitting the enquiry. This internal capability is never planner-callable.",
        WorkflowDraftInput,
        mode="workflow",
        invocation="internal",
    ),
    _tool(
        "prepare_sales_enquiry",
        "Start or continue a vehicle sales enquiry",
        "Start or continue a sales enquiry when the customer explicitly asks to send a question "
        "or buying request to the sales team, or asks a dealership to follow up. A factual stock-"
        "status or 'can I buy it?' question is vehicle availability and must be answered from "
        "inventory instead; interest alone is not permission to create an enquiry. Merely asking "
        "for finance, offer, vehicle, or part-exchange information is also not permission to start "
        "this workflow, even when a vehicle is referenced. Use only public "
        "values supplied by the customer or trusted context, and capture message only from relevant "
        "customer-authored content. Do not use for a generic dealership message, callback, contact "
        "details, or workshop request. Existing values are retained and a protected channel gathers "
        "contact details. This never submits the enquiry.",
        WorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("contextual_dealership_prefill", "trusted_workflow_entities"),
        provider_input_model=SalesEnquiryAgentInput,
        retrieval_examples=(
            "I want to make a sales enquiry about buying a vehicle.",
            "Please send the sales team my question about this car.",
            "Continue my active sales enquiry with the missing dealership or enquiry type.",
        ),
    ),
    _tool(
        "prepare_test_drive",
        "Start or update a test-drive request",
        "Start the test-drive capability as soon as the customer asks to book a test drive or "
        "arrange a vehicle viewing, even when vehicle and appointment are still missing. Also use "
        "it to retain trusted vehicle or appointment selections after the capability has started. "
        "The application resolves inventory and live-time dependencies. Existing values are retained "
        "and a protected channel gathers contact details. This "
        "never confirms the booking.",
        AppointmentWorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("trusted_workflow_entities",),
        provider_input_model=TestDriveAgentInput,
        retrieval_examples=(
            "I want to book a test drive but have not chosen a vehicle yet.",
            "Arrange a test drive for me.",
            "I'd like to arrange a viewing for this car.",
            "Book an appointment for the BMW 3 Series.",
            "Arrange an appointment for this vehicle.",
            "Continue a test drive after the customer chooses one trusted appointment.",
        ),
    ),
    _tool(
        "prepare_vehicle_interest",
        "Register interest in a reserved vehicle",
        "Start or continue a request to register interest in one trusted vehicle that is currently "
        "reserved. Capture optional notes only from exact relevant customer-authored content. Do not "
        "use for general vehicle details, availability checks, or sales enquiries. A protected "
        "channel gathers contact details. This never registers interest without confirmation.",
        WorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("trusted_vehicle_id",),
        provider_input_model=VehicleInterestAgentInput,
        retrieval_examples=("Register my interest in this reserved vehicle.",),
    ),
    _tool(
        "prepare_callback",
        "Start or continue a dealership callback request",
        "Use when the customer explicitly asks Northstar or a dealership department to call or "
        "phone them back. Do not use when they ask to send or leave a message, view contact details, "
        "or check opening hours. Use public dealership, department, reason, preferred-time, or "
        "trusted-vehicle values, and capture free text only from relevant customer-authored content. "
        "Existing values are retained and a protected channel gathers contact details. This never "
        "requests the callback without confirmation.",
        WorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("contextual_dealership_prefill", "trusted_workflow_entities"),
        provider_input_model=CallbackAgentInput,
        retrieval_examples=(
            "Please ask the dealership to call me back.",
            "Request a callback from the service department.",
            "Continue my callback request with the missing dealership or department.",
        ),
    ),
    _tool(
        "prepare_workshop_booking",
        "Prepare a selected workshop appointment",
        "Continue an active new workshop booking only after a trusted service, dealership, and live "
        "appointment slot have been resolved. Capture optional notes only from exact relevant "
        "customer-authored content. Do not use to start availability discovery or to change an "
        "existing confirmed booking. Existing values are retained and a protected channel gathers "
        "vehicle and contact details. This never confirms the booking.",
        AppointmentWorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("trusted_workflow_entities",),
        provider_input_model=WorkshopBookingAgentInput,
        retrieval_examples=(
            "Continue the active workshop booking with a selected trusted appointment time.",
        ),
    ),
    _tool(
        "prepare_workshop_amendment",
        "Prepare an amendment to a verified booking",
        "Use only after an existing workshop booking has been successfully verified and the "
        "customer supplies a change such as mileage, notes, or one selected trusted replacement "
        "slot. Never use to begin an unverified booking lookup or a new availability search. This "
        "never applies the change without application confirmation.",
        WorkshopAmendmentInput,
        risk="draft",
        mode="workflow",
        preconditions=("verified_booking", "trusted_workflow_entities"),
        provider_input_model=WorkshopAmendmentAgentInput,
        trusted_input_model=WorkshopAmendmentSlotContext,
        retrieval_examples=(
            "Update the mileage on my verified workshop booking.",
            "Use this selected replacement slot for my verified booking.",
        ),
    ),
    _tool(
        "prepare_workshop_cancellation",
        "Prepare cancellation of a verified booking",
        "Use only after an existing workshop booking has been successfully verified and the "
        "customer still wants to cancel it. Never use to begin an unverified booking lookup. No "
        "public customer fields are required at this stage. This never cancels without application "
        "confirmation.",
        WorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("verified_booking",),
        provider_input_model=EmptyInput,
        retrieval_examples=("Prepare cancellation of the booking that was just verified.",),
    ),
    _tool(
        "prepare_dealership_message",
        "Start or continue a message to a dealership department",
        "Use whenever the customer explicitly asks to send, write, or leave a message for a "
        "dealership or department, even when the dealership, department, subject, or message body is "
        "still missing. Do not show the contact-method chooser, request a callback, or start a sales "
        "enquiry unless the customer asked for those instead. Use public routing values plus exact "
        "customer-authored subject and message excerpts. Existing values are retained and a "
        "protected channel gathers contact details. This never sends anything without confirmation.",
        DealershipMessageDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("contextual_dealership_prefill", "trusted_workflow_entities"),
        provider_input_model=DealershipMessageAgentInput,
        retrieval_examples=(
            "Leave a message for a dealership department.",
            "Send a message to the service department.",
            "I want to write to the dealership.",
        ),
    ),
    _tool(
        "prepare_part_exchange",
        "Start or continue a part-exchange follow-up enquiry",
        "Use when the customer explicitly wants a dealership to follow up about proceeding with a "
        "part exchange, including after an indicative estimate. Do not use for a valuation-only "
        "request; request_part_exchange_estimate_form owns that. Use a public dealership choice, "
        "while a protected channel gathers vehicle and contact details. This never submits anything "
        "without confirmation.",
        WorkflowDraftInput,
        risk="draft",
        mode="workflow",
        preconditions=("contextual_dealership_prefill", "trusted_workflow_entities"),
        provider_input_model=PartExchangeAgentInput,
        retrieval_examples=(
            "I want to proceed with a part-exchange enquiry.",
            "Have a dealership follow up about my part exchange.",
        ),
    ),
)
