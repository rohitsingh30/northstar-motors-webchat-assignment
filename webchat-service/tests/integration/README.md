# Integration tests

Integration tests assemble FastAPI with temporary SQLite and injected provider/platform boundaries.

| File | Coverage |
| --- | --- |
| `test_conversations_api.py` | sessions, turns, restoration, provider/tool/state/action/workflow boundaries |
| `test_security.py` | same-origin JSON, credentials, redaction, protected-input boundaries |
| `test_structured_flow_contracts.py` | normalized results, grounded output, public/protected capability contracts |

Use an integration test when behavior crosses an API, repository, workflow, orchestration, or
browser payload boundary. External services must be mocked/injected; sensitive fields must be
asserted absent from public persistence and AI-facing input.
