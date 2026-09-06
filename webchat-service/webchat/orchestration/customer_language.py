"""Customer-facing language boundaries shared by every response path."""

import re

_IMPLEMENTATION_CONTEXT = re.compile(
    r"\b(?:"
    r"(?:on|from|in)\s+(?:this|the|your|current)\s+(?:web\s*)?page|"
    r"(?:current|host)\s+(?:web\s*)?page|"
    r"(?:page|website)\s+context|"
    r"trusted\s+(?:application|page|website)\s+context"
    r")\b",
    re.IGNORECASE,
)


def validate_customer_language(text: str) -> None:
    """Reject implementation context that should remain invisible to customers."""

    if _IMPLEMENTATION_CONTEXT.search(text):
        raise ValueError("customer response exposes implementation context")
