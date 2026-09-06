"""Workshop services, locations, slots, and test-drive read tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from webchat.domain.business_semantics import money
from webchat.orchestration.appointments import (
    appointment_choices,
    appointment_collection_payload,
    appointment_fallback_queries,
)
from webchat.orchestration.contracts.presentation import CollectionPresentation
from webchat.orchestration.presentation.suggestions import (
    service_type_suggestions,
    test_drive_no_availability_suggestions,
    unknown_dealership_suggestions,
    workshop_booking_location_suggestions,
    workshop_location_suggestions,
    workshop_no_availability_suggestions,
)
from webchat.orchestration.tools.helpers import dealership_in_town, dealership_towns
from webchat.orchestration.tools.inputs import Filters, ServiceQuery
from webchat.orchestration.tools.result import ToolResult, alternative_offer
from webchat.orchestration.tools.service_resolution import resolve_live_service
from webchat.orchestration.tools.vehicles import (
    vehicle_availability_result,
    vehicle_item,
)
from webchat.orchestration.workflow_kernel import (
    test_drive_slot_resolution,
    workshop_slot_resolution,
)


class WorkshopGateway(Protocol):
    async def search_vehicles(self, filters: dict[str, Any]) -> dict[str, Any]: ...

    async def list_service_types(self) -> dict[str, Any]: ...

    async def list_workshop_locations(self) -> dict[str, Any]: ...

    async def list_workshop_slots(self, filters: dict[str, Any]) -> dict[str, Any]: ...

    async def list_test_drive_slots(self, filters: dict[str, Any]) -> dict[str, Any]: ...

    async def list_dealerships(self) -> dict[str, Any]: ...

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]: ...

    async def get_vehicle_availability(self, vehicle_id: str) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class WorkshopReadToolHandler:
    """Own workshop catalogue, location, and live slot reads."""

    def __init__(self, dealership: WorkshopGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "get_service_information": self._service_information,
            "list_service_types": self._list_service_types,
            "list_workshop_locations": self._list_workshop_locations,
            "list_test_drive_slots": self._test_drive_slots,
            "list_workshop_slots": self._workshop_slots,
            "refine_workshop_slots": self._workshop_slots,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _service_information(self, arguments: dict[str, Any]) -> ToolResult:
        query = ServiceQuery.model_validate(arguments)
        data = await self.dealership.list_service_types()
        items = data.get("items", [])
        matched = next(
            (
                item
                for item in items
                if query.serviceTypeId is not None and str(item.get("id")) == query.serviceTypeId
            ),
            None,
        )
        if matched is None and query.q is not None:
            resolution = resolve_live_service(items, query.q)
            if resolution.status != "matched":
                return _service_resolution_result(resolution.status, resolution.candidates)
            matched = resolution.service
        if matched is None:
            return _service_resolution_result("unavailable", ())
        name = str(matched.get("name") or "This service")
        price_value = matched.get("priceFromPence")
        price = (
            f"from {money(price_value)}" if isinstance(price_value, int) else "priced on request"
        )
        trusted_service = {
            **matched,
            "pricingStatus": (
                "published" if isinstance(price_value, int) else "priced_on_request"
            ),
        }
        duration = matched.get("durationMinutes")
        duration_text = (
            f"About {duration} minutes" if isinstance(duration, int) else "Not confirmed"
        )
        description = str(matched.get("description") or "").strip()
        details = [f"Price: {price}", f"Duration: {duration_text}"]
        if description:
            details.append(f"Description: {description}")
        return ToolResult(
            f"{name}:\n" + "\n".join(f"- {detail}" for detail in details),
            None,
            None,
            {
                "resolution": {"status": "matched"},
                "service": trusted_service,
            },
        )

    async def _list_service_types(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        data = await self.dealership.list_service_types()
        items = data.get("items", [])
        payload = {
            "version": 1,
            "items": items,
            "suggestions": service_type_suggestions(items),
        }
        if presentation := _service_collection_presentation(items, "information"):
            payload["collectionPresentation"] = presentation
        return ToolResult(
            "We currently provide these workshop services:",
            "service_list",
            payload,
            data,
        )

    async def _list_workshop_locations(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        data = await self.dealership.list_workshop_locations()
        return ToolResult(
            "Here are our workshop locations.",
            "workshop_location_list",
            {
                "version": 1,
                "items": data.get("items", []),
                "suggestions": workshop_location_suggestions(),
            },
            data,
        )

    async def _test_drive_slots(self, arguments: dict[str, Any]) -> ToolResult:
        filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
        filters.pop("schedulePreferenceMode", None)
        time_of_day = filters.pop("timeOfDay", None)
        town = filters.pop("dealershipTown", None)
        if town and not filters.get("dealershipId"):
            dealerships = list((await self.dealership.list_dealerships()).get("items", []))
            matched = dealership_in_town(dealerships, str(town))
            if matched is None:
                locations = dealership_towns(dealerships)
                return ToolResult(
                    f"Northstar does not currently have a dealership in {str(town).title()}.",
                    "suggestion_list",
                    {"version": 1, "suggestions": unknown_dealership_suggestions()},
                    {
                        "outcome": "unavailable",
                        "requestedTown": town,
                        "items": [],
                    },
                    alternative_offer=alternative_offer(
                        reason_code="requested_dealership_location_unavailable",
                        requested_outcome=f"A test drive in {str(town).title()}",
                        failure_reason=(
                            f"Northstar does not currently have a dealership in "
                            f"{str(town).title()}."
                        ),
                        offered_outcome=(
                            f"Choose a current Northstar location: {locations}."
                        ),
                        changes=[
                            {
                                "dimension": "Location",
                                "requested": str(town).title(),
                                "offered": locations,
                            }
                        ],
                        preserved=["Selected vehicle", "Test-drive request"],
                    ),
                )
            filters["dealershipId"] = str(matched["id"])
        vehicle_id = str(filters["vehicleId"])
        availability = await self.dealership.get_vehicle_availability(vehicle_id)
        if availability.get("availability") != "available":
            result = await vehicle_availability_result(self.dealership, vehicle_id, availability)
            facts = dict(result.facts or {})
            facts["resolution"] = test_drive_slot_resolution(
                arguments, count=0, vehicle_available=False
            )
            return ToolResult(
                result.text,
                result.view_type,
                result.view_payload,
                facts,
                result.interaction,
                result.alternative_offer,
            )
        selected_vehicle = await self.dealership.get_vehicle(vehicle_id)
        schedule_requested = (
            any(arguments.get(field) is not None for field in ("dateFrom", "dateTo", "timeOfDay"))
            or arguments.get("schedulePreferenceMode") == "clear"
        )
        if not schedule_requested:
            location = str(
                selected_vehicle.get("dealershipName")
                or selected_vehicle.get("dealershipTown")
                or "the selected dealership"
            )
            return ToolResult(
                f"The selected vehicle at {location} is ready for appointment scheduling.",
                None,
                None,
                {
                    # Operational identity remains in the resolution contract below. Public
                    # response facts receive the customer-facing identity projection instead.
                    "vehicle": vehicle_item(selected_vehicle),
                    "resolution": test_drive_slot_resolution(arguments, count=0),
                },
            )
        now = datetime.now(UTC)
        if arguments.get("schedulePreferenceMode") == "clear":
            filters.setdefault("dateFrom", now.date().isoformat())
        data = await self.dealership.list_test_drive_slots(filters)
        items = _future_slots(
            list(data.get("items", [])),
            now,
            time_of_day=time_of_day,
            date_from=filters.get("dateFrom"),
            date_to=filters.get("dateTo"),
        )
        matching_items = items
        schedule_alternatives: list[dict[str, Any]] = []
        location_alternatives: list[dict[str, Any]] = []
        equivalent_vehicle_slots: list[dict[str, Any]] = []
        equivalent_schedule_relaxed = False
        filtered = any(
            arguments.get(field) is not None for field in ("dateFrom", "dateTo", "timeOfDay")
        )
        fallback_queries = appointment_fallback_queries(
            filters, earliest_date=now.date().isoformat()
        )
        if not matching_items and filtered:
            broad = await self.dealership.list_test_drive_slots(
                fallback_queries["same_target_next"]
            )
            schedule_alternatives = _future_slots(list(broad.get("items", [])), now)
            items = schedule_alternatives
        if not matching_items and not schedule_alternatives and filters.get("dealershipId"):
            other_location_data = await self.dealership.list_test_drive_slots(
                fallback_queries["other_location_requested_schedule"]
            )
            location_alternatives = _future_slots(
                list(other_location_data.get("items", [])),
                now,
                time_of_day=time_of_day,
                date_from=filters.get("dateFrom"),
                date_to=filters.get("dateTo"),
            )[:4]
            items = location_alternatives
        if not matching_items and not schedule_alternatives and not location_alternatives:
            equivalent_vehicles = list(
                (
                    await self.dealership.search_vehicles(
                        {
                            "make": selected_vehicle.get("make"),
                            "model": selected_vehicle.get("model"),
                            "availability": "available",
                            "pageSize": 100,
                        }
                    )
                ).get("items", [])
            )
            alternatives_by_id = {
                str(vehicle.get("id")): vehicle
                for vehicle in equivalent_vehicles
                if vehicle.get("id") and str(vehicle.get("id")) != vehicle_id
            }
            if alternatives_by_id:
                equivalent_filters = {
                    key: value for key, value in filters.items() if key != "vehicleId"
                }
                # A model-level test-drive request is resolved to one stock vehicle, but the
                # dealership remains part of the customer's chosen context. Keep that location
                # when checking equivalent stock instead of silently widening both dimensions.
                selected_dealership_id = str(
                    filters.get("dealershipId")
                    or selected_vehicle.get("dealershipId")
                    or ""
                ).strip()
                if selected_dealership_id:
                    equivalent_filters["dealershipId"] = selected_dealership_id
                equivalent_queries = appointment_fallback_queries(
                    equivalent_filters,
                    earliest_date=now.date().isoformat(),
                )
                broad_alternative_data = await self.dealership.list_test_drive_slots(
                    equivalent_filters
                )
                equivalent_vehicle_slots = _equivalent_vehicle_appointments(
                    list(broad_alternative_data.get("items", [])),
                    now,
                    alternatives_by_id,
                    time_of_day=time_of_day,
                    date_from=filters.get("dateFrom"),
                    date_to=filters.get("dateTo"),
                )
                if not equivalent_vehicle_slots:
                    # The requested schedule has now failed for the selected vehicle and every
                    # equivalent listing. Relax only the schedule, keep model and dealership,
                    # and offer the next concrete appointments instead of asking the customer
                    # to guess another day that may also be unavailable.
                    relaxed_data = await self.dealership.list_test_drive_slots(
                        equivalent_queries["same_target_next"]
                    )
                    equivalent_vehicle_slots = _equivalent_vehicle_appointments(
                        list(relaxed_data.get("items", [])),
                        now,
                        alternatives_by_id,
                    )
                    equivalent_schedule_relaxed = bool(equivalent_vehicle_slots)
                items = equivalent_vehicle_slots
        items = appointment_choices(items)
        data["items"] = items
        payload = {
            "version": 1,
            "items": items,
            "vehicleId": filters.get("vehicleId"),
        }
        if filters.get("vehicleId"):
            payload["vehicle"] = vehicle_item(
                selected_vehicle or await self.dealership.get_vehicle(filters["vehicleId"])
            )
        if schedule_alternatives:
            payload["emptyMessage"] = (
                "No test-drive times matched that preference. Here are the next available times "
                "for the selected vehicle."
            )
            payload["requestedPreferenceHadNoMatch"] = True
            disclosure = alternative_offer(
                reason_code="requested_schedule_no_match",
                requested_outcome=f"The selected vehicle at {_schedule_label(arguments)}",
                failure_reason="No test-drive appointments matched the requested schedule.",
                offered_outcome=(
                    "Here are the next available times for the same selected vehicle."
                ),
                changes=[
                    {
                        "dimension": "Appointment schedule",
                        "requested": _schedule_label(arguments),
                        "offered": "the next available times shown",
                    }
                ],
                preserved=["Selected vehicle", *(_location_preserved(arguments))],
                candidate_references=_appointment_references(items),
            )
        elif location_alternatives:
            payload["emptyMessage"] = (
                "No test-drive times are available for this vehicle at the requested dealership. "
                "The listed times are for the same vehicle at another dealership."
            )
            payload["requestedDealershipHadNoAvailability"] = True
            locations = _slot_locations(items)
            disclosure = alternative_offer(
                reason_code="requested_location_no_availability",
                requested_outcome=(
                    f"The selected vehicle at the requested dealership, {_schedule_label(arguments)}"
                ),
                failure_reason="The requested dealership has no matching test-drive appointments.",
                offered_outcome=(
                    f"Here are matching times for the same vehicle at "
                    f"{_joined(locations) or 'another dealership'}."
                ),
                changes=[
                    {
                        "dimension": "Dealership",
                        "requested": "the requested dealership",
                        "offered": _joined(locations) or "another dealership",
                    }
                ],
                preserved=["Selected vehicle", _schedule_label(arguments)],
                candidate_references=_appointment_references(items),
            )
        elif equivalent_vehicle_slots:
            payload["emptyMessage"] = (
                (
                    "No appointments matched the requested schedule for the selected vehicle "
                    "or another listing of the same model. The listed times are the next "
                    "available appointments for another currently available listing."
                )
                if equivalent_schedule_relaxed
                else (
                    "No future times are available for the selected vehicle. The listed times "
                    "are for another currently available listing of the same make and model."
                )
            )
            payload["requestedVehicleHadNoAvailability"] = True
            if equivalent_schedule_relaxed:
                payload["requestedPreferenceHadNoMatch"] = True
            requested_vehicle = _vehicle_label(selected_vehicle or {})
            model_identity = (
                " ".join(
                    value
                    for value in (
                        str((selected_vehicle or {}).get("make") or "").strip(),
                        str((selected_vehicle or {}).get("model") or "").strip(),
                    )
                    if value
                )
                or "the same make and model"
            )
            changes = [
                {
                    "dimension": "Vehicle listing",
                    "requested": requested_vehicle,
                    "offered": "a different stock listing identified with each appointment",
                }
            ]
            preserved = [model_identity, *(_location_preserved(arguments))]
            if equivalent_schedule_relaxed:
                changes.append(
                    {
                        "dimension": "Appointment schedule",
                        "requested": _schedule_label(arguments),
                        "offered": "the next available times shown",
                    }
                )
            else:
                preserved.append(_schedule_label(arguments))
            disclosure = alternative_offer(
                reason_code=(
                    "selected_vehicle_and_schedule_no_match"
                    if equivalent_schedule_relaxed
                    else "selected_vehicle_no_future_appointments"
                ),
                requested_outcome=f"{requested_vehicle} at {_schedule_label(arguments)}",
                failure_reason=(
                    "No test-drive appointments matched the requested schedule for the selected "
                    "vehicle or another listing of the same model."
                    if equivalent_schedule_relaxed
                    else "The exact selected vehicle has no future test-drive appointments."
                ),
                offered_outcome=(
                    f"Here are the next available times for a different currently available "
                    f"{model_identity} listing."
                    if equivalent_schedule_relaxed
                    else (
                        f"Here are matching times for a different currently available "
                        f"{model_identity} listing."
                    )
                ),
                changes=changes,
                preserved=preserved,
                candidate_references=_appointment_references(items),
            )
        elif not items:
            payload["emptyMessage"] = (
                "No online test-drive times are currently available for this vehicle."
            )
            payload["suggestions"] = test_drive_no_availability_suggestions(vehicle_id)
            disclosure = None
        else:
            disclosure = None
        data["resolution"] = test_drive_slot_resolution(
            arguments,
            count=len(matching_items),
            schedule_alternative_count=len(schedule_alternatives),
            location_alternative_count=len(location_alternatives),
            equivalent_vehicle_slot_count=len(equivalent_vehicle_slots),
            schedule_search_exhausted=filtered,
        )
        return _slot_result(
            "test_drive_slot_picker",
            items,
            payload,
            data,
            alternative=disclosure,
        )

    async def _workshop_slots(self, arguments: dict[str, Any]) -> ToolResult:
        filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
        filters.pop("schedulePreferenceMode", None)
        workflow_mode = filters.pop("workflowMode", "booking")
        time_of_day = filters.pop("timeOfDay", None)
        service_name = filters.pop("serviceTypeName", None)
        service_label = service_name
        town = filters.pop("dealershipTown", None)
        requested_town: str | None = None
        if town and not filters.get("dealershipId"):
            dealerships = await self.dealership.list_dealerships()
            matched = dealership_in_town(dealerships.get("items", []), town)
            if not matched:
                locations = dealerships.get("items", [])
                return ToolResult(
                    f"Northstar does not currently have a workshop in {town.title()}.",
                    "workshop_location_list",
                    {
                        "version": 1,
                        "items": locations,
                        "suggestions": (
                            workshop_booking_location_suggestions(
                                locations, str(filters["serviceTypeId"])
                            )
                            if filters.get("serviceTypeId")
                            else unknown_dealership_suggestions()
                        ),
                    },
                    {
                        "resolution": {
                            "kind": "dealership_location",
                            "status": "unavailable",
                            "requestedTown": town,
                        },
                        "items": locations,
                    },
                    alternative_offer=alternative_offer(
                        reason_code="requested_workshop_location_unavailable",
                        requested_outcome=f"A Northstar workshop in {town.title()}",
                        failure_reason=f"Northstar does not currently have a workshop in {town.title()}.",
                        offered_outcome=(
                            f"Choose from the current workshop locations: "
                            f"{_joined(_dealership_locations(locations))}."
                        ),
                        changes=[
                            {
                                "dimension": "Workshop location",
                                "requested": town.title(),
                                "offered": _joined(_dealership_locations(locations)),
                            }
                        ],
                        preserved=[service_label or "Workshop enquiry"],
                        candidate_references=[
                            f"dealership:{item['id']}"
                            for item in locations
                            if isinstance(item, dict) and item.get("id")
                        ],
                    ),
                )
            filters["dealershipId"] = matched["id"]
            requested_town = str(matched.get("town") or town)
        if not service_name and not filters.get("serviceTypeId"):
            service_types = await self.dealership.list_service_types()
            dealership_id = filters.get("dealershipId")
            items = list(service_types.get("items", []))
            if dealership_id:
                availability = await self.dealership.list_workshop_slots(
                    {
                        "dealershipId": dealership_id,
                        "dateFrom": datetime.now(UTC).date().isoformat(),
                    }
                )
                available_service_ids = {
                    str(slot.get("serviceTypeId"))
                    for slot in availability.get("items", [])
                    if slot.get("serviceTypeId")
                }
                items = [
                    service for service in items if str(service.get("id")) in available_service_ids
                ]
            return _service_selection_result(
                items,
                (
                    (
                        "Please choose a workshop service available at this dealership."
                        if items
                        else (
                            "No workshop services currently have available appointments "
                            "at this dealership."
                        )
                    )
                    if dealership_id
                    else (
                        "Which workshop service would you like to book? You can also tell me "
                        "what your car needs if you’re unsure."
                    )
                ),
                dealership_id=dealership_id,
            )
        if service_name and not filters.get("serviceTypeId"):
            resolution = await self._resolve_service(service_name)
            if isinstance(resolution, ToolResult):
                return resolution
            filters["serviceTypeId"] = resolution["id"]
            service_label = str(resolution.get("name") or service_name)

        # Booking order is service -> dealership -> appointment. A slot already belongs to a
        # dealership, so searching across all locations first would create contradictory state.
        if workflow_mode == "booking" and not filters.get("dealershipId"):
            locations = list((await self.dealership.list_workshop_locations()).get("items", []))
            service_type_id = str(filters["serviceTypeId"])
            return ToolResult(
                "Choose a workshop location before looking for appointment times.",
                "workshop_location_list",
                {
                    "version": 1,
                    "items": locations,
                    "suggestions": workshop_booking_location_suggestions(
                        locations, service_type_id
                    ),
                },
                {
                    "locationSelectionRequired": True,
                    "serviceTypeId": service_type_id,
                    "items": locations,
                },
            )

        schedule_requested = (
            any(arguments.get(field) is not None for field in ("dateFrom", "dateTo", "timeOfDay"))
            or arguments.get("schedulePreferenceMode") == "clear"
        )
        if workflow_mode == "booking" and not schedule_requested:
            return ToolResult(
                "The selected service and workshop are ready for appointment scheduling.",
                None,
                None,
                {
                    "serviceTypeId": str(filters["serviceTypeId"]),
                    "dealershipId": str(filters["dealershipId"]),
                    "resolution": workshop_slot_resolution(arguments, count=0),
                },
            )

        filters.setdefault("dateFrom", datetime.now(UTC).date().isoformat())
        data = await self.dealership.list_workshop_slots(filters)
        now = datetime.now(UTC)
        data["items"] = _future_slots(
            data.get("items", []),
            now,
            time_of_day=time_of_day,
            date_from=filters.get("dateFrom"),
            date_to=filters.get("dateTo"),
        )
        matching_items = data["items"]
        schedule_alternatives: list[dict[str, Any]] = []
        filtered = any(
            arguments.get(field) is not None for field in ("dateFrom", "dateTo", "timeOfDay")
        )
        fallback_queries = appointment_fallback_queries(
            filters, earliest_date=now.date().isoformat()
        )
        if not matching_items and filtered:
            broad_local_data = await self.dealership.list_workshop_slots(
                fallback_queries["same_target_next"]
            )
            schedule_alternatives = _future_slots(list(broad_local_data.get("items", [])), now)
        alternative_slots: list[dict[str, Any]] = []
        if not matching_items and not schedule_alternatives:
            alternative_data = await self.dealership.list_workshop_slots(
                fallback_queries["other_location_requested_schedule"]
            )
            alternative_slots = _future_slots(
                alternative_data.get("items", []),
                now,
                time_of_day=time_of_day,
                date_from=filters.get("dateFrom"),
                date_to=filters.get("dateTo"),
            )
        location_schedule_alternatives: list[dict[str, Any]] = []
        if (
            not matching_items
            and not schedule_alternatives
            and not alternative_slots
            and filtered
        ):
            relaxed_location_data = await self.dealership.list_workshop_slots(
                fallback_queries["other_location_next"]
            )
            location_schedule_alternatives = _future_slots(
                list(relaxed_location_data.get("items", [])), now
            )[:4]
        items = appointment_choices(
            matching_items
            or schedule_alternatives
            or alternative_slots
            or location_schedule_alternatives
        )
        data["items"] = items
        payload = {"version": 1, "items": items, "mode": workflow_mode}
        if schedule_alternatives:
            payload["requestedPreferenceHadNoMatch"] = True
            payload["emptyMessage"] = (
                "No workshop appointments matched that preference at the selected location. "
                "Here are the next available times there."
            )
            if time_of_day:
                payload["requestedTimeOfDay"] = time_of_day
                data["requestedTimeOfDay"] = time_of_day
            data["resolution"] = workshop_slot_resolution(
                arguments,
                count=0,
                schedule_alternative_count=len(schedule_alternatives),
            )
            return _slot_result(
                "slot_list",
                items,
                payload,
                data,
                alternative=alternative_offer(
                    reason_code="requested_schedule_no_match",
                    requested_outcome=(f"A workshop appointment at {_schedule_label(arguments)}"),
                    failure_reason=(
                        "No workshop appointments matched the requested schedule at the selected location."
                    ),
                    offered_outcome=(
                        "Here are the next available times at the selected location."
                    ),
                    changes=[
                        {
                            "dimension": "Appointment schedule",
                            "requested": _schedule_label(arguments),
                            "offered": "the next available times shown",
                        }
                    ],
                    preserved=["Workshop service", "Selected location"],
                    candidate_references=_appointment_references(items),
                ),
            )
        if alternative_slots:
            requested_location = requested_town
            if not requested_location and filters.get("dealershipId"):
                directory = list((await self.dealership.list_dealerships()).get("items", []))
                selected_location = next(
                    (
                        item
                        for item in directory
                        if str(item.get("id") or "") == str(filters["dealershipId"])
                    ),
                    None,
                )
                if selected_location:
                    requested_location = str(
                        selected_location.get("town")
                        or selected_location.get("name")
                        or ""
                    ).strip()
            requested_location = requested_location or "the selected workshop"
            locations = _slot_locations(items)
            payload["requestedDealershipHadNoAvailability"] = True
            payload["emptyMessage"] = (
                "The selected workshop has no appointment matching that preference. "
                "Here are matching appointments at other Northstar workshops."
            )
            if time_of_day:
                payload["requestedTimeOfDay"] = time_of_day
                data["requestedTimeOfDay"] = time_of_day
            data["resolution"] = workshop_slot_resolution(
                arguments,
                count=0,
                alternative_count=len(alternative_slots),
            )
            return _slot_result(
                "slot_list",
                items,
                payload,
                data,
                alternative=alternative_offer(
                    reason_code="requested_workshop_location_no_availability",
                    requested_outcome=(
                        f"{service_label or 'Workshop service'} at {requested_location}, "
                        f"{_schedule_label(arguments)}"
                    ),
                    failure_reason=(
                        f"{requested_location} has no appointment matching the requested schedule."
                    ),
                    offered_outcome=(
                        f"Here are appointments at {_joined(locations)} matching that schedule."
                    ),
                    changes=[
                        {
                            "dimension": "Workshop location",
                            "requested": requested_location,
                            "offered": _joined(locations),
                        }
                    ],
                    preserved=[
                        service_label or "Selected workshop service",
                        _schedule_label(arguments),
                    ],
                    candidate_references=_appointment_references(items),
                ),
            )
        if location_schedule_alternatives:
            requested_location = requested_town
            if not requested_location and filters.get("dealershipId"):
                directory = list((await self.dealership.list_dealerships()).get("items", []))
                selected_location = next(
                    (
                        item
                        for item in directory
                        if str(item.get("id") or "") == str(filters["dealershipId"])
                    ),
                    None,
                )
                if selected_location:
                    requested_location = str(
                        selected_location.get("town")
                        or selected_location.get("name")
                        or ""
                    ).strip()
            requested_location = requested_location or "the selected workshop"
            locations = _slot_locations(items)
            payload["requestedPreferenceHadNoMatch"] = True
            payload["requestedDealershipHadNoAvailability"] = True
            payload["emptyMessage"] = (
                "No appointments matched the requested schedule at the selected workshop, and "
                "that workshop has no later appointments for this service. Here are the next "
                "available times at other Northstar workshops."
            )
            data["resolution"] = workshop_slot_resolution(
                arguments,
                count=0,
                location_schedule_alternative_count=len(location_schedule_alternatives),
                schedule_search_exhausted=True,
            )
            return _slot_result(
                "slot_list",
                items,
                payload,
                data,
                alternative=alternative_offer(
                    reason_code="requested_workshop_and_schedule_no_match",
                    requested_outcome=(
                        f"{service_label or 'Workshop service'} at {requested_location}, "
                        f"{_schedule_label(arguments)}"
                    ),
                    failure_reason=(
                        "The selected workshop has no appointments for this service, including "
                        "the requested schedule."
                    ),
                    offered_outcome=(
                        f"Here are the next available times at {_joined(locations)}."
                    ),
                    changes=[
                        {
                            "dimension": "Workshop location",
                            "requested": requested_location,
                            "offered": _joined(locations),
                        },
                        {
                            "dimension": "Appointment schedule",
                            "requested": _schedule_label(arguments),
                            "offered": "the next available times shown",
                        },
                    ],
                    preserved=[service_label or "Selected workshop service"],
                    candidate_references=_appointment_references(items),
                ),
            )
        if not items:
            payload["suggestions"] = workshop_no_availability_suggestions(
                [], service_label, filters.get("serviceTypeId")
            )
            service_phrase = f" for {service_label}" if service_label else ""
            location_phrase = f" in {requested_town}" if requested_town else ""
            period_phrase = f" in the {time_of_day}" if time_of_day else ""
            empty_message = (
                f"No appointments{service_phrase} are currently available{location_phrase}"
                f"{period_phrase}."
            )
            if time_of_day:
                payload["requestedTimeOfDay"] = time_of_day
                data["requestedTimeOfDay"] = time_of_day
            payload["emptyMessage"] = empty_message
            data["resolution"] = workshop_slot_resolution(
                arguments,
                count=0,
                alternative_count=0,
                schedule_search_exhausted=filtered,
            )
            return ToolResult(
                empty_message,
                "slot_list",
                payload,
                data,
            )
        data["resolution"] = workshop_slot_resolution(arguments, count=len(items))
        return _slot_result("slot_list", items, payload, data)

    async def _resolve_service(self, service_name: str) -> dict | ToolResult:
        service_types = await self.dealership.list_service_types()
        candidates = service_types.get("items", [])
        resolution = resolve_live_service(candidates, service_name)
        if resolution.status == "matched" and resolution.service is not None:
            return resolution.service
        return _service_resolution_result(resolution.status, resolution.candidates)


def _future_slots(
    items: list[dict[str, Any]],
    now: datetime,
    *,
    time_of_day: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("startsAt")
        and datetime.fromisoformat(str(item["startsAt"])) > now
        and (time_of_day is None or _slot_matches_time_of_day(item, time_of_day))
        and _slot_matches_date(item, date_from=date_from, date_to=date_to)
    ]


def _equivalent_vehicle_appointments(
    slots: list[dict[str, Any]],
    now: datetime,
    vehicles_by_id: dict[str, dict[str, Any]],
    *,
    time_of_day: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Project live slots for equivalent stock without losing their display identity."""

    appointments: list[dict[str, Any]] = []
    for slot in _future_slots(
        slots,
        now,
        time_of_day=time_of_day,
        date_from=date_from,
        date_to=date_to,
    ):
        vehicle = vehicles_by_id.get(str(slot.get("vehicleId")))
        if vehicle is None:
            continue
        appointments.append(
            {
                **slot,
                "vehicleYear": vehicle.get("year"),
                "make": vehicle.get("make"),
                "model": vehicle.get("model"),
                "variant": vehicle.get("variant"),
                "alternativeVehicle": True,
            }
        )
        if len(appointments) == 4:
            break
    return appointments


