from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from webchat.orchestration.prompt import SYSTEM_POLICY
from webchat.orchestration.turn_planning import (
    TURN_PLAN_TOOL,
    parse_turn_plan,
    turn_plan_definition,
)

from .contracts import ProviderReply

TOOL_NAMES = [
    "search_vehicles",
    "get_vehicle",
    "get_vehicle_availability",
    "get_vehicle_facets",
    "compare_vehicles",
    "compare_vehicle_models",
    "list_offers",
    "get_offer",
    "list_dealerships",
    "list_dealership_departments",
    "get_dealership",
    "get_opening_hours",
    "list_opening_hours",
    "get_service_information",
    "list_service_types",
    "list_test_drive_slots",
    "list_workshop_locations",
    "list_workshop_slots",
    "get_business_information",
    "prepare_sales_enquiry",
    "prepare_test_drive",
    "prepare_vehicle_interest",
    "prepare_callback",
    "prepare_workshop_booking",
    "request_workshop_booking_lookup_form",
    "prepare_workshop_amendment",
    "prepare_workshop_cancellation",
    "prepare_dealership_message",
    "prepare_part_exchange",
    "request_part_exchange_estimate_form",
    "estimate_part_exchange",
]


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
FILTER_PROPERTIES = {
    "dealershipId": STRING, "vehicleId": {"type": "string", "pattern": "^veh-[0-9]{3}$"},
    "serviceTypeId": STRING, "serviceTypeName": {"type": "string", "description": "Human service name; resolve against live service types."},
    "dealershipTown": {"type": "string", "description": "Workshop town; resolve against live workshop locations."},
    "dateFrom": {"type": "string", "description": "ISO date YYYY-MM-DD"},
    "dateTo": {"type": "string", "description": "ISO date YYYY-MM-DD"}, "make": STRING,
    "productType": {"type": "string", "enum": ["PCP", "PCH"]},
}
SEARCH_PROPERTIES = {
    "page": {"type": "integer", "minimum": 1, "description": "Result page for a follow-up such as 'show me more'; increment the previous page."},
    "q": STRING, "make": STRING, "model": STRING, "fuelType": STRING, "transmission": STRING,
    "bodyStyle": STRING, "availability": {"type": "string", "enum": ["available", "reserved", "sold"]},
    "dealershipId": STRING,
    "dealershipTown": {"type": "string", "description": "Town name such as Stockport; resolve location from live dealership data."},
    "minPricePence": INTEGER, "maxPricePence": INTEGER,
    "maxMileage": INTEGER, "minYear": INTEGER,
    "sort": {"type": "string", "enum": ["newest", "priceAsc", "priceDesc", "mileageAsc"]},
}
CONTACT_PROPERTIES = {"firstName": STRING, "lastName": STRING, "email": STRING, "phone": STRING}
WORKFLOW_PROPERTIES = {
    "prepare_sales_enquiry": {**CONTACT_PROPERTIES, "dealershipId": STRING, "vehicleId": STRING, "enquiryType": STRING, "message": STRING},
    "prepare_test_drive": {**CONTACT_PROPERTIES, "slotId": STRING},
    "prepare_vehicle_interest": {**CONTACT_PROPERTIES, "vehicleId": STRING},
    "prepare_callback": {
        **CONTACT_PROPERTIES,
        "dealershipId": STRING,
        "department": STRING,
        "reason": STRING,
        "vehicleId": {"type": "string", "pattern": "^veh-[0-9]{3}$"},
        "preferredTime": STRING,
    },
    "prepare_workshop_booking": {**CONTACT_PROPERTIES, "slotId": STRING, "registration": STRING, "mileage": INTEGER, "notes": STRING},
    "prepare_workshop_amendment": {"slotId": STRING, "mileage": INTEGER, "notes": STRING},
    "prepare_workshop_cancellation": {},
    "prepare_dealership_message": {**CONTACT_PROPERTIES, "dealershipId": STRING, "department": STRING, "subject": STRING, "message": STRING, "preferredContactMethod": {"type": "string", "enum": ["email", "phone"]}},
    "prepare_part_exchange": {**CONTACT_PROPERTIES, "dealershipId": STRING, "registration": STRING, "mileage": INTEGER, "condition": STRING, "vehicleId": STRING},
    "estimate_part_exchange": {"registration": STRING, "mileage": INTEGER, "condition": {"type": "string", "enum": ["excellent", "good", "fair"]}},
}


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Explicit optional fields guide the model; registry validation remains authoritative."""
    return {"type": "object", "properties": properties, "additionalProperties": False}


def tool_definition(name: str) -> dict[str, Any]:
    if name == "search_vehicles":
        description = "Search live vehicle stock. Prices are pence: £35,000 is 3500000."
        parameters = object_schema(SEARCH_PROPERTIES)
    elif name == "compare_vehicles":
        description = "Compare two or three different vehicle IDs using only current platform facts."
        parameters = object_schema(
            {"vehicleIds": {"type": "array", "items": {"type": "string", "pattern": "^veh-[0-9]{3}$"}, "minItems": 2, "maxItems": 3}}
        )
    elif name == "compare_vehicle_models":
        description, parameters = (
            "Compare two or three named vehicle models by resolving each name against current available stock.",
            object_schema({"queries": {"type": "array", "items": STRING, "minItems": 2, "maxItems": 3}}),
        )
    elif name == "get_vehicle_facets":
        description, parameters = "Load current searchable vehicle facets from live stock.", object_schema({})
    elif name in {"get_vehicle", "get_vehicle_availability", "get_offer", "get_dealership", "get_opening_hours"}:
        description, parameters = "Get one current Northstar record by stable ID.", object_schema({"id": STRING})
    elif name == "list_dealership_departments":
        description, parameters = "List the departments exposed by each current dealership.", object_schema({})
    elif name == "list_dealerships":
        description, parameters = (
            "Find Northstar dealerships, optionally by town. For questions asking whether a town has a dealership, always pass that town.",
            object_schema({"town": {"type": "string", "description": "Town to check, for example Newcastle."}}),
        )
    elif name == "list_opening_hours":
        description, parameters = (
            "List current opening hours, optionally narrowed to a weekday, dealership town, and department.",
            object_schema({
                "day": {"type": "string", "description": "Weekday name, for example Saturday."},
                "town": {"type": "string", "description": "Dealership town, for example Manchester."},
                "department": {
                    "type": "string",
                    "enum": ["sales", "service", "parts"],
                    "description": "Dealership department when the customer names one.",
                },
            }),
        )
    elif name == "list_workshop_slots":
        description, parameters = (
            "List workshop availability only after a service is known; include serviceTypeId or serviceTypeName plus known town/date filters.",
            object_schema(FILTER_PROPERTIES),
        )
    elif name == "get_service_information":
        description, parameters = (
            "Get the current price, duration, and description for one workshop service named or described by the customer.",
            object_schema({
                "q": {"type": "string", "description": "The customer's service wording."},
                "serviceTypeId": STRING,
            }),
        )
    elif name in {"list_offers", "list_test_drive_slots"}:
        description, parameters = "List current offers or availability using optional filters.", object_schema(FILTER_PROPERTIES)
    elif name == "request_part_exchange_estimate_form":
        description = (
            "Show the three-field indicative part-exchange estimate form. Call this whenever "
            "the customer wants an estimate but registration, mileage, or condition is missing. "
            "Pass any valuation details already supplied so the form can prefill them."
        )
        parameters = object_schema(
            {
                "registration": STRING,
                "mileage": INTEGER,
                "condition": {
                    "type": "string",
                    "enum": ["excellent", "good", "fair"],
                },
            }
        )
    elif name == "estimate_part_exchange":
        description = "Return the platform's indicative part-exchange range from registration, mileage, and condition. No contact details are needed for this read-only estimate."
        parameters = object_schema(WORKFLOW_PROPERTIES[name])
    elif name in WORKFLOW_PROPERTIES:
        description = "Prepare a draft only; collect missing details and never claim it has been submitted."
        parameters = object_schema(WORKFLOW_PROPERTIES[name])
    elif name == "request_workshop_booking_lookup_form":
        description, parameters = (
            (
                "Show the private booking lookup form; never request proof in chat. Preserve "
                "whether the customer wants to view, amend, or cancel the verified booking."
            ),
            object_schema(
                {"mode": {"type": "string", "enum": ["lookup", "amend", "cancel"]}}
            ),
        )
    else:
        description, parameters = f"Get current Northstar {name.replace('_', ' ')}.", object_schema({})
    return {"type": "function", "name": name, "description": description, "parameters": parameters, "strict": False}


def response_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], separators=(",", ":")),
                    }
                )
        elif message.get("role") == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
        else:
            items.append({"role": message["role"], "content": message["content"]})
    return items


class OpenAIProvider:
    """Small Responses API adapter; policy and tool execution remain application-owned."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        *,
        endpoint: str | None = None,
        azure: bool = False,
    ):
        self.model = model
        self._api_key = api_key
        self._azure = azure
        base_url = "https://api.openai.com/v1/"
        if azure:
            base_url = self._azure_base_url(endpoint or "")
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(25, connect=5),
        )
        self._owns_client = client is None

    @staticmethod
    def _azure_base_url(endpoint: str) -> str:
        parsed = urlsplit(endpoint.strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("AZURE_OPENAI_ENDPOINT must be an absolute URL")
        path = parsed.path.rstrip("/")
        if path.endswith("/openai/v1"):
            return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", ""))
        return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        response = await self._client.post(
            "/responses",
            headers={
                ("api-key" if self._azure else "Authorization"): (
                    self._api_key if self._azure else f"Bearer {self._api_key}"
                ),
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": SYSTEM_POLICY,
                "input": response_input(messages),
                # Production turns are always a typed semantic plan. Business tools
                # are deliberately not exposed here: the transition controller owns
                # which operation and UI state follows each intent.
                "tools": [turn_plan_definition()],
                "tool_choice": {"type": "function", "name": TURN_PLAN_TOOL},
                "parallel_tool_calls": False,
                "max_output_tokens": 900,
                "store": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        plans = []
        for item in payload.get("output", []):
            if item.get("type") != "function_call":
                continue
            if item.get("name") != TURN_PLAN_TOOL:
                raise ValueError("OpenAI returned an unknown turn planner")
            if not item.get("call_id"):
                raise ValueError("OpenAI returned a turn plan without an ID")
            try:
                arguments = json.loads(item.get("arguments", "{}"))
            except json.JSONDecodeError as error:
                raise ValueError("OpenAI returned invalid turn-plan arguments") from error
            if not isinstance(arguments, dict):
                raise TypeError("OpenAI returned a non-object turn plan")
            plans.append(parse_turn_plan(arguments))
        if len(plans) != 1:
            raise ValueError("OpenAI must return exactly one typed turn plan")
        text = payload.get("output_text") or ""
        if not text:
            # Some Azure Foundry deployments omit the convenience output_text
            # field while returning the same content under message.content.
            parts: list[str] = []
            for item in payload.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"} and content.get("text"):
                        parts.append(str(content["text"]))
            text = "\n".join(parts)
        return ProviderReply(text=str(text), plan=plans[0])
