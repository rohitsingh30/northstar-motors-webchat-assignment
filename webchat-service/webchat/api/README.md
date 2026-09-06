# API package

The API validates browser input, authorizes a conversation session, coordinates application
services, and returns public contracts.

| File | Responsibility |
| --- | --- |
| `router.py` | assemble `/api/chat/v1` routes |
| `conversations.py` | sessions, history, deletion, images, and ordinary `/turns` |
| `enquiries.py` | protected sales/contact/part-exchange payload completion |
| `workshop.py` | protected booking payloads, verified lookup, amendment/cancellation preparation |
| `drafts.py` | deterministic draft confirmation/cancellation lifecycle |
| `dependencies.py` | authorization and shared protected-result persistence |
| `models.py` | strict request/action models and limits |
| `restoration.py` | reconcile messages, interactions, drafts, and receipts |
| `security.py` | origin/content/rate/body checks and headers |
| `turn_admission.py` | daily and concurrent limits for new AI-backed turns while allowing idempotent retries |
| `errors.py` | safe API-wide exception and dealership-error translation |

Every public natural-language message and external chip reaches `/turns`. Protected endpoints accept
only fields declared secure by the capability registry and never replace the AI's public-language
role. Routes authorize before state access, reject unknown fields, and delegate business behavior
to orchestration/domain services. Confirm endpoints accept an idempotent client action ID and use
the stored draft; they never accept replacement operation fields.
