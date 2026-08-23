# Northstar Motors AI Webchat

This repository contains the Northstar Motors website, supplied local dealership platform, and a
server-side AI webchat. The webchat keeps protected dealership operations and credentials out of
the browser, stores anonymous conversations in SQLite, and uses explicit confirmation before any
business record is created or changed.

## Start

Requirements:

- Docker with Docker Compose
- Ports `4010`, `4020`, and `4173` available

Copy the environment template:

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` for AI responses. Without it, the service uses a deterministic local
provider so the application and automated tests can run without network access. Do not add either
the OpenAI key or dealership API key to browser code.

```bash
docker compose up --build -d
```

Then open:

- Dealership website: http://localhost:4173
- API documentation: http://localhost:4010/docs
- Dealership Systems Console: http://localhost:4010/admin

The standalone webchat API is available at `http://localhost:4020`. Its restricted CORS policy
accepts the configured website origin and supports the widget's HttpOnly conversation cookie.

Public catalogue and reference reads do not require authentication. Saved customer-record reads
and write operations require this local API key:

```text
northstar-local-development
```

Send it using the `X-API-Key` header. Keep the key in server-side code rather than
browser-delivered code.

Start with:

- [PRODUCT-BRIEF.md](./PRODUCT-BRIEF.md) for the product requirements;
- [docs/PRD.md](./docs/PRD.md) for the detailed product requirements and acceptance criteria;
- [docs/HLD.md](./docs/HLD.md) for the current architecture, trust boundaries, and decisions;
- [docs/LLD.md](./docs/LLD.md) for the current component, data, API, workflow, and test design;
- [webchat-service/README.md](./webchat-service/README.md) for service operation and the complete
  module documentation map;
- [docs/diagrams/README.md](./docs/diagrams/README.md) for architecture images and editable sources;
- [docs/INTEGRATION-GUIDE.md](./docs/INTEGRATION-GUIDE.md) for API usage;
- [docs/WIDGET-INTEGRATION.md](./docs/WIDGET-INTEGRATION.md) for installing and controlling the
  reusable browser widget;
- [docs/BUSINESS-SEMANTICS.md](./docs/BUSINESS-SEMANTICS.md) for operation outcomes;
- [docs/SEEDED-SCENARIOS.md](./docs/SEEDED-SCENARIOS.md) for the seed data catalogue.
- [docs/IMPLEMENTATION-PLAN.md](./docs/IMPLEMENTATION-PLAN.md) for task status, verification gates,
  and safe instructions for continuing the implementation.

## How the webchat works

![Current webchat architecture](./docs/diagrams/webchat-system-architecture.svg)

```text
Browser widget
    │ restricted-origin JSON + HttpOnly conversation cookie
    ▼
Webchat service ──► OpenAI Responses API
    │
    ├──► SQLite conversation/workflow state
    └──► Dealership platform (authoritative reads and confirmed writes)
```

The language model may request platform reads or prepare a workflow draft. It cannot call the
confirmation executor. Application code validates the draft, waits for the user to press Confirm,
persists an idempotency key, and only then sends the protected dealership request.

Workshop booking lookup is separate from ordinary chat: reference, surname, registration, and
phone are sent directly to a deterministic endpoint and are not stored in the transcript, model
input, draft, or logs.

## Configuration

| Variable | Purpose | Local default |
| --- | --- | --- |
| `OPENAI_API_KEY` | Server-side Responses API credential | Empty; uses fake provider |
| `OPENAI_MODEL` | Configurable model ID | `gpt-5-mini` |
| `LLM_PROVIDER` | Hosted provider mode (`openai` or `azure`) | `openai` |
| `AZURE_OPENAI_ENDPOINT` | Azure/Foundry OpenAI endpoint | Empty |
| `AZURE_OPENAI_DEPLOYMENT` | Azure deployment name | Empty |
| `AZURE_OPENAI_API_KEY` | Azure server-side credential | Empty |
| `NORTHSTAR_API_KEY` | Protected dealership-operation key | Local development value |
| `NORTHSTAR_BASE_URL` | Internal dealership API URL | `http://dealership-platform:4010` |
| `WEBCHAT_DATABASE_PATH` | Conversation SQLite path | `/data/webchat.sqlite3` |
| `WEBCHAT_COOKIE_SECURE` | Adds Secure to the chat cookie | `false` for local HTTP |
| `WEBCHAT_ALLOWED_ORIGIN` | Accepted browser origin for writes | `http://localhost:4173` |
| `WEBCHAT_RETENTION_DAYS` | Anonymous conversation retention | `30` |
| `SEMANTIC_PLAN_POLICY_MODE` | Semantic-plan policy: `off`, `observe`, or `enforce`; legacy `GENERAL_RESPONSE_GATE_MODE` remains accepted | `enforce` in Compose |
| `LOG_LEVEL` | Structured server log level | `INFO` |

## Tests

Build and run the isolated webchat tests:

```bash
docker build --target test -t northstar-webchat-test ./webchat-service
docker run --rm northstar-webchat-test
```

The current Python suite contains 335 tests. Run lint and all widget-module syntax checks:

```bash
docker run --rm northstar-webchat-test ruff check webchat tests
for file in $(find webchat-service/webchat/widget -name '*.js'); do
  node --check "$file" || exit 1
done
node --test webchat-service/tests/browser/*.mjs
```

Run the supplied dealership-platform tests:

```bash
docker compose exec dealership-platform python -m unittest discover -s tests
```

Useful static checks:

```bash
docker compose config
node --check dealership-website/src/app.js
git diff --check
```

## Important boundaries

- `dealership-platform/` is supplied and must not be changed.
- The browser stores an opaque conversation ID, UI preference, and ordinary reusable form values;
  authorization is an HttpOnly cookie and private booking lookup proof is explicitly excluded.
- Platform facts and operation statuses are never guessed by the model.
- Every write uses a persisted draft and explicit confirmation.
- Creation retries reuse an idempotency key. Workshop amendment/cancellation retries are locally
  deduplicated because those platform endpoints do not accept that header.
- Operational logs exclude message bodies, contact details, booking proof, and credentials.

## Reset

```bash
./reset.sh
```

Resetting clears dealership-platform records and restores the original seed data.

## Stop

```bash
docker compose down
```

State is retained in the `northstar-platform-data` Docker volume until reset or removal.

## Dealership services

The dealership platform provides:

- vehicle inventory and offers;
- dealership locations and opening hours;
- sales enquiries, callbacks, and test drives;
- workshop availability and bookings;
- dealership messages and part-exchange valuations.

The customer website is editable and uses the same vehicle inventory API.
