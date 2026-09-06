# Browser widget

The service exposes `/widget/embed.js`, which mounts the Shadow-DOM `<northstar-chat>` component.
The widget is a render/controller client, not the public conversational agent.

| Path | Responsibility |
| --- | --- |
| `embed.js` | one-script host entry, context bridge, protected vehicle-modal lifecycle |
| `northstar-chat-widget.js` | custom element, shell, and desktop host-width reservation |
| `webchat.js` | conversation/history/request epoch, pacing, protected capture and actions |
| `webchat.css` | full-height desktop right rail, mobile full screen, bubbles/cards/accessibility |
| `core/` | transport, host context, state machine, protected workflow session state |
| `views/` | safe message, collection, card, receipt and external-chip renderers |

Every public typed reply and external chip goes to `/turns`. Only a server `secure_input`
activation starts protected capture. Protected values use versioned per-conversation
`sessionStorage`; they never use `localStorage` or ordinary messages. Same-origin JavaScript can
read session storage, so only trusted scripts may share the widget origin. Capture happens through
the normal chat composer in a field-specific private-input mode, not through a separate form.
Invalid private input is kept out of the transcript; accepted input receives a field-specific
acknowledgement before the composer advances to the next required field.

Protected capture does not lock the conversation. Natural cancel, pause, resume, and private-field
corrections are handled locally. A recognizable ordinary question, public correction, or new
request pauses protected capture and goes through `/turns`; collected private values remain only in
the per-conversation tab state. Confirmation cards are visual-only: natural standalone decisions
are resolved by the server, while corrections or topic changes supersede the review safely.

Card payloads contain no links, actions, inputs, embedded chips, or handlers. Vehicle preview cards
add a fixed “View vehicle” control in the trusted renderer; it derives the same-site modal target
only from a validated vehicle ID. Navigation requested through conversation still requires the
trusted server `clientAction` and protected reconfirmation.

Broad appointment results are retained only as trusted context. After the AI narrows them and the
customer selects one, the secure-input message may show that single appointment as a visual-only
summary; the widget never parses a public date or chooses a slot.

On desktop, opening the 350px right rail reserves the same width from the host body so site content
remains visible beside it; closing the widget or hiding it for the host vehicle modal releases that
space. Recent chats is part of the empty new-conversation surface rather than a header toggle and is
hidden by the first customer message. New chat remains available during processing: it aborts active
browser requests and advances the request epoch before resetting, preventing any late result from
mutating the replacement conversation.

The widget never renders orchestration provenance as a standalone card. Substitution disclosure is
already ordinary persisted assistant copy when it reaches the browser. Consecutive messages expose
their persisted `turnId`; the renderer uses same-role/same-turn identity for compact intra-turn
spacing rather than guessing from workflow or message-purpose labels. Every assistant prose node
receives the bubble class directly, so a question remains a bubble whether it appears before or
after a card; DOM child position is not a presentation owner.

Public host API:

```js
NorthstarChat.open()
NorthstarChat.close()
NorthstarChat.newConversation()
NorthstarChat.setContext({ section, vehicleId, controls, entities })
NorthstarChat.onNavigate(handler)
```
