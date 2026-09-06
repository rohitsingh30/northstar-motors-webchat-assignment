# Widget core modules

| File | Responsibility |
| --- | --- |
| `api.js` | credentialed strict JSON requests, safe error normalization, and pending-request aborts |
| `context.js` | bounded validated host/page context |
| `dom.js` | small safe DOM creation boundary |
| `format.js` | `en-GB` GBP formatting |
| `recovery.js` | retry classification and safe client-message-ID reuse/replacement |
| `widget-state.js` | explicit closed/open/busy/unread/error/unavailable/restored lifecycle |
| `workflow-specs.js` | protected-only field specifications per capability |
| `workflow-fields.js` | protected deterministic parsing/validation/masking |
| `workflow-conversation.js` | protected per-conversation session reducer, natural privacy commands/steering boundary, and one-shot payload construction |

Core modules do not resolve public business meaning or select public workflow steps. The protected
reducer recognizes only bounded privacy commands/corrections and likely public steering; `webchat.js`
sends the untouched public text to the AI turn path. Protected values never enter `localStorage`,
`/turns`, or the persisted transcript.
