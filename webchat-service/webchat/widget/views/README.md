# Widget view modules

Views materialize closed server payloads into safe Shadow DOM. They do not perform requests, route
intent, mutate workflow state, or accept executable actions from card payloads.

| File | Responsibility |
| --- | --- |
| `message.js` | closed view dispatcher |
| `message-content.js` | safe paragraphs, lists, and trusted text/link segments |
| `structured-collection.js` | semantic trusted option/fact lists |
| `suggestions.js` | external quick replies and typed action data |
| `vehicle.js` | vehicle visuals plus the fixed trusted-ID modal control |
| `appointments.js` | one selected appointment summary plus read-only booking visuals |
| `information-cards.js` | read-only offer/dealership/hours/service/fact visuals |
| `workflow-confirmations.js` | static protected-write review visuals |
| `workflow-receipts.js` | read-only public receipts/restored booking views |
| `workflow-cards.js` | workflow view facade |

Structured appointment details render as plain semantic bullets. A separate row uses the ordinary
simple reply component: its concise labels are server-authored, and each reply submits the exact
canonical appointment label through the same turn endpoint. Trusted IDs remain only in the
persisted server choice contract. Compact workflow scalar values and intentionally selected
follow-up answers use the same reply component; detailed entity/reference choices stay as bullets.
The retired rich collection-chip renderer is not used. Semantic bullet collections are appended to
an assistant bubble. For detailed choices, the renderer places that bullet bubble before a separate
question bubble and then appends any intentional simple replies; informational lists remain inside
their ordinary answer bubble. The same ordering projection covers restored legacy messages.

All runtime text uses safe DOM properties/helpers. Card payloads must not contain anchors, buttons,
form controls, embedded chips, executable URLs, or click handlers. The vehicle renderer alone adds
a fixed modal button for a validated vehicle ID; other operations live in typed conversation or
suggestion controls outside the card.
