import json

import httpx
import pytest

from webchat.integrations.openai_provider import OpenAIProvider, response_input
from webchat.orchestration.planning.ontology import ALL_GOALS
from webchat.orchestration.planning.turn_plan import turn_plan_definition


def test_hosted_schema_contains_every_and_only_canonical_domain_goal() -> None:
    parameters = turn_plan_definition()["parameters"]
    schema_goals = set()
    for definition in parameters["$defs"].values():
        properties = definition.get("properties", {})
        if "domain" not in properties or "goal" not in properties:
            continue
        goal_schema = properties["goal"]
        goals = (
            goal_schema["enum"]
            if "enum" in goal_schema
            else [goal_schema["const"]]
        )
        schema_goals.update(
            (properties["domain"]["const"], goal) for goal in goals
        )

    assert schema_goals == {(key.domain, key.goal) for key in ALL_GOALS}


@pytest.mark.asyncio
async def test_responses_request_is_stateless_and_requires_one_typed_turn_plan() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["parallel_tool_calls"] is False
        assert body["tool_choice"] == {"type": "function", "name": "plan_customer_turn"}
        assert [tool["name"] for tool in body["tools"]] == ["plan_customer_turn"]
        planner = body["tools"][0]
        serialized_schema = json.dumps(planner["parameters"])
        assert "maxPricePence" in serialized_schema
        assert "book_service" in serialized_schema
        assert "workshop" in serialized_schema
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "output_text": "",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "plan_customer_turn",
                        "arguments": (
                            '{"version":2,"domain":"vehicle",'
                            '"goal":"search","make":"Volvo"}'
                        ),
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.openai.com/v1") as http:
        reply = await OpenAIProvider("secret", "test-model", http).generate_turn(
            [{"role": "user", "content": "Show me Volvos"}]
        )

    assert reply.tool_calls == []
    assert reply.plan is not None
    assert (reply.plan.domain, reply.plan.goal) == ("vehicle", "search")
    assert reply.plan.arguments["make"] == "Volvo"


@pytest.mark.asyncio
async def test_responses_rejects_unknown_tools() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output_text": "",
                "output": [{"type": "function_call", "call_id": "bad", "name": "delete_everything", "arguments": "{}"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.openai.com/v1") as http:
        with pytest.raises(ValueError, match="unknown turn planner"):
            await OpenAIProvider("secret", "test-model", http).generate_turn(
                [{"role": "user", "content": "hello"}]
            )


def test_internal_tool_messages_convert_to_responses_items() -> None:
    items = response_input(
        [
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "name": "list_offers", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": '{"items":[]}'},
        ]
    )

    assert items[0]["type"] == "function_call"
    assert items[1] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"items":[]}',
    }


def test_azure_endpoint_uses_foundry_responses_base_url() -> None:
    assert OpenAIProvider._azure_base_url(
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=v1"
    ) == "https://example.cognitiveservices.azure.com/openai/v1/"
