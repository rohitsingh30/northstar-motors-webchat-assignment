# Orchestration context

This folder assembles the bounded, labelled state used to understand a turn.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`builder.py`](./builder.py) | `TurnContext`, history assembly, current/initial page snapshots, displayed vehicle/offer references, search state |

## Context rules

- Include at most the last 20 persisted messages.
- Label page snapshots and workflow state as developer data.
- Preserve current and initial page context separately.
- Derive positional vehicle/offer references only from application-owned closed payloads.
- Keep a single vehicle-detail card separate from the last ordered search result so positional
  follow-ups remain stable; use trusted workflow state for subsequent “this vehicle” references.
- Keep current vehicle search filters/page for explicit refinement and pagination.
- Add typed widget actions as trusted structured data.
- Perform no dealership, provider, or workflow mutations.

Tests live in `tests/unit/test_vehicle_reference_context.py` and relevant conversation integration
tests.
