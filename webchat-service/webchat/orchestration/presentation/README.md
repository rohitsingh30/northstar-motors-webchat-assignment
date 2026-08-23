# Presentation package

Presentation turns trusted tool/provider output into the final persisted assistant response. It
does not execute workflows or call repositories.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`response.py`](./response.py) | Renderable view allow-list, direct-answer rules, final `PresentedResponse`, facet/clarification suggestions |
| [`suggestions.py`](./suggestions.py) | Application-owned labels, text, typed actions, live facet choices, and next-step suggestion policies |

## Rules

- Preserve a renderable tool view rather than replacing it with provider prose.
- Use live tool facts for facet suggestions.
- Suggestions carry safe conversational text and optional allow-listed structured actions.
- Do not place dynamic business facts in static suggestion code.
- Keep view payloads closed and versioned.

Tests live primarily in `tests/unit/test_suggestions.py` and integration flow contracts.
