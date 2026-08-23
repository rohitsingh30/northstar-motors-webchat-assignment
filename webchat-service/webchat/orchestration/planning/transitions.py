"""Deterministic workflow-state transitions from validated turn plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import uuid4

from webchat.integrations.contracts import ToolCall, TurnPlan
from webchat.orchestration.planning.conformance import (
    vehicle_discovery_has_evidence,
    vehicle_plan_has_concrete_constraints,
)
from webchat.orchestration.planning.ontology import (
    GoalKey,
    Goals,
    normalize_workflow_state,
    workflow_state,
)
from webchat.orchestration.planning.tool_routes import (
    ToolTransitionContext,
    ToolTransitionRouter,
)


@dataclass(frozen=True)
class PlannedTransition:
    """One validated application transition produced from a semantic turn plan."""

    state: dict[str, Any]
    tool_call: ToolCall | None = None
    response: str = ""
    suggestion_dimension: str | None = None


class TransitionController:
    """Own workflow branching from a validated semantic domain and goal."""

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
    _STRUCTURED_QUERY_FIELDS: ClassVar[set[str]] = {
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

    def __init__(self) -> None:
        self.tool_router = ToolTransitionRouter(self._merged_vehicle_filters)

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
        state = normalize_workflow_state(current_state)
        key, arguments = self._normalize_plan(
            plan, state, user_text, displayed_offers or []
        )
        preference_response = self._preference_selection(key, arguments, state)
        if preference_response is not None:
            return preference_response
        next_state = self._next_state(key, arguments, state)
        non_tool_response = self._non_tool_response(
            plan,
            key,
            arguments,
            next_state,
            displayed_vehicles,
            vehicle_search_state,
            page_vehicles or [],
        )
        if non_tool_response is not None:
            return non_tool_response
        if key == Goals.SALES_REGISTER_INTEREST:
            missing_interest = self._resolve_interest(arguments, state, next_state)
            if missing_interest is not None:
                return missing_interest
            next_state = self._next_state(key, arguments, state)

        tool_name, tool_arguments = self.tool_router.resolve(
            key,
            arguments,
            state,
            ToolTransitionContext(
                user_text=user_text,
                vehicle_search_state=vehicle_search_state,
                page_vehicles=page_vehicles or [],
            ),
        )
        if key == Goals.VEHICLE_CONTINUE_SEARCH:
            next_state["constraints"] = {
                key: value for key, value in tool_arguments.items() if key != "page"
            }
        return PlannedTransition(
            next_state,
            tool_call=ToolCall(f"plan-{uuid4()}", tool_name, tool_arguments),
        )

    def _normalize_plan(
        self,
        plan: TurnPlan,
        state: dict[str, Any],
        user_text: str,
        displayed_offers: list[dict[str, object]],
    ) -> tuple[GoalKey, dict[str, Any]]:
        arguments = dict(plan.arguments)
        key = GoalKey(plan.domain, plan.goal)
        self._clean_town_argument(arguments)
        if key == Goals.WORKSHOP_BROWSE_SERVICES and self._named_service_check(
            user_text
        ):
            # Provider-first planning remains authoritative for the domain. This
            # narrow conformance rule prevents a named support question from being
            # accepted as a structurally valid catalogue-browse request.
            key = Goals.WORKSHOP_CHECK_SERVICE
            arguments["serviceQuery"] = user_text[:200]
        if self._is_active_workshop_location_follow_up(
            key, arguments, state, user_text
        ):
            key = Goals.WORKSHOP_BOOK_SERVICE
            arguments = {
                **self._only(
                    arguments,
                    {"town", "dealershipId", "dateFrom", "dateTo"},
                ),
                "reuseActiveEntity": True,
            }
        if key in {
            Goals.VEHICLE_CHOOSE_PREFERENCES,
            Goals.VEHICLE_APPLY_PREFERENCE,
        }:
            recovered = self._recover_vehicle_constraints(user_text)
            if vehicle_plan_has_concrete_constraints(arguments) or recovered:
                # A provider may select a preference goal while supplying or
                # omitting an explicit filter such as minYear. Recover customer-
                # stated constraints before a broad picker can discard them.
                key = Goals.VEHICLE_SEARCH
                arguments = {**recovered, **arguments}
        if key in {Goals.VEHICLE_SEARCH, Goals.VEHICLE_APPLY_PREFERENCE}:
            if vehicle_discovery_has_evidence(user_text, arguments):
                self._ground_vehicle_context_flags(arguments, user_text)
            else:
                # A vehicle noun alone does not make an inventory request. If a
                # provider emits a schema-valid search with only its default sort,
                # route the exact question through authoritative business facts
                # instead of allowing unrelated stock cards to escape.
                key = self._business_information_goal(state)
                arguments = {}
        active_offer = self._active_offer(arguments, displayed_offers)
        offer_domain = active_offer is not None or state.get("domain") == "offer"
        if (
            key == Goals.SALES_REGISTER_INTEREST
            and not arguments.get("vehicleId")
            and offer_domain
        ):
            key = Goals.OFFER_ENQUIRE
        if key in {Goals.SALES_ENQUIRE, Goals.OFFER_ENQUIRE} and offer_domain:
            key = Goals.OFFER_ENQUIRE
            arguments.setdefault("enquiryType", "finance")
            arguments.setdefault(
                "message",
                self.offer_enquiry_message(active_offer) if active_offer else user_text,
            )
            if active_offer and active_offer.get("offerId"):
                arguments.setdefault("offerId", active_offer["offerId"])
        return key, arguments

    @staticmethod
    def _recover_vehicle_constraints(user_text: str) -> dict[str, Any]:
        """Recover explicit filters only when a provider chose a broad picker."""
        from webchat.orchestration.routing.parsers import vehicle_filters

        aliases = {"q": "query", "dealershipTown": "town"}
        return {
            aliases.get(field, field): value
            for field, value in vehicle_filters(user_text).items()
            if field != "sort"
        }

    @staticmethod
    def _business_information_goal(state: dict[str, Any]) -> GoalKey:
        if state.get("domain") == "part_exchange":
            return Goals.BUSINESS_PART_EXCHANGE_INFORMATION
        if state.get("domain") == "business":
            active_goal = GoalKey("business", str(state.get("goal") or ""))
            if active_goal in {
                Goals.BUSINESS_FINANCE_INFORMATION,
                Goals.BUSINESS_PRIVACY_INFORMATION,
                Goals.BUSINESS_PART_EXCHANGE_INFORMATION,
                Goals.BUSINESS_GENERAL_INFORMATION,
            }:
                return active_goal
        return Goals.BUSINESS_GENERAL_INFORMATION

    @staticmethod
    def _named_service_check(user_text: str) -> bool:
        normalized = " ".join(user_text.casefold().split())
        catalogue_request = bool(
            re.search(
                r"\b(?:what|which|list|show)\b.*\bservice(?:s|\s+types?)\b"
                r"|\bservice\s+types?\b",
                normalized,
            )
        )
        if catalogue_request:
            return False
        return bool(
            re.search(
                r"\bdo you (?:do|offer|provide|support)\s+\S"
                r"|\bis .+? (?:supported|available)\s*(?:as a service)?[?.!]*$",
                normalized,
            )
        )

    @staticmethod
    def _clean_town_argument(arguments: dict[str, Any]) -> None:
        """Remove conversational references accidentally included in a town name."""
        town = arguments.get("town")
        if not isinstance(town, str):
            return
        cleaned = re.split(
            r"\s+(?:for|about)\s+(?:it|that|this|them|the\s+"
            r"(?:service|appointment|booking|job|work))\b",
            town,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .,?!")
        if cleaned:
            arguments["town"] = cleaned

    @staticmethod
    def _is_active_workshop_location_follow_up(
        key: GoalKey,
        arguments: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
    ) -> bool:
        """Keep a referred-to service active when the customer narrows its location."""
        entities = state.get("entities") or {}
        has_active_service = bool(
            entities.get("serviceTypeId") or entities.get("serviceQuery")
        )
        refers_to_service = bool(
            re.search(
                r"\b(?:for|about)\s+(?:it|that|this|the\s+service)\b",
                user_text,
                re.IGNORECASE,
            )
        )
        return bool(
            state.get("domain") == "workshop"
            and has_active_service
            and key in {Goals.DEALERSHIP_FIND, Goals.WORKSHOP_FIND_LOCATIONS}
            and arguments.get("town")
            and refers_to_service
        )

    def _preference_selection(
        self,
        key: GoalKey,
        arguments: dict[str, Any],
        state: dict[str, Any],
    ) -> PlannedTransition | None:
        if key != Goals.VEHICLE_APPLY_PREFERENCE:
            return None
        selection = self._preference_filter(arguments)
        if selection is not None:
            field, value = selection
            arguments[field] = value
            if self._state_owns_preference_filter(state, field):
                arguments["refineCurrentSearch"] = True
            return None
        dimension = str(arguments.get("preferenceDimension") or "startingPoint")
        next_state = self._next_state(
            Goals.VEHICLE_CHOOSE_PREFERENCES, arguments, state
        )
        next_state["stage"] = "choosing_preference"
        return PlannedTransition(
            next_state,
            response="Choose one of the available options below.",
            suggestion_dimension=dimension,
        )

    def _non_tool_response(
        self,
        plan: TurnPlan,
        key: GoalKey,
        arguments: dict[str, Any],
        next_state: dict[str, Any],
        displayed_vehicles: list[dict[str, object]],
        vehicle_search_state: dict[str, object] | None,
        page_vehicles: list[dict[str, object]],
    ) -> PlannedTransition | None:
        if arguments.get("referenceScope") == "currentPage" and not page_vehicles:
            next_state["stage"] = "clarifying"
            return PlannedTransition(
                next_state,
                response=(
                    "I can't identify any vehicle results on the current page. "
                    "Open the relevant results and ask me again."
                ),
            )
        if key in {Goals.CONVERSATION_RESPOND, Goals.CONVERSATION_CLARIFY}:
            return self._conversation_response(plan, key, next_state)
        if key == Goals.VEHICLE_CHOOSE_PREFERENCES:
            return self._vehicle_preferences_response(plan, arguments, next_state)
        if key == Goals.VEHICLE_CONTINUE_SEARCH and not vehicle_search_state:
            next_state["stage"] = "clarifying"
            return PlannedTransition(
                next_state,
                response=(
                    "There isn't an active vehicle result set to continue. "
                    "Tell me what kind of car you want to find."
                ),
                suggestion_dimension="startingPoint",
            )
        if key == Goals.VEHICLE_COMPARE and not self._valid_comparison(
            arguments, displayed_vehicles
        ):
            next_state["stage"] = "clarifying"
            return PlannedTransition(
                next_state,
                response=(
                    "Please name at least two vehicles to compare, or show some vehicles first."
                ),
            )
        return None

    @staticmethod
    def _conversation_response(
        plan: TurnPlan, key: GoalKey, next_state: dict[str, Any]
    ) -> PlannedTransition:
        response = plan.response.strip() or "Could you tell me a little more about what you need?"
        next_state["stage"] = (
            "answered" if key == Goals.CONVERSATION_RESPOND else "clarifying"
        )
        return PlannedTransition(next_state, response=response)

    @staticmethod
    def _vehicle_preferences_response(
        plan: TurnPlan,
        arguments: dict[str, Any],
        next_state: dict[str, Any],
    ) -> PlannedTransition:
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

    @staticmethod
    def _valid_comparison(
        arguments: dict[str, Any], displayed_vehicles: list[dict[str, object]]
    ) -> bool:
        vehicle_ids = list(arguments.get("vehicleIds") or [])
        queries = list(arguments.get("vehicleQueries") or [])
        displayed_ids = {str(item.get("vehicleId")) for item in displayed_vehicles}
        invalid_reference = bool(
            vehicle_ids and displayed_ids and not set(vehicle_ids).issubset(displayed_ids)
        )
        return not invalid_reference and (
            len(vehicle_ids) >= 2 or len(queries) >= 2
        )

    def _resolve_interest(
        self,
        arguments: dict[str, Any],
        state: dict[str, Any],
        next_state: dict[str, Any],
    ) -> PlannedTransition | None:
        vehicle_id = arguments.get("vehicleId")
        if not vehicle_id and arguments.get("reuseActiveEntity"):
            vehicle_id = self._state_entity(state, "vehicleId")
        if vehicle_id:
            arguments["vehicleId"] = vehicle_id
            return None
        next_state["stage"] = "clarifying"
        return PlannedTransition(
            next_state,
            response="Which reserved vehicle would you like to register interest in?",
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
            "vehicle_details": "viewing_details",
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
        state = normalize_workflow_state(current_state)
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
            key = (
                Goals.WORKSHOP_BROWSE_SERVICES
                if action_type == "show_workshop_services"
                else Goals.WORKSHOP_BOOK_SERVICE
            )
            state = workflow_state(
                key, "planned", entities=entities, constraints=constraints
            )
            tool_name = (
                "list_service_types"
                if action_type == "show_workshop_services"
                else "list_workshop_slots"
            )
            return self.advance(state, tool_name, view_type)
        if action_type in {"next_vehicle_page", "compare_displayed_vehicles"}:
            key = (
                Goals.VEHICLE_CONTINUE_SEARCH
                if action_type == "next_vehicle_page"
                else Goals.VEHICLE_COMPARE
            )
            constraints = (
                dict(state.get("constraints") or {})
                if action_type == "next_vehicle_page"
                and state.get("domain") == "vehicle"
                else {}
            )
            state = workflow_state(key, "planned", constraints=constraints)
            tool_name = (
                "search_vehicles"
                if action_type == "next_vehicle_page"
                else "compare_vehicles"
            )
            return self.advance(state, tool_name, view_type)
        if action_type == "select_test_drive_vehicle":
            state = workflow_state(
                Goals.TEST_DRIVE_BOOK,
                "planned",
                entities={"vehicleId": action.get("vehicleId")},
            )
            return self.advance(state, "list_test_drive_slots", view_type)
        if action_type in {"start_vehicle_interest", "start_sales_enquiry"}:
            key = (
                Goals.SALES_REGISTER_INTEREST
                if action_type == "start_vehicle_interest"
                else Goals.SALES_ENQUIRE
            )
            state = workflow_state(
                key,
                "planned",
                entities={"vehicleId": action.get("vehicleId")},
            )
            tool_name = (
                "prepare_vehicle_interest"
                if action_type == "start_vehicle_interest"
                else "prepare_sales_enquiry"
            )
            return self.advance(state, tool_name, view_type)
        if action_type == "start_offer_enquiry":
            state = workflow_state(
                Goals.OFFER_ENQUIRE,
                "planned",
                entities={"offerId": action.get("offerId")},
            )
            return self.advance(state, "prepare_sales_enquiry", view_type)
        if action_type == "apply_vehicle_preference":
            constraints = self.preference_action_filters(state, action)
            state = workflow_state(
                Goals.VEHICLE_SEARCH, "planned", constraints=constraints
            )
            return self.advance(state, "search_vehicles", view_type)
        return state

    def _next_state(
        self,
        key: GoalKey,
        arguments: dict[str, Any],
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
        if key.domain == "conversation" and current_state:
            preserved = normalize_workflow_state(current_state)
            preserved["stage"] = (
                "clarifying" if key == Goals.CONVERSATION_CLARIFY else "answered"
            )
            return preserved
        entities = self._state_entities(key, arguments, current_state)
        constraints = self._state_constraints(key.domain, arguments, current_state)
        return workflow_state(
            key, "planned", entities=entities, constraints=constraints
        )

    def _state_entities(
        self,
        key: GoalKey,
        arguments: dict[str, Any],
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
        entities: dict[str, Any] = {}
        if (
            arguments.get("reuseActiveEntity")
            and key.domain == current_state.get("domain")
        ):
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
        if key.domain == "workshop":
            explicit_service_query = arguments.get("serviceQuery") or arguments.get("query")
            if explicit_service_query:
                entities["serviceQuery"] = explicit_service_query
                entities.pop("serviceTypeId", None)
            elif arguments.get("serviceTypeId"):
                entities["serviceTypeId"] = arguments["serviceTypeId"]
                entities.pop("serviceQuery", None)
            elif key in {
                Goals.WORKSHOP_BOOK_SERVICE,
                Goals.WORKSHOP_CHECK_SERVICE,
            } and not arguments.get("reuseActiveEntity"):
                entities.pop("serviceTypeId", None)
                entities.pop("serviceQuery", None)
        return entities

    def _state_constraints(
        self,
        domain: str,
        arguments: dict[str, Any],
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
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
        return constraints

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

        if not cls._STRUCTURED_QUERY_FIELDS.intersection(filters):
            return
        residual = cls._strip_typed_query_values(query.casefold(), filters)
        residual = cls._strip_numeric_query_values(residual, filters)
        meaningful_words = cls._meaningful_query_words(residual, filters.get("sort"))
        if meaningful_words:
            filters["q"] = " ".join(meaningful_words)
        else:
            filters.pop("q", None)

    @staticmethod
    def _strip_typed_query_values(residual: str, filters: dict[str, Any]) -> str:
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
        return residual

    @staticmethod
    def _strip_numeric_query_values(residual: str, filters: dict[str, Any]) -> str:
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
        return residual

    @classmethod
    def _meaningful_query_words(
        cls, residual: str, sort: object
    ) -> list[str]:
        generic_words = set(cls._GENERIC_VEHICLE_QUERY_WORDS)
        generic_words.update(
            {
                "priceAsc": {"cheap", "cheapest", "lowest"},
                "priceDesc": {"expensive", "highest"},
                "mileageAsc": {"low", "lowest"},
                "newest": {"latest", "new", "newest"},
            }.get(str(sort), set())
        )
        return [
            word
            for word in re.findall(r"[a-z0-9]+", residual)
            if word not in generic_words
        ]

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
            and state.get("goal") == Goals.VEHICLE_CHOOSE_PREFERENCES.goal
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
        state = normalize_workflow_state(current_state)
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
    def _state_entity(state: dict[str, Any], name: str) -> Any | None:
        return (state.get("entities") or {}).get(name)

    @staticmethod
    def _only(arguments: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
        return {
            key: value
            for key, value in arguments.items()
            if key in allowed and value is not None
        }
