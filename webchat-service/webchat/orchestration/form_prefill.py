"""Conservative, customer-authored context carry-over for collecting forms."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormTopic:
    subject: str
    message: str
    callback_reason: str
    department: str | None = None
    enquiry_type: str | None = None


def contextual_form_prefill(
    tool_name: str,
    arguments: dict[str, Any],
    latest_customer_message: str,
    recent_customer_messages: Iterable[str] = (),
) -> dict[str, Any]:
    """Fill only a high-confidence topic omitted by a form-opening proposal."""
    result = dict(arguments)
    topic = _latest_topic(
        [latest_customer_message, *reversed(tuple(recent_customer_messages))]
    )
    if topic is None:
        return result

    if tool_name == "prepare_callback":
        result.setdefault("reason", topic.callback_reason)
        if topic.department:
            result.setdefault("department", topic.department)
    elif tool_name == "prepare_dealership_message":
        result.setdefault("subject", topic.subject)
        result.setdefault("message", topic.message)
        if topic.department:
            result.setdefault("department", topic.department)
    elif tool_name == "prepare_sales_enquiry":
        result.setdefault("message", topic.message)
        if topic.enquiry_type:
            result.setdefault("enquiryType", topic.enquiry_type)
    return result


def _latest_topic(messages: Iterable[str]) -> FormTopic | None:
    for message in messages:
        text = " ".join(str(message or "").strip().split())
        normalized = text.casefold()
        if not normalized or normalized in {"yes", "no", "okay", "ok", "please"}:
            continue
        if _contains(normalized, "pickup", "pick up", "collect", "collection") and _contains(
            normalized, "car", "vehicle", "it"
        ):
            return FormTopic(
                "Vehicle collection",
                "Please confirm whether vehicle collection or home pickup is available.",
                "Vehicle collection or home pickup",
                department="sales",
            )
        if _contains(normalized, "part exchange", "part-exchange", "trade in", "trade-in"):
            return FormTopic(
                "Part-exchange enquiry",
                "Please contact me about a part exchange.",
                "Discuss a part exchange",
                department="sales",
                enquiry_type="part-exchange",
            )
        if _contains(normalized, "finance", "pcp", "pch"):
            return FormTopic(
                "Vehicle finance",
                "Please contact me about vehicle finance.",
                "Discuss vehicle finance",
                department="sales",
                enquiry_type="finance",
            )
        if _contains(normalized, "availability", "available") and _contains(
            normalized, "car", "vehicle", "model", "it"
        ):
            return FormTopic(
                "Vehicle availability",
                "Please contact me about vehicle availability.",
                "Discuss vehicle availability",
                department="sales",
                enquiry_type="availability",
            )
        if _contains(normalized, "mot", "service", "servicing", "workshop"):
            return FormTopic(
                "Service enquiry",
                "Please contact me about a service enquiry.",
                "Discuss a service enquiry",
                department="service",
            )
        if re.search(r"\bparts?\b", normalized):
            return FormTopic(
                "Parts enquiry",
                "Please contact me about a parts enquiry.",
                "Discuss a parts enquiry",
                department="parts",
            )
    return None


def _contains(text: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in phrases)
