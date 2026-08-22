from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import uuid4

from webchat.integrations.contracts import ToolCall, TurnPlan


@dataclass(frozen=True)
class PlannedTransition:
    """One validated application transition produced from a semantic turn plan."""

    state: dict[str, Any]
    tool_call: ToolCall | None = None
    response: str = ""
    suggestion_dimension: str | None = None


class TransitionController:
    """Owns workflow branching; the language model only supplies semantic intent."""

    _SEARCH_FIELDS: ClassVar[set[str]] = {
        "page",
        "query",
        "make",
        "model",
        "fuelType",
        "transmission",
        "bodyStyle",
        "availability",
        "dealershipId",
        "town",
        "minPricePence",
        "maxPricePence",
        "maxMileage",
        "minYear",
        "sort",
    }
    _PREFERENCE_FIELDS: ClassVar[dict[str, str]] = {
        "budgets": "maxPricePence",
        "mileages": "maxMileage",
        "makes": "make",
        "models": "model",
        "fuelTypes": "fuelType",
        "transmissions": "transmission",
        "bodyStyles": "bodyStyle",
    }
    _CONTACT_FIELDS: ClassVar[set[str]] = {"firstName", "lastName", "email", "phone"}
    _GENERIC_VEHICLE_QUERY_WORDS: ClassVar[set[str]] = {
        "a",
        "all",
        "an",
        "and",
        "any",
        "around",
        "at",
        "available",
        "below",
        "budget",
        "budgets",
        "car",
        "cars",
        "cost",
        "costing",
        "find",
        "for",
        "from",
        "give",
        "have",
        "i",
        "in",
        "less",
        "like",
        "looking",
        "max",
        "maximum",
        "me",
        "mile",
        "mileage",
        "miles",
        "min",
        "minimum",
        "my",
        "near",
        "need",
        "only",
        "or",
        "over",
        "please",
        "prefer",
        "price",
        "priced",
        "show",
        "some",
        "stock",
        "than",
        "that",
        "the",
        "these",
        "those",
        "to",
        "under",
        "up",
        "vehicle",
        "vehicles",
        "want",
        "with",
        "would",
    }

    def resolve(
        self,
        plan: TurnPlan,
        current_state: dict[str, Any] | None,
        *,
        user_text: str,
        displayed_vehicles: list[dict[str, object]],
        vehicle_search_state: dict[str, object] | None,
        displayed_offers: list[dict[str, object]] | None = None,
        page_vehicles: list[dict[str, object]] | None = None,
    ) -> PlannedTransition:
        state = current_state or {}
        arguments = dict(plan.arguments)
        intent = plan.intent
        if intent in {"vehicle_search", "vehicle_preference_selection"}:
            self._ground_vehicle_context_flags(arguments, user_text)
        active_offer = self._active_offer(arguments, displayed_offers or [])
        if (
            intent == "vehicle_interest"
            and not arguments.get("vehicleId")
            and (active_offer is not None or state.get("domain") in {"offer", "offers"})
        ):
            # Reserved-vehicle interest is not a valid action for an offer.
            # Recover a misclassified purchase turn at the domain boundary.
            intent = "sales_enquiry"
        if intent == "sales_enquiry" and (
            active_offer is not None or state.get("domain") in {"offer", "offers"}
        ):
            arguments.setdefault("enquiryType", "finance")
            arguments.setdefault(
                "message",
                self.offer_enquiry_message(active_offer) if active_offer else user_text,
            )
            if active_offer and active_offer.get("offerId"):
                arguments.setdefault("offerId", active_offer["offerId"])
        if intent == "vehicle_preference_selection":
            selection = self._preference_filter(arguments)
            if selection is None:
                dimension = str(arguments.get("preferenceDimension") or "startingPoint")
                next_state = self._next_state("vehicle_preferences", arguments, state)
                next_state["stage"] = "choosing_preference"
                return PlannedTransition(
                    next_state,
                    response="Choose one of the available options below.",
                    suggestion_dimension=dimension,
                )
            field, value = selection
            arguments[field] = value
            if self._state_owns_preference_filter(state, field):
                arguments["refineCurrentSearch"] = True
        next_state = self._next_state(intent, arguments, state)

        if arguments.get("referenceScope") == "currentPage" and not page_vehicles:
            next_state["stage"] = "clarifying"
            return PlannedTransition(
                next_state,
                response=(
                    "I can't identify any vehicle results on the current page. "
                    "Open the relevant results and ask me again."
                ),
            )

        if intent in {"general_response", "clarification"}:
            response = plan.response.strip()
            if not response:
                response = "Could you tell me a little more about what you need?"
            next_state["stage"] = "answered" if intent == "general_response" else "clarifying"
            return PlannedTransition(next_state, response=response)

        if intent == "vehicle_preferences":
            dimension = str(arguments.get("preferenceDimension") or "startingPoint")
            response = plan.response.strip() or (
                "Tell me what matters most, or choose a starting point below."
                if dimension == "startingPoint"
                else "Choose an option below."
            )
            next_state["stage"] = "choosing_preference"
            return PlannedTransition(
                next_state,
                response=response,
                suggestion_dimension=dimension,
            )

        if intent == "vehicle_more" and not vehicle_search_state:
            next_state["stage"] = "clarifying"
            return PlannedTransition(
                next_state,
                response="There isn't an active vehicle result set to continue. Tell me what kind of car you want to find.",
                suggestion_dimension="startingPoint",
            )

        if intent == "vehicle_compare":
            vehicle_ids = list(arguments.get("vehicleIds") or [])
            queries = list(arguments.get("vehicleQueries") or [])
            displayed_ids = {str(item.get("vehicleId")) for item in displayed_vehicles}
            invalid_reference = bool(
                vehicle_ids and displayed_ids and not set(vehicle_ids).issubset(displayed_ids)
            )
            if invalid_reference or (len(vehicle_ids) < 2 and len(queries) < 2):
                next_state["stage"] = "clarifying"
                return PlannedTransition(
                    next_state,
                    response=(
                        "Please name at least two vehicles to compare, or show some vehicles first."
                    ),
                )

        if intent == "vehicle_interest":
            vehicle_id = arguments.get("vehicleId")
            if not vehicle_id and arguments.get("reuseActiveEntity"):
                vehicle_id = self._state_entity(state, "vehicleId")
            if not vehicle_id:
                next_state["stage"] = "clarifying"
                return PlannedTransition(
                    next_state,
                    response="Which reserved vehicle would you like to register interest in?",
                )
            arguments["vehicleId"] = vehicle_id
            next_state = self._next_state(intent, arguments, state)

        tool_name, tool_arguments = self._tool_transition(
            intent,
            arguments,
            state,
            user_text=user_text,
            displayed_vehicles=displayed_vehicles,
            vehicle_search_state=vehicle_search_state,
            page_vehicles=page_vehicles or [],
        )
        if intent == "vehicle_more":
            next_state["constraints"] = {
                key: value for key, value in tool_arguments.items() if key != "page"
            }
        return PlannedTransition(
            next_state,
            tool_call=ToolCall(f"plan-{uuid4()}", tool_name, tool_arguments),
        )

    def advance(
        self,
        state: dict[str, Any],
        tool_name: str,
        view_type: str | None,
    ) -> dict[str, Any]:
        advanced = dict(state)
        stage_by_view = {
            "vehicle_list": "viewing_results",
            "vehicle_comparison": "viewing_comparison",
            "offer_list": "viewing_offers",
            "dealership_list": "viewing_dealerships",
            "opening_hours": "viewing_hours",
            "workshop_location_list": "viewing_workshops",
            "service_list": "choosing_service",
            "slot_list": "choosing_time",
            "test_drive_slot_picker": "choosing_time",
            "part_exchange_estimate_form": "collecting_vehicle_details",
            "part_exchange_estimate": "estimate_shown",
            "private_booking_lookup": "verifying_booking",
            "draft": "collecting_details",
            "confirmation": "awaiting_confirmation",
        }
        advanced["stage"] = stage_by_view.get(view_type, "answered")
        advanced["lastTool"] = tool_name
        return advanced

    def advance_action(
        self,
        current_state: dict[str, Any] | None,
        action: dict[str, Any],
        view_type: str | None,
    ) -> dict[str, Any]:
        """Keep trusted widget actions in the same canonical workflow state."""
        action_type = str(action.get("type") or "")
        state = dict(current_state or {})
        if action_type in {
            "select_workshop_service",
            "try_workshop_location",
            "show_workshop_services",
        }:
            # Action state mirrors only the identifiers sent to the tool. It
            # never inherits an old service, location, or date implicitly.
            entities = (
                {"serviceTypeId": action["serviceTypeId"]}
                if action.get("serviceTypeId")
                else {}
            )
            constraints = (
                {"dealershipId": action["dealershipId"]}
                if action.get("dealershipId")
                else {}
            )
            state = {
                "version": 1,
                "domain": "workshop",
                "intent": "workshop_booking",
                "stage": "planned",
                "entities": entities,
                "constraints": constraints,
            }
            tool_name = (
                "list_service_types"
                if action_type == "show_workshop_services"
                else "list_workshop_slots"
            )
            return self.advance(state, tool_name, view_type)
        if action_type in {"next_vehicle_page", "compare_displayed_vehicles"}:
            state = {
                "version": 1,
                "domain": "vehicle",
                "intent": (
                    "vehicle_more"
                    if action_type == "next_vehicle_page"
                    else "vehicle_compare"
                ),
                "stage": "planned",
                "entities": {},
                "constraints": (
                    dict(state.get("constraints") or {})
                    if action_type == "next_vehicle_page"
                    and state.get("domain") == "vehicle"
                    else {}
                ),
            }
            tool_name = (
                "search_vehicles"
                if action_type == "next_vehicle_page"
                else "compare_vehicles"
            )
            return self.advance(state, tool_name, view_type)
        if action_type == "select_test_drive_vehicle":
            state = {
                "version": 1,
                "domain": "vehicle",
                "intent": "test_drive",
                "stage": "planned",
                "entities": {"vehicleId": action.get("vehicleId")},
                "constraints": {},
            }
            return self.advance(state, "list_test_drive_slots", view_type)
        if action_type in {"start_vehicle_interest", "start_sales_enquiry"}:
            intent = (
                "vehicle_interest"
                if action_type == "start_vehicle_interest"
                else "sales_enquiry"
            )
            state = {
                "version": 1,
                "domain": "vehicle",
                "intent": intent,
                "stage": "planned",
                "entities": {"vehicleId": action.get("vehicleId")},
                "constraints": {},
            }
            tool_name = (
                "prepare_vehicle_interest"
                if action_type == "start_vehicle_interest"
                else "prepare_sales_enquiry"
            )
            return self.advance(state, tool_name, view_type)
        if action_type == "start_offer_enquiry":
            state = {
                "version": 1,
                "domain": "vehicle",
                "intent": "sales_enquiry",
                "stage": "planned",
                "entities": {"offerId": action.get("offerId")},
                "constraints": {},
            }
            return self.advance(state, "prepare_sales_enquiry", view_type)
        if action_type == "apply_vehicle_preference":
            constraints = self.preference_action_filters(state, action)
            state = {
                "version": 1,
                "domain": "vehicle",
                "intent": "vehicle_search",
                "stage": "planned",
                "entities": {},
                "constraints": constraints,
            }
            return self.advance(state, "search_vehicles", view_type)
        return state

    def _tool_transition(
        self,
        intent: str,
        arguments: dict[str, Any],
        state: dict[str, Any],
        *,
        user_text: str,
        displayed_vehicles: list[dict[str, object]],
        vehicle_search_state: dict[str, object] | None,
        page_vehicles: list[dict[str, object]],
    ) -> tuple[str, dict[str, Any]]:
        if intent in {"vehicle_search", "vehicle_preference_selection"}:
            filters = self._merged_vehicle_filters(arguments, state)
            if arguments.get("referenceScope") == "currentPage":
                filters["vehicleIds"] = [
                    str(item["vehicleId"])
                    for item in page_vehicles
                    if item.get("vehicleId")
                ]
                filters["limit"] = int(arguments.get("resultLimit") or 3)
                return "select_page_vehicles", filters
            return "search_vehicles", filters

        if intent == "vehicle_more":
            if vehicle_search_state:
                filters = dict(vehicle_search_state.get("filters") or {})
                filters["page"] = int(vehicle_search_state.get("page") or 1) + 1
                return "search_vehicles", filters
            raise ValueError("vehicle_more requires an active search")

        if intent == "vehicle_compare":
            vehicle_ids = list(arguments.get("vehicleIds") or [])
            if len(vehicle_ids) >= 2:
                return "compare_vehicles", {"vehicleIds": vehicle_ids[:3]}
            queries = list(arguments.get("vehicleQueries") or [])
            if len(queries) >= 2:
                return "compare_vehicle_models", {"queries": queries[:3]}
            raise ValueError("vehicle comparison requires resolved vehicles or model queries")

        if intent in {"vehicle_details", "vehicle_availability"}:
            query = arguments.get("query")
            vehicle_id = arguments.get("vehicleId")
            if not vehicle_id and not query and arguments.get("reuseActiveEntity"):
                vehicle_id = self._state_entity(state, "vehicleId")
            if vehicle_id:
                tool = "get_vehicle" if intent == "vehicle_details" else "get_vehicle_availability"
                return tool, {"id": vehicle_id}
            return "search_vehicles", {"q": query or user_text}

        if intent == "test_drive":
            query = arguments.get("query")
            vehicle_id = arguments.get("vehicleId")
            if not vehicle_id and not query and arguments.get("reuseActiveEntity"):
                vehicle_id = self._state_entity(state, "vehicleId")
            if vehicle_id:
                return "list_test_drive_slots", {"vehicleId": vehicle_id}
            return "search_vehicles", {"q": query or user_text}

        if intent == "offers_list":
            return "list_offers", self._only(arguments, {"make", "productType"})
        if intent == "offer_details":
            offer_id = arguments.get("offerId")
            if not offer_id:
                return "list_offers", self._only(arguments, {"make", "productType"})
            return "get_offer", {"id": offer_id}

        if intent in {"dealership_locations", "dealership_contact"}:
            return "list_dealerships", self._rename(
                self._only(arguments, {"town"}), {"town": "town"}
            )
        if intent == "dealership_departments":
            return "list_dealership_departments", {}
        if intent == "opening_hours":
            return "list_opening_hours", self._only(arguments, {"town", "day", "department"})

        if intent == "workshop_locations":
            return "list_workshop_locations", {}
        if intent == "workshop_services":
            return "list_service_types", {}
        if intent == "workshop_service_information":
            explicit_service_query = arguments.get("serviceQuery") or arguments.get("query")
            service_type_id = arguments.get("serviceTypeId")
            if (
                arguments.get("reuseActiveEntity")
                and not explicit_service_query
                and not service_type_id
            ):
                service_type_id = self._state_entity(state, "serviceTypeId")
            if service_type_id:
                return "get_service_information", {"serviceTypeId": service_type_id}
            service_query = self._service_query(arguments, state) or user_text
            return "get_service_information", {"q": service_query[:200]}
        if intent == "workshop_booking":
            explicit_service_query = arguments.get("serviceQuery") or arguments.get("query")
            service_type_id = arguments.get("serviceTypeId")
            if (
                arguments.get("reuseActiveEntity")
                and not explicit_service_query
                and not service_type_id
            ):
                service_type_id = self._state_entity(state, "serviceTypeId")
            service_query = self._service_query(
                arguments,
                state if arguments.get("reuseActiveEntity") else {},
            )
            if not service_type_id and not service_query:
                return "list_service_types", {}
            filters = self._only(
                arguments, {"dealershipId", "dateFrom", "dateTo"}
            )
            if arguments.get("town"):
                filters["dealershipTown"] = arguments["town"]
            if service_type_id:
                filters["serviceTypeId"] = service_type_id
            else:
                filters["serviceTypeName"] = str(service_query)[:80]
            return "list_workshop_slots", filters
        if intent == "workshop_booking_lookup":
            return "request_workshop_booking_lookup_form", {"mode": "lookup"}
        if intent == "workshop_booking_change":
            if state.get("domain") == "workshop" and state.get("stage") == "verified":
                changes = self._only(arguments, {"slotId", "mileage", "notes"})
                if changes:
                    return "prepare_workshop_amendment", changes
                return "prepare_workshop_amendment", {}
            return "request_workshop_booking_lookup_form", {"mode": "amend"}
        if intent == "workshop_booking_cancel":
            if state.get("domain") == "workshop" and state.get("stage") == "verified":
                return "prepare_workshop_cancellation", {}
            return "request_workshop_booking_lookup_form", {"mode": "cancel"}

        if intent == "part_exchange_estimate":
            estimate = self._only(arguments, {"registration", "mileage", "condition"})
            if set(estimate) == {"registration", "mileage", "condition"}:
                return "estimate_part_exchange", estimate
            return "request_part_exchange_estimate_form", estimate
        if intent == "part_exchange_follow_up":
            return "prepare_part_exchange", self._only(
                arguments,
                self._CONTACT_FIELDS
                | {"dealershipId", "registration", "mileage", "condition", "vehicleId"},
            )

        if intent == "callback":
            return "prepare_callback", self._only(
                arguments,
                self._CONTACT_FIELDS
                | {"dealershipId", "department", "reason", "vehicleId", "preferredTime"},
            )
        if intent == "sales_enquiry":
            fields = self._only(
                arguments,
                self._CONTACT_FIELDS
                | {"dealershipId", "vehicleId", "message", "enquiryType"},
            )
            fields.setdefault("enquiryType", "general")
            return "prepare_sales_enquiry", fields
        if intent == "vehicle_interest":
            return "prepare_vehicle_interest", self._only(
                arguments, self._CONTACT_FIELDS | {"vehicleId"}
            )
        if intent == "dealership_message":
            return "prepare_dealership_message", self._only(
                arguments,
                self._CONTACT_FIELDS
                | {
                    "dealershipId",
                    "department",
                    "subject",
                    "message",
                    "preferredContactMethod",
                },
            )
        if intent == "business_information":
            return "get_business_information", {}

        raise ValueError(f"unsupported turn-plan intent: {intent}")

    def _next_state(
        self,
        intent: str,
        arguments: dict[str, Any],
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
        if intent in {"general_response", "clarification", "business_information"}:
            preserved = dict(current_state)
            preserved.update(version=1, intent=intent, stage="planned")
            preserved.setdefault("domain", "general")
            preserved.setdefault("entities", {})
            preserved.setdefault("constraints", {})
            return preserved

        domain = intent.split("_", 1)[0]
        if intent in {"test_drive", "sales_enquiry", "vehicle_interest"}:
            domain = "vehicle"
        elif intent in {"opening_hours", "callback", "dealership_message"}:
            domain = "dealership"
        elif intent.startswith("part_exchange"):
            domain = "part_exchange"

        entities: dict[str, Any] = {}
        if arguments.get("reuseActiveEntity") and domain == current_state.get("domain"):
            entities.update(dict(current_state.get("entities") or {}))
        for field in (
            "vehicleId",
            "offerId",
            "dealershipId",
            "serviceTypeId",
            "serviceQuery",
            "preferenceDimension",
        ):
            if arguments.get(field):
                entities[field] = arguments[field]
        if domain == "workshop":
            explicit_service_query = arguments.get("serviceQuery") or arguments.get("query")
            if explicit_service_query:
                entities["serviceQuery"] = explicit_service_query
                entities.pop("serviceTypeId", None)
            elif arguments.get("serviceTypeId"):
                entities["serviceTypeId"] = arguments["serviceTypeId"]
                entities.pop("serviceQuery", None)
            elif intent in {"workshop_booking", "workshop_service_information"} and not arguments.get(
                "reuseActiveEntity"
            ):
                entities.pop("serviceTypeId", None)
                entities.pop("serviceQuery", None)

        constraints: dict[str, Any] = {}
        if domain == "vehicle":
            # One invariant owns all vehicle state: previous constraints merge
            # only when the semantic plan explicitly marks this turn as a
            # refinement. Preference screens are not an exception.
            constraints.update(self._merged_vehicle_filters(arguments, current_state))
            constraints.pop("page", None)
        elif domain == "workshop":
            constraints.update(
                self._only(arguments, {"town", "dealershipId", "dateFrom", "dateTo"})
            )

        return {
            "version": 1,
            "domain": domain,
            "intent": intent,
            "stage": "planned",
            "entities": entities,
            "constraints": constraints,
        }

    def _vehicle_filters(self, arguments: dict[str, Any]) -> dict[str, Any]:
        filters = self._only(arguments, self._SEARCH_FIELDS)
        if "query" in filters:
            filters["q"] = filters.pop("query")
        if "town" in filters:
            filters["dealershipTown"] = filters.pop("town")
        return filters

    @classmethod
    def _ground_vehicle_context_flags(
        cls, arguments: dict[str, Any], user_text: str
    ) -> None:
        """Reject model-selected hidden context that the customer did not reference.

        Page context and an active search are candidates for resolving language, not
        implicit filters. The planner may select a candidate only when the latest
        utterance contains an ordinary result-set reference or refinement marker.
        This keeps identical, self-contained searches deterministic across pages and
        conversations while retaining natural follow-ups such as "among these" or
        "only show the automatic ones".
        """
        normalized = " ".join(user_text.casefold().split())
        result_reference = bool(
            re.search(
                r"\b(?:these|those|them)\b"
                r"|\b(?:current|visible|displayed|shown|previous|same)\s+"
                r"(?:cars?|vehicles?|results?|list|ones?)\b"
                r"|\b(?:on|from|among|within)\s+(?:this|that|the\s+current)\s+"
                r"(?:page|list|search|results?)\b"
                r"|\b(?:first|second|third|last)\s+(?:one|two|three|car|vehicle)s?\b"
                r"|\b(?:cars?|vehicles?|results?)\s+(?:here|above)\b",
                normalized,
            )
        )
        if arguments.get("referenceScope") and not result_reference:
            arguments.pop("referenceScope", None)

        refinement = result_reference or bool(
            re.search(
                r"^\s*(?:and|but)\b"
                r"|\b(?:also|only|instead|actually|still|keep|retain|add|remove|"
                r"exclude|include|change|switch|refine|narrow|filter)\b"
                r"|\b(?:make|set|raise|lower|increase|decrease)\s+"
                r"(?:it|them|those|these|the|my|budget)\b"
                r"|\bwith(?:out)?\b"
                r"|\b(?:cheaper|dearer|newer|older)\s+(?:ones?|cars?|vehicles?)?\b",
                normalized,
            )
        )
        if arguments.get("refineCurrentSearch") and not refinement:
            arguments["refineCurrentSearch"] = False

    def _merged_vehicle_filters(
        self, arguments: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        """Reuse search constraints only when the semantic plan explicitly says refine."""
        filters: dict[str, Any] = {}
        if arguments.get("refineCurrentSearch") and state.get("domain") == "vehicle":
            filters.update(dict(state.get("constraints") or {}))
        aliases = {"query": "q", "town": "dealershipTown"}
        for field in arguments.get("clearVehicleFilters") or []:
            filters.pop(aliases.get(str(field), str(field)), None)
        filters.update(self._vehicle_filters(arguments))
        if "page" not in arguments:
            filters.pop("page", None)
        self._remove_redundant_vehicle_query(filters)
        return filters

    @classmethod
    def _remove_redundant_vehicle_query(cls, filters: dict[str, Any]) -> None:
        """Drop conversational search text when typed filters already express it.

        The stock API treats ``q`` as a literal make/model/variant/colour substring.
        A planner may legitimately emit both a typed constraint and the customer's
        wording, for example ``q='cars under £35,000'`` plus a numeric price cap.
        Sending that prose to the stock API makes the valid typed filter impossible
        to match. Keep ``q`` only when it contains a residual vehicle identity term.
        """
        query = str(filters.get("q") or "").strip()
        if not query:
            filters.pop("q", None)
            return

        structured_fields = {
            "make",
            "model",
            "fuelType",
            "transmission",
            "bodyStyle",
            "dealershipTown",
            "dealershipId",
            "availability",
            "minPricePence",
            "maxPricePence",
            "maxMileage",
            "minYear",
            "sort",
        }
        if not structured_fields.intersection(filters):
            return

        residual = query.casefold()
        for field in (
            "make",
            "model",
            "fuelType",
            "transmission",
            "bodyStyle",
            "dealershipTown",
            "availability",
        ):
            value = str(filters.get(field) or "").strip().casefold()
            if value:
                residual = re.sub(rf"\b{re.escape(value)}\b", " ", residual)

        if {"minPricePence", "maxPricePence"}.intersection(filters):
            residual = re.sub(
                r"\b(?:under|below|less\s+than|up\s+to|over|above|from|max(?:imum)?|budget\s+of)\s*"
                r"(?:£|gbp)?\s*\d[\d,.]*\s*(?:pounds?)?\b",
                " ",
                residual,
            )
            residual = re.sub(r"(?:£|gbp)\s*\d[\d,.]*", " ", residual)
        if "maxMileage" in filters:
            residual = re.sub(
                r"\b(?:under|below|less\s+than|up\s+to|max(?:imum)?)?\s*"
                r"\d[\d,]*\s*(?:miles?|mi)\b",
                " ",
                residual,
            )
        if "minYear" in filters:
            residual = re.sub(
                r"\b(?:from|since|newer\s+than)?\s*20\d{2}(?:\s+or\s+newer)?\b",
                " ",
                residual,
            )

        generic_words = set(cls._GENERIC_VEHICLE_QUERY_WORDS)
        sort = filters.get("sort")
        if sort == "priceAsc":
            generic_words.update({"cheap", "cheapest", "lowest"})
        elif sort == "priceDesc":
            generic_words.update({"expensive", "highest"})
        elif sort == "mileageAsc":
            generic_words.update({"low", "lowest"})
        elif sort == "newest":
            generic_words.update({"latest", "new", "newest"})

        meaningful_words = [
            word
            for word in re.findall(r"[a-z0-9]+", residual)
            if word not in generic_words
        ]
        if not meaningful_words:
            filters.pop("q", None)
        else:
            filters["q"] = " ".join(meaningful_words)

    @classmethod
    def _preference_filter(cls, arguments: dict[str, Any]) -> tuple[str, Any] | None:
        dimension = arguments.get("preferenceDimension")
        field = cls._PREFERENCE_FIELDS.get(str(dimension))
        if not field:
            return None
        value = arguments.get(field, arguments.get("preferenceValue"))
        if value in (None, ""):
            return None
        if field in {"maxPricePence", "maxMileage"}:
            if isinstance(value, bool):
                return None
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None
            if value < 0:
                return None
        else:
            value = str(value).strip()
            if not value:
                return None
        return field, value

    @classmethod
    def _state_owns_preference_filter(
        cls, state: dict[str, Any], vehicle_filter: str
    ) -> bool:
        entities = state.get("entities") or {}
        dimension = str(entities.get("preferenceDimension") or "")
        return bool(
            state.get("domain") == "vehicle"
            and state.get("intent") == "vehicle_preferences"
            and state.get("stage") == "choosing_preference"
            and cls._PREFERENCE_FIELDS.get(dimension) == vehicle_filter
        )

    @classmethod
    def preference_action_filters(
        cls,
        current_state: dict[str, Any] | None,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve a choice only against the preference screen that owns it."""
        state = current_state or {}
        vehicle_filter = str(action.get("vehicleFilter") or "")
        filters = (
            dict(state.get("constraints") or {})
            if cls._state_owns_preference_filter(state, vehicle_filter)
            else {}
        )
        if vehicle_filter in cls._PREFERENCE_FIELDS.values():
            filters[vehicle_filter] = action.get("vehicleFilterValue")
        filters.pop("page", None)
        return filters

    @staticmethod
    def _active_offer(
        arguments: dict[str, Any], displayed_offers: list[dict[str, object]]
    ) -> dict[str, object] | None:
        offer_id = arguments.get("offerId")
        if offer_id:
            return next(
                (
                    offer
                    for offer in displayed_offers
                    if offer.get("offerId") == offer_id
                ),
                None,
            )
        return displayed_offers[0] if len(displayed_offers) == 1 else None

    @staticmethod
    def offer_enquiry_message(offer: dict[str, object] | None) -> str:
        if not offer:
            return "I am interested in the currently displayed new-car offer."
        label = " ".join(
            str(offer.get(field) or "").strip()
            for field in ("make", "model", "productType")
        ).strip()
        return f"I am interested in the currently published {label} offer." if label else (
            "I am interested in the currently displayed new-car offer."
        )

    @staticmethod
    def _service_query(arguments: dict[str, Any], state: dict[str, Any]) -> str | None:
        value = arguments.get("serviceQuery") or arguments.get("query")
        if value:
            return str(value)
        entities = state.get("entities") or {}
        value = entities.get("serviceQuery")
        return str(value) if value else None

    @staticmethod
    def _state_entity(state: dict[str, Any], name: str) -> Any | None:
        return (state.get("entities") or {}).get(name)

    @staticmethod
    def _only(arguments: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
        return {
            key: value
            for key, value in arguments.items()
            if key in allowed and value is not None
        }

    @staticmethod
    def _rename(values: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
        return {names.get(key, key): value for key, value in values.items()}
