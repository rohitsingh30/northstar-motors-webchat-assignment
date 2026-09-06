"""Application tool handler for authoritative public business information."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.domain.interactions import ActionHandoff, single_action_interaction
from webchat.orchestration.tools.business_information import BusinessInformationResolver
from webchat.orchestration.tools.inputs import BusinessInformationQuery
from webchat.orchestration.tools.result import ToolResult, alternative_offer


class BusinessInformationGateway(Protocol):
    async def get_business_information(self) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class BusinessInformationToolHandler:
    """Resolve one customer question to the smallest supported platform fact set."""

    def __init__(
        self,
        dealership: BusinessInformationGateway,
        resolver: BusinessInformationResolver | None = None,
    ):
        self.dealership = dealership
        self.resolver = resolver or BusinessInformationResolver()
        self.routes: dict[str, ToolMethod] = {
            "get_business_information": self._get,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _get(self, arguments: dict[str, Any]) -> ToolResult:
        query = BusinessInformationQuery.model_validate(arguments)
        data = await self.dealership.get_business_information()
        resolution = self.resolver.resolve(data, topic=query.topic, question=query.question)
        facts = {
            "outcome": resolution.outcome,
            "topic": resolution.topic,
            "factKeys": [fact.key for fact in resolution.facts],
        }
        if resolution.outcome == "unavailable":
            text = (
                "I don't have confirmed Northstar information that answers that question. "
                "Would you like me to help you contact a dealership?"
            )
            return ToolResult(
                text,
                None,
                None,
                facts,
                single_action_interaction(
                    text,
                    {"type": "show_dealership_contact_options"},
                    handoff=ActionHandoff(
                        topic=query.topic,
                        customerReason=query.question,
                    ),
                ),
                alternative_offer=alternative_offer(
                    reason_code="confirmed_information_unavailable",
                    requested_outcome=f"Confirmed Northstar information about {query.topic.replace('_', ' ')}",
                    failure_reason="Northstar does not have confirmed online information that answers this question.",
                    offered_outcome=(
                        "You can contact a dealership team for a confirmed answer."
                    ),
                    changes=[
                        {
                            "dimension": "Answer channel",
                            "requested": "Online information",
                            "offered": "Dealership contact",
                        }
                    ],
                    preserved=[query.topic.replace("_", " ")],
                ),
            )
        if resolution.outcome == "ambiguous":
            labels = ", ".join(dict.fromkeys(fact.label for fact in resolution.facts))
            return ToolResult(
                "I found more than one possible Northstar information topic. "
                f"Please ask specifically about {labels}.",
                None,
                None,
                facts,
            )
        view_facts = [fact.as_view() for fact in resolution.facts]
        facts["facts"] = view_facts
        return ToolResult(
            f"Here is the current Northstar {query.topic.replace('_', '-')} information.",
            "business_information",
            {
                "version": 2,
                "organisation": data.get("organisation") or "Northstar Motors",
                "topic": resolution.topic,
                "facts": view_facts,
            },
            facts,
        )
