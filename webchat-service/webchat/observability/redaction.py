from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "email",
    "first_name",
    "last_name",
    "message",
    "northstar_api_key",
    "openai_api_key",
    "phone",
    "registration",
    "surname",
    "text",
}
EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE = re.compile(r"(?<!\w)(?:\+?44\s?\d|0\d)(?:[\s-]?\d){8,12}(?!\w)")
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~-]+")


def redact(value: Any, key: str | None = None) -> Any:
    """Return a log-safe copy; callers should log structured values through this function."""
    if key and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        cleaned = EMAIL.sub(REDACTED, value)
        cleaned = PHONE.sub(REDACTED, cleaned)
        return BEARER.sub(REDACTED, cleaned)
    return value
