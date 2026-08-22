import json
import logging

from webchat.observability.logging import JsonFormatter
from webchat.observability.redaction import REDACTED, redact


def test_redacts_structured_and_embedded_personal_data() -> None:
    result = redact(
        {
            "email": "person@example.com",
            "nested": {"phone": "07700900123"},
            "error": "Contact person@example.com using Bearer secret-token",
            "operation": "vehicle_search",
        }
    )

    assert result["email"] == REDACTED
    assert result["nested"]["phone"] == REDACTED
    assert "person@example.com" not in result["error"]
    assert "secret-token" not in result["error"]
    assert result["operation"] == "vehicle_search"


def test_json_formatter_keeps_only_sanitized_context() -> None:
    record = logging.LogRecord("webchat", logging.INFO, "", 0, "turn_complete", (), None)
    record.context = {"conversation_id_hash": "abc", "message": "private text"}

    output = json.loads(JsonFormatter().format(record))

    assert output["conversation_id_hash"] == "abc"
    assert output["message"] == REDACTED
