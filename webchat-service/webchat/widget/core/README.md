# Widget core modules

Core modules contain browser infrastructure shared by the controller and renderers.

## Files

| File | Responsibility |
| --- | --- |
| [`api.js`](./api.js) | Credentialed JSON requests for every public webchat endpoint; normalize problem/validation errors |
| [`context.js`](./context.js) | Validate host context and collect bounded URL/page text/dialog/control/entity snapshots |
| [`dom.js`](./dom.js) | Create elements with text content at one small safe boundary |
| [`form-profile.js`](./form-profile.js) | Save/prefill ordinary reusable form values; skip internal and private lookup fields |
| [`format.js`](./format.js) | Format integer pence as `en-GB` GBP or return `null` for unknown values |
| [`recovery.js`](./recovery.js) | Classify booking failures and preserve or replace client-message IDs safely during turn retry |

Core modules must not contain feature-card layout. Put new card/form rendering in `views`, and keep
controller event sequencing in `webchat.js`.
