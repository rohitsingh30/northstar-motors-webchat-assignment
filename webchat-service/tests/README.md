# Webchat test suite

Tests run against the webchat package with temporary SQLite databases and injected/fake HTTP or AI
dependencies. They do not require edits to `dealership-platform`.

## Files and folders

| Path | Responsibility |
| --- | --- |
| [`test_health.py`](./test_health.py) | Health/readiness, production provider validation, and hosted widget entry asset |
| [`unit/`](./unit/README.md) | Focused deterministic functions, handlers, providers, retrieval, policy, state, repositories, workflows |
| [`integration/`](./integration/README.md) | FastAPI/session/security/restoration and end-to-end structured flow contracts |
| [`contract/`](./contract/README.md) | Dealership HTTP adapter contract using representative mock responses |
| [`browser/`](./browser/README.md) | Isolated Node ES-module tests for browser-only utilities |
| `e2e/` | Playwright responsive UI checks plus configured-model product, exhaustive, and stress journeys |

## Run

```bash
docker build --target test -t northstar-webchat-test:refactor ./webchat-service
docker run --rm northstar-webchat-test:refactor
```

Focused runs:

```bash
docker run --rm northstar-webchat-test:refactor pytest -q tests/unit
docker run --rm northstar-webchat-test:refactor pytest -q tests/integration
docker run --rm northstar-webchat-test:refactor pytest -q tests/contract
node --test webchat-service/tests/browser/*.mjs
npm run test:real-ai
npm run test:real-ai:stress
```

The configured-model commands require the running Compose stack and valid hosted-provider
credentials. `npm run test:real-ai` currently discovers 137 journeys; the stress command discovers
169. Test discovery is not a pass result.

A Starlette/httpx deprecation warning is emitted by the test-client dependency and is not a test
failure.

## Test boundaries

- Use temporary paths for SQLite.
- Inject providers and `httpx.MockTransport`; do not call live external services in CI tests.
- Assert public text/view/error contracts rather than private implementation call order unless the
  architecture boundary itself is under test.
- Add regression coverage at the lowest useful level and integration coverage for browser/API
  contracts.
