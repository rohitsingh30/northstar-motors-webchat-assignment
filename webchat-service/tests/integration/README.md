# Integration tests

Integration tests assemble the FastAPI application with temporary SQLite and injected provider or
mock platform boundaries. They verify behavior across modules.

## Files

| File | Coverage |
| --- | --- |
| [`test_conversations_api.py`](./test_conversations_api.py) | Conversation/session lifecycle, restoration, page snapshots, tool views, semantic-plan policy/re-plan behavior, business-fact answerability, typed actions, forms, validation, verified workshop flows |
| [`test_security.py`](./test_security.py) | Same-origin JSON enforcement and credentialed CORS |
| [`test_structured_flow_contracts.py`](./test_structured_flow_contracts.py) | Renderable tool termination and domain-goal service-flow invariants |

Add an integration test when a change crosses an API, repository, workflow, orchestration, or
browser payload boundary. Use public response contracts and verify sensitive fields are absent.
