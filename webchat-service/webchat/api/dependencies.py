"""Shared deterministic dependencies for the chat HTTP routers."""

from __future__ import annotations

import json

from fastapi import HTTPException, Request, status

from webchat.orchestration.state import workflow_state

COOKIE = "northstar_chat"

def _authorize(request: Request, conversation_id: str) -> None:
    token = request.cookies.get(COOKIE)
    if not token or not request.app.state.conversations.authorize(conversation_id, token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


def _workshop_state(active_workflow: str, stage: str, booking: dict | None = None) -> dict:
    booking = booking or {}
    entities = {}
    if booking.get("serviceTypeId"):
        entities["serviceTypeId"] = booking["serviceTypeId"]
    elif booking.get("serviceTypeName"):
        entities["serviceTypeName"] = booking["serviceTypeName"]
    constraints = {"dealershipId": booking["dealershipId"]} if booking.get("dealershipId") else {}
    return workflow_state(active_workflow, stage, entities=entities, constraints=constraints)


def _persist_receipt_once(request: Request, conversation_id: str, text: str, receipt: dict) -> None:
    identity = (
        receipt.get("kind"),
        receipt.get("reference"),
        receipt.get("status"),
    )
    for message in reversed(request.app.state.messages.list(conversation_id)):
        if message.role != "assistant" or message.view_type != "receipt":
            continue
        try:
            previous = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if (
            previous.get("kind"),
            previous.get("reference"),
            previous.get("status"),
        ) == identity:
            return
    request.app.state.messages.add(
        conversation_id,
        "assistant",
        text,
        None,
        "receipt",
        json.dumps(receipt, separators=(",", ":")),
    )

async def _tool_view(
    request: Request, conversation_id: str, tool_name: str, arguments: dict
) -> dict:
    """Execute a validated application tool and expose only its public view contract."""
    result = await request.app.state.tools.execute(tool_name, arguments, conversation_id)
    return {"viewType": result.view_type, "view": result.view_payload}
