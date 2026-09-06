# Northstar Motors AI Webchat

Live website: [https://scheduling-production-modifications-producers.trycloudflare.com/](https://scheduling-production-modifications-producers.trycloudflare.com/)

This repository contains the supplied Northstar Motors website and dealership platform plus a
server-side conversational assistant. It helps customers discover vehicles, get accurate
dealership information, and complete common sales and workshop tasks using authoritative platform
data.

The implementation covers the journeys required by [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md):

- natural-language vehicle search, refinement, comparison, availability, details, and offers;
- sales enquiries, callbacks, reserved-vehicle interest, and confirmed test-drive bookings;
- service discovery plus new, retrieved, amended, and cancelled workshop bookings;
- dealership details, opening hours and exceptions, messages, callbacks, and indicative
  part-exchange estimates;
- responsive, keyboard-usable conversation with page context, persisted history, progress states,
  trusted links and choices, explicit confirmations, and recoverable errors.

## Run locally

Requirements:

- Docker with Docker Compose
- ports `4010`, `4020`, and `4173`
- a Responses-compatible LLM endpoint, API key, and model

```bash
cp .env.example .env
```

Set these values in the uncommitted `.env` file:

```dotenv
LLM_PROVIDER_URL=https://your-provider.example/v1
LLM_API_KEY=replace-me
LLM_MODEL=replace-me
```

Development and production startup fail when any of those values is missing. The deterministic
provider is available only when `ENVIRONMENT=test`; a configured hosted-provider failure never
falls back to it.

```bash
docker compose up --build -d
```

Open:

- website and webchat: http://localhost:4173
- dealership API: http://localhost:4010
- dealership API docs: http://localhost:4010/docs
- dealership systems console: http://localhost:4010/admin
- webchat service: http://localhost:4020

Database migrations run automatically when each service starts. The webchat database is stored in
the `northstar-webchat-data` volume.

## How it works

The website loads a browser widget served by the FastAPI webchat service. A configured hosted model
interprets the customer's turn, selects from an application-controlled tool catalogue, and composes
the response from validated results. Application code validates intent/tool compatibility, entity
references, arguments, links, and protected actions before calling the dealership platform.

The dealership platform remains authoritative for dynamic business facts and operation outcomes.
SQLite stores the transcript, page context, workflow progress, trusted result references, and
idempotency state so a conversation can survive refreshes and interrupted requests.

## Privacy and protected operations

- Dealership and LLM credentials remain server-side.
- Contact details and booking-verification proof use protected endpoints and are excluded from the
  normal chat transcript, model context, and operational logs.
- The model may prepare a draft but cannot confirm a business operation. The customer must review
  and explicitly confirm each write.
- Confirmed operations are idempotent, and live availability is rechecked where required.
- Platform facts, prices, links, appointment times, and completion statuses are grounded in trusted
  application data rather than invented by the model.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER_URL` | Responses-compatible provider base URL | required outside tests |
| `LLM_API_KEY` | server-only provider credential | required outside tests |
| `LLM_MODEL` | model used for turn resolution, planning, and composition | required outside tests |
| `LLM_TURN_TIMEOUT_SECONDS` | complete AI/tool turn deadline | `45` |
| `MCP_SERVERS_JSON` | optional read-only Streamable HTTP MCP servers | empty |
| `NORTHSTAR_API_KEY` | server-only dealership operation key | local development value |
| `NORTHSTAR_BASE_URL` | internal dealership API URL | `http://dealership-platform:4010` |
| `WEBCHAT_PORT` | host port for the webchat service | `4020` |
| `WEBCHAT_DATABASE_PATH` | SQLite path | `/data/webchat.sqlite3` |
| `WEBCHAT_ALLOWED_ORIGIN` | browser origin accepted for writes | `http://localhost:4173` |
| `WEBCHAT_COOKIE_SECURE` | add `Secure` to the session cookie | `false` locally |
| `WEBCHAT_RETENTION_DAYS` | anonymous conversation retention | `30` |
| `WEBCHAT_REQUESTS_PER_MINUTE` | per-client mutation limit; `0` disables it for local Docker | `0` locally; `30` in the public deployment |
| `WEBCHAT_DAILY_TURN_LIMIT` | global daily AI-turn limit; `0` disables | `0` |
| `WEBCHAT_MAX_CONCURRENT_TURNS` | concurrent AI turn limit | `8` |
| `WEBCHAT_TRUST_PROXY_HEADERS` | trust gateway client-address headers | `false` |
| `LOG_LEVEL` | structured log level | `INFO` |

## Important decisions and known limitations

- The core product requires no hosted dependency other than the configured LLM provider. Optional
  MCP servers are disabled unless configured and only read-only tools are exposed.
- Development and production require a working hosted-model configuration; the deterministic
  provider is test-only.
- Model context uses a bounded recent transcript and does not currently create a long-conversation
  summary.
- Protected form values are held in per-conversation `sessionStorage` until submission or tab
  closure. Deploy only trusted same-origin scripts.

## Focused verification

```bash
.venv/bin/ruff check webchat-service/webchat
.venv/bin/pytest -q \
  webchat-service/tests/unit/test_conversational_contracts.py \
  webchat-service/tests/unit/test_hosted_llm.py \
  webchat-service/tests/unit/test_workflow_state_and_policy.py \
  webchat-service/tests/unit/test_routing_architecture.py \
  webchat-service/tests/unit/test_service_resolution.py \
  webchat-service/tests/unit/test_vehicle_reference_context.py
node --test \
  webchat-service/tests/browser/test_conversational_workflows.mjs \
  webchat-service/tests/browser/test_read_only_cards.mjs \
  webchat-service/tests/browser/test_widget_state.mjs
docker compose config
git diff --check
```

The test image preloads the local embedding model, so the complete Python suite can run without a
model download at test time:

```bash
docker build --target test -t northstar-webchat-test ./webchat-service
docker run --rm northstar-webchat-test
```

Configured-model browser suites require the running stack and valid hosted-provider credentials:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

`npm run test:real-ai` and `npm run test:real-ai:stress` run the focused release and stress suites.
Test discovery alone is not a passing result.

## Project documentation

- [Product brief](./PRODUCT-BRIEF.md)
- [Integration guide](./docs/INTEGRATION-GUIDE.md)
- [Business semantics](./docs/BUSINESS-SEMANTICS.md)
- [Reviewed customer knowledge](./docs/CUSTOMER-KNOWLEDGE.md)
- [Seeded scenarios](./docs/SEEDED-SCENARIOS.md)
- [Webchat service guide](./webchat-service/README.md)
- [Private demo deployment](./deployment/gcp/README.md)
