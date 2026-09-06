"""Declarative workflow capability contracts.

The agent owns conversational wording.  These contracts own only the information and privacy
boundaries required to complete an operation; they deliberately contain no scripted questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class LiveFieldResolver:
    """Declare how an unresolved public field is obtained from live business data."""

    field: str
    tool: str
    prerequisite_fields: tuple[str, ...]
    filter_fields: tuple[str, ...] = ()
    replacement_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilitySpec:
    kind: str
    agent_tool: str
    public_fields: tuple[str, ...]
    secure_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    public_steps: tuple[tuple[str, ...], ...] = ()
    live_field_resolvers: tuple[LiveFieldResolver, ...] = ()
    continuation_tools: tuple[str, ...] = ()

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset((*self.public_fields, *self.secure_fields)) - frozenset(
            self.optional_fields
        )

    def missing(self, supplied: set[str]) -> tuple[list[str], list[str]]:
        required = self.required_fields
        return (
            [field for field in self.public_fields if field in required and field not in supplied],
            [field for field in self.secure_fields if field in required and field not in supplied],
        )

    def next_missing_public(self, missing: set[str]) -> list[str]:
        """Return one dependency-safe field for the next conversational question."""

        steps = self.public_steps or (self.public_fields,)
        for step in steps:
            requested = next((field for field in step if field in missing), None)
            if requested:
                return [requested]
        return []


class CapabilityRegistry:
    """Single source of truth for conversational workflow data requirements."""

    # Finite public fields must be accompanied by authoritative choices when they are the next
    # unresolved question. This metadata is about data provenance, not scripted wording.
    CHOICE_SOURCES: ClassVar[dict[tuple[str, str], str]] = {
        ("*", "dealershipId"): "dealership_directory",
        ("callback", "department"): "callback_departments",
        ("dealership_message", "department"): "message_departments",
        ("sales_enquiry", "enquiryType"): "sales_enquiry_types",
        ("dealership_message", "preferredContactMethod"): "contact_methods",
    }
    STATIC_CHOICES: ClassVar[dict[str, tuple[tuple[str, str], ...]]] = {
        "callback_departments": (
            ("sales", "Sales"),
            ("service", "Service"),
            ("parts", "Parts"),
        ),
        "message_departments": (
            ("sales", "Sales"),
            ("service", "Service"),
            ("parts", "Parts"),
            ("general", "General enquiries"),
        ),
        "sales_enquiry_types": (
            ("general", "General enquiry"),
            ("availability", "Vehicle availability"),
            ("finance", "Finance"),
            ("part-exchange", "Part exchange"),
        ),
        "contact_methods": (("email", "Email"), ("phone", "Phone")),
    }

    SPECS: ClassVar[dict[str, CapabilitySpec]] = {
        "sales_enquiry": CapabilitySpec(
            "sales_enquiry",
            "prepare_sales_enquiry",
            ("vehicleId", "dealershipId", "enquiryType", "message"),
            ("firstName", "lastName", "email", "phone"),
            ("vehicleId",),
        ),
        "test_drive": CapabilitySpec(
            "test_drive",
            "prepare_test_drive",
            ("vehicleId", "slotId"),
            ("firstName", "lastName", "email", "phone"),
            public_steps=(("vehicleId",), ("slotId",)),
            live_field_resolvers=(
                LiveFieldResolver(
                    "vehicleId",
                    "search_vehicles",
                    (),
                    ("make", "model", "q", "dealershipId", "dealershipTown"),
                    ("make", "model", "q"),
                ),
                LiveFieldResolver(
                    "slotId",
                    "list_test_drive_slots",
                    ("vehicleId",),
                    (
                        "dealershipId",
                        "dealershipTown",
                        "dateFrom",
                        "dateTo",
                        "timeOfDay",
                    ),
                ),
            ),
            continuation_tools=("search_vehicles", "list_test_drive_slots"),
        ),
        "vehicle_interest": CapabilitySpec(
            "vehicle_interest",
            "prepare_vehicle_interest",
            ("vehicleId", "notes"),
            ("firstName", "lastName", "email", "phone"),
            ("notes",),
        ),
        "callback": CapabilitySpec(
            "callback",
            "prepare_callback",
            ("dealershipId", "department", "reason", "preferredTime", "vehicleId"),
            ("firstName", "lastName", "email", "phone"),
            ("vehicleId", "preferredTime"),
            public_steps=(("dealershipId",), ("department", "reason")),
        ),
        "workshop_booking": CapabilitySpec(
            "workshop_booking",
            "prepare_workshop_booking",
            ("serviceTypeId", "dealershipId", "slotId", "notes"),
            ("registration", "mileage", "firstName", "lastName", "email", "phone"),
            ("notes",),
            public_steps=(("serviceTypeId",), ("dealershipId",), ("slotId",)),
            continuation_tools=(
                "list_service_types",
                "list_workshop_locations",
                "list_workshop_slots",
                "refine_workshop_slots",
            ),
        ),
        "dealership_message": CapabilitySpec(
            "dealership_message",
            "prepare_dealership_message",
            ("dealershipId", "department", "subject", "message", "preferredContactMethod"),
            ("firstName", "lastName", "email", "phone"),
            public_steps=(
                ("dealershipId",),
                ("department", "subject", "message", "preferredContactMethod"),
            ),
        ),
        "part_exchange": CapabilitySpec(
            "part_exchange",
            "prepare_part_exchange",
            ("dealershipId",),
            ("registration", "mileage", "condition", "firstName", "lastName", "email", "phone"),
        ),
        "booking_lookup": CapabilitySpec(
            "booking_lookup",
            "request_workshop_booking_lookup_form",
            (),
            ("reference", "lastName", "registration", "phone"),
            continuation_tools=(
                "list_workshop_slots",
                "refine_workshop_slots",
                "prepare_workshop_amendment",
                "prepare_workshop_cancellation",
            ),
        ),
        "part_exchange_estimate": CapabilitySpec(
            "part_exchange_estimate",
            "request_part_exchange_estimate_form",
            (),
            ("registration", "mileage", "condition"),
        ),
    }

    ACTIVE_WORKFLOW_ALIASES: ClassVar[dict[str, str]] = {
        "existing_workshop_booking": "booking_lookup",
        "workshop_amend": "booking_lookup",
        "workshop_cancel": "booking_lookup",
    }
    ACTIVE_WORKFLOW_GOAL_INTENTS: ClassVar[dict[str, str]] = {
        "existing_workshop_booking": "booking_lookup",
        "workshop_amend": "booking_amendment",
        "workshop_cancel": "booking_cancellation",
        "part_exchange_estimate": "part_exchange",
    }

    @classmethod
    def secure_edit_field(cls, kind: str, requested_fields: list[str]) -> str | None:
        """Map semantic secure-field targets to the collector's canonical edit field."""

        spec = cls.for_active_workflow(kind)
        if spec is None:
            return None
        canonical = []
        collects_full_name = {"firstName", "lastName"}.issubset(spec.secure_fields)
        for field in requested_fields:
            if field in {"fullName", "firstName"} and collects_full_name:
                canonical.append("fullName")
            elif field in spec.secure_fields:
                canonical.append(field)
        unique = list(dict.fromkeys(canonical))
        return unique[0] if len(unique) == 1 else None

    @classmethod
    def get(cls, kind: str) -> CapabilitySpec:
        try:
            return cls.SPECS[kind]
        except KeyError as error:
            raise ValueError(f"unsupported capability: {kind}") from error

    @classmethod
    def for_agent_tool(cls, tool_name: str) -> CapabilitySpec | None:
        return next(
            (spec for spec in cls.SPECS.values() if spec.agent_tool == tool_name),
            None,
        )

    @classmethod
    def for_active_workflow(cls, kind: str) -> CapabilitySpec | None:
        return cls.SPECS.get(cls.ACTIVE_WORKFLOW_ALIASES.get(kind, kind))

    @classmethod
    def goal_intent_for_active_workflow(cls, kind: str) -> str:
        """Return the semantic goal owned by a durable workflow state name."""

        if kind in cls.ACTIVE_WORKFLOW_GOAL_INTENTS:
            return cls.ACTIVE_WORKFLOW_GOAL_INTENTS[kind]
        spec = cls.for_active_workflow(kind)
        return spec.kind if spec is not None else kind

    @classmethod
    def is_transactional_workflow(cls, kind: str) -> bool:
        """Return whether durable state belongs to a registered transaction.

        Workflow/dialogue transition code must derive this boundary from the capability
        registry so a newly registered transaction cannot silently lose interruption handling.
        """

        return cls.for_active_workflow(kind) is not None

    @classmethod
    def private_fields(cls) -> frozenset[str]:
        return frozenset(field for spec in cls.SPECS.values() for field in spec.secure_fields)

    @classmethod
    def choice_source(cls, kind: str, field: str) -> str | None:
        return cls.CHOICE_SOURCES.get((kind, field)) or cls.CHOICE_SOURCES.get(("*", field))

    @classmethod
    def static_choices(cls, source: str) -> tuple[tuple[str, str], ...]:
        return cls.STATIC_CHOICES.get(source, ())
