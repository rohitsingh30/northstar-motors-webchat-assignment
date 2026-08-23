# Observability package

This package owns log formatting and removal of sensitive values before operational output.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`logging.py`](./logging.py) | Configure root JSON logging and emit sanitized exception type metadata |
| [`redaction.py`](./redaction.py) | Recursively redact sensitive keys plus embedded email, phone, and bearer-token patterns |

## Logging rule

Prefer structured context and pass it through `redact()`. Do not log request bodies, provider
prompts, raw platform bodies, contact data, cookies, API keys, booking proof, or free-text messages.

Tests for changes belong in `tests/unit/test_redaction.py`.
