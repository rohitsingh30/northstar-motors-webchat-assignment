from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from webchat.orchestration.planning.prompt import SYSTEM_POLICY
from webchat.orchestration.planning.turn_plan import (
    TURN_PLAN_TOOL,
    parse_turn_plan,
    turn_plan_definition,
)

from .contracts import ProviderReply


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
            headers=self._headers(),
            json=self._request_payload(messages),
        )
        response.raise_for_status()
        payload = response.json()
        plans = _turn_plans(payload)
        if len(plans) != 1:
            raise ValueError("OpenAI must return exactly one typed turn plan")
        return ProviderReply(text=_response_text(payload), plan=plans[0])

    def _headers(self) -> dict[str, str]:
        authorization = (
            self._api_key if self._azure else f"Bearer {self._api_key}"
        )
        return {
            "api-key" if self._azure else "Authorization": authorization,
            "Content-Type": "application/json",
        }

    def _request_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "instructions": SYSTEM_POLICY,
            "input": response_input(messages),
            # Only a semantic planner is exposed to the model. Deterministic
            # application transitions own business tools and UI state.
            "tools": [turn_plan_definition()],
            "tool_choice": {"type": "function", "name": TURN_PLAN_TOOL},
            "parallel_tool_calls": False,
            "max_output_tokens": 900,
            "store": False,
        }


def _turn_plans(payload: dict[str, Any]) -> list:
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
    return plans


def _response_text(payload: dict[str, Any]) -> str:
    if text := payload.get("output_text"):
        return str(text)
    # Some Azure Foundry deployments omit the convenience output_text field.
    parts = [
        str(content["text"])
        for item in payload.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") in {"output_text", "text"} and content.get("text")
    ]
    return "\n".join(parts)
