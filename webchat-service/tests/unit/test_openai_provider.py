import json

import httpx
import pytest

from webchat.integrations.openai_provider import OpenAIProvider, response_input


@pytest.mark.asyncio
async def test_responses_request_is_stateless_and_requires_one_typed_turn_plan() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["parallel_tool_calls"] is False
        assert body["tool_choice"] == {"type": "function", "name": "plan_customer_turn"}
        assert [tool["name"] for tool in body["tools"]] == ["plan_customer_turn"]
        planner = body["tools"][0]
        assert "maxPricePence" in planner["parameters"]["properties"]
        assert "workshop_booking" in planner["parameters"]["properties"]["intent"]["enum"]
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
                        "arguments": '{"intent":"vehicle_search","make":"Volvo"}',
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
    assert reply.plan.intent == "vehicle_search"
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