def _slot_matches_date(
    item: dict[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if not date_from and not date_to:
        return True
    starts_at = item.get("startsAt")
    if not starts_at:
        return False
    try:
        instant = datetime.fromisoformat(str(starts_at))
        lower = date.fromisoformat(date_from) if date_from else None
        upper = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        return False
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local_date = instant.astimezone(ZoneInfo("Europe/London")).date()
    return (lower is None or local_date >= lower) and (upper is None or local_date <= upper)


def _slot_matches_time_of_day(item: dict[str, Any], time_of_day: str) -> bool:
    starts_at = item.get("startsAt")
    if not starts_at:
        return False
    instant = datetime.fromisoformat(str(starts_at))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    hour = instant.astimezone(ZoneInfo("Europe/London")).hour
    return hour < 12 if time_of_day == "morning" else hour >= 12


def _service_selection_result(
    items: list[dict], text: str, dealership_id: str | None = None
) -> ToolResult:
    payload = {
        "version": 1,
        "items": items,
        "suggestions": service_type_suggestions(items, dealership_id),
        # Preserve trusted choices for typed-reference resolution without turning the booking
        # question into a catalogue of cards and chips.
        "selectionOnly": True,
        "choiceEntityType": "service",
        "choiceField": "serviceTypeId",
        "collectionViewType": "choice_list",
    }
    if presentation := _service_collection_presentation(items, "choice"):
        payload["collectionPresentation"] = presentation
    if dealership_id:
        payload["dealershipId"] = dealership_id
    return ToolResult(
        text,
        "service_list",
        payload,
        {
            "selectionOnly": True,
            "choiceEntityType": "service",
            "choiceField": "serviceTypeId",
            "items": items,
            **({"dealershipId": dealership_id} if dealership_id else {}),
        },
    )


def _service_resolution_result(status: str, candidates: tuple[dict[str, Any], ...]) -> ToolResult:
    if status == "ambiguous":
        items = list(candidates)
        payload = {
            "version": 1,
            "items": items,
            "suggestions": service_type_suggestions(items),
            "resolution": {"status": "ambiguous"},
            "selectionOnly": True,
            "choiceEntityType": "service",
            "choiceField": "serviceTypeId",
            "collectionViewType": "choice_list",
        }
        if presentation := _service_collection_presentation(items, "clarification"):
            payload["collectionPresentation"] = presentation
        return ToolResult(
            "I found more than one possible workshop service. Please choose the one you mean.",
            "service_list",
            payload,
            {
                "resolution": {"status": "ambiguous"},
                "selectionOnly": True,
                "choiceEntityType": "service",
                "choiceField": "serviceTypeId",
                "items": items,
            },
        )
    suggestions = [
        {
            "label": "View supported services",
            "text": "What workshop services do you support?",
            "action": {"type": "show_workshop_services"},
        }
    ]
    return ToolResult(
        "That service is not currently listed as a supported Northstar workshop service.",
        "suggestion_list",
        {
            "version": 1,
            "suggestions": suggestions,
            "resolution": {"status": "unavailable"},
        },
        {"resolution": {"status": "unavailable"}, "items": []},
    )


def _service_collection_presentation(
    items: list[dict[str, Any]],
    purpose: str,
) -> dict[str, Any] | None:
    display_items = []
    for item in items[:20]:
        label = str(item.get("name") or "").strip()
        if not label:
            continue
        description = str(item.get("description") or "").strip() or None
        display_items.append({"label": label, "description": description})
    if not display_items:
        return None
    return CollectionPresentation.model_validate(
        {
            "schemaVersion": 1,
            "layout": "bullet_list",
            "purpose": purpose,
            "items": display_items,
        }
    ).model_dump(mode="json", exclude_none=True)


def _slot_result(
    view_type: str,
    items: list[dict],
    payload: dict[str, Any],
    facts: dict[str, Any],
    *,
    alternative: dict[str, Any] | None = None,
) -> ToolResult:
    trusted_payload = appointment_collection_payload({**payload, "items": items})
    return ToolResult(
        f"Found {len(items)} result{'s' if len(items) != 1 else ''}.",
        view_type,
        trusted_payload,
        facts,
        alternative_offer=alternative,
    )


def _schedule_label(arguments: dict[str, Any]) -> str:
    date_from = str(arguments.get("dateFrom") or "").strip()
    date_to = str(arguments.get("dateTo") or "").strip()
    period = str(arguments.get("timeOfDay") or "").strip()
    if date_from:
        try:
            start = date.fromisoformat(date_from)
            date_label = f"{start.day} {start.strftime('%b %Y')}"
        except ValueError:
            date_label = date_from
        if date_to and date_to != date_from:
            try:
                end = date.fromisoformat(date_to)
                date_label = f"{date_label} to {end.day} {end.strftime('%b %Y')}"
            except ValueError:
                date_label = f"{date_label} to {date_to}"
    else:
        date_label = "the requested date"
    return f"{date_label}, {period}" if period else date_label


def _vehicle_label(vehicle: dict[str, Any]) -> str:
    return (
        " ".join(
            str(vehicle.get(field) or "").strip()
            for field in ("year", "make", "model", "variant")
            if str(vehicle.get(field) or "").strip()
        )
        or "the exact selected vehicle"
    )


def _appointment_references(items: list[dict[str, Any]]) -> list[str]:
    return [
        f"appointment:{item['id']}" for item in items if isinstance(item, dict) and item.get("id")
    ]


def _slot_locations(items: list[dict[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("dealershipTown") or item.get("dealershipName") or "").strip()
            for item in items
            if isinstance(item, dict)
            and str(item.get("dealershipTown") or item.get("dealershipName") or "").strip()
        )
    )


def _dealership_locations(items: list[dict[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("town") or item.get("name") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("town") or item.get("name") or "").strip()
        )
    )


def _joined(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return f"{', '.join(values[:-1])} and {values[-1]}"


def _location_preserved(arguments: dict[str, Any]) -> list[str]:
    return ["Requested dealership"] if arguments.get("dealershipId") else []
