# Northstar Motors AI Webchat

Website - https://gtk-slow-eval-ourselves.trycloudflare.com/

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

Set the three provider-neutral `LLM_*` values in `.env` for hosted AI responses. The planner and
reviewer are compulsory separate requests to the supplied Responses-compatible provider and share
one API key and model.
Without a complete hosted configuration, development and test environments use the deterministic
local provider. Do not add either the LLM key or dealership API key to browser code.

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
- [webchat-service/README.md](./webchat-service/README.md) for service operation and the complete
  module documentation map;
- [docs/INTEGRATION-GUIDE.md](./docs/INTEGRATION-GUIDE.md) for API usage;
- [docs/BUSINESS-SEMANTICS.md](./docs/BUSINESS-SEMANTICS.md) for operation outcomes;
- [docs/SEEDED-SCENARIOS.md](./docs/SEEDED-SCENARIOS.md) for the seed data catalogue.

## How the webchat works

```text
Browser widget
    │ restricted-origin JSON + HttpOnly conversation cookie
    ▼
Webchat service ──► hosted planner call
                └─► compulsory independent reviewer call
    │
    ├──► SQLite conversation/workflow state
    └──► Dealership platform (authoritative reads and confirmed writes)
```

Semantic retrieval supplies relevant definitions from one unified application/MCP tool catalogue
plus evidence generated from the product and business documents. The hosted model proposes concrete
tool calls. A separate stateless reviewer request accepts, corrects, clarifies, or rejects the
proposal before deterministic policy and execution. Neither request can call the confirmation
executor. Application code validates the draft, waits for the user to press Confirm, persists an
idempotency key, and only then sends the protected dealership request.

Workshop booking lookup is separate from ordinary chat: reference, surname, registration, and
phone are sent directly to a deterministic endpoint and are not stored in the transcript, model
input, draft, or logs.

## Configuration

| Variable | Purpose | Local default |
| --- | --- | --- |
| `LLM_PROVIDER_URL` | Responses-compatible provider base URL | Empty; uses fake provider locally |
| `LLM_API_KEY` | Shared server-side credential for both semantic calls | Empty |
| `LLM_MODEL` | Provider model ID used by both independent semantic calls | Empty; required with hosted provider |
| `LLM_TURN_TIMEOUT_SECONDS` | Complete planner/reviewer iteration budget | `45` |
| `MCP_SERVERS_JSON` | Optional JSON array of Streamable HTTP MCP server configurations | Empty |
| `NORTHSTAR_API_KEY` | Protected dealership-operation key | Local development value |
| `NORTHSTAR_BASE_URL` | Internal dealership API URL | `http://dealership-platform:4010` |
| `WEBCHAT_PORT` | Host port exposing the webchat service | `4020` |
| `WEBCHAT_DATABASE_PATH` | Conversation SQLite path | `/data/webchat.sqlite3` |
| `WEBCHAT_COOKIE_SECURE` | Adds Secure to the chat cookie | `false` for local HTTP |
| `WEBCHAT_ALLOWED_ORIGIN` | Accepted browser origin for writes | `http://localhost:4173` |
| `WEBCHAT_RETENTION_DAYS` | Anonymous conversation retention | `30` |
| `WEBCHAT_REQUESTS_PER_MINUTE` | Per-client webchat API request ceiling | `60` |
| `WEBCHAT_DAILY_TURN_LIMIT` | Global UTC-day turn ceiling (`0` disables it) | `0` |
| `WEBCHAT_MAX_CONCURRENT_TURNS` | Concurrent AI-backed turn ceiling | `8` |
| `WEBCHAT_TRUST_PROXY_HEADERS` | Trust gateway-provided client addresses | `false` |
| `LOG_LEVEL` | Structured server log level | `INFO` |

## Tests

Build and run the isolated webchat tests:

```bash
docker build --target test -t northstar-webchat-test ./webchat-service
docker run --rm northstar-webchat-test
```

Run lint and all widget-module syntax checks as well:

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
