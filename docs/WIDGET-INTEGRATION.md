# Northstar chat widget integration

The customer widget is a style-isolated Web Component. The integrating page does not need to copy
chat markup, know internal element IDs, or handle conversation persistence.

On desktop, opening the widget creates a full-height right panel and reserves 380px for it so the
host page remains usable. At widths below 800px it becomes a full-screen panel without changing
the host layout. Integrators may override `--northstar-chat-panel-width` on the root element.

## Installation

The `webchat-service` serves the complete widget bundle. Add one module script before the closing
`body` tag:

```html
<script
  type="module"
  src="http://localhost:4020/widget/embed.js"
  data-northstar-chat
  data-navigation="history"
></script>
```

The host application does not host widget files, import widget modules, copy markup, dispatch
events, or change its vehicle-detail behaviour. By default, `embed.js` uses its own service origin
for the API. `data-api-base` is available only when a deployment routes the API elsewhere. The
dealership key and OpenAI key are never browser configuration.

`data-navigation="history"` is optional. It handles safe widget links using `pushState` plus a
standard `popstate` notification, allowing an existing client-side router to open its normal view
without reloading or closing the chat. Sites with a different router can omit the attribute and
register a callback instead:

```js
const unsubscribe = NorthstarChat.onNavigate(({ href, vehicleId }) => {
  myRouter.openVehicle(vehicleId, href);
  return true; // the host handled this navigation
});
```

## Host integration API

The auto-mount script exposes a deliberately small API:

```js
NorthstarChat.open();
NorthstarChat.close();
NorthstarChat.newConversation();
NorthstarChat.setContext({ section: "vehicles", vehicleId: "veh-001" });
```

The widget automatically captures a bounded semantic snapshot of the current page: local path,
safe section slug, title, primary heading, meta description, visible text from the active page
section, current visible search/select choices, and visible text from an open page dialog. It sends
text only—never HTML, scripts, hidden content, passwords, or contact-form values. The backend keeps
the snapshot from where the conversation started as well as the current snapshot for each turn.

This automatic snapshot works on future pages without adding page-specific filter schemas. The
`setContext` API remains available when a host router has a stable selected vehicle ID or a more
accurate section slug. Vehicle IDs must match `veh-000`; section names must be safe lowercase
slugs. Page snapshots are model data, never instructions, and dynamic dealership facts are still
rechecked through backend tools.

Vehicle-card links retain a safe `?vehicle={vehicleId}` href and also emit a cancellable,
composed `northstar-chat:navigate` event. A single-page host can prevent that event's default and
open its own vehicle-detail view without reloading; otherwise the normal link remains functional.

For module-based host applications, import `mountNorthstarChat` from the service's
`/widget/northstar-chat-widget.js`. It returns the `<northstar-chat>` element with the same
lifecycle and context methods.

## Backend deployment contract

The local Compose deployment publishes the standalone webchat on port 4020 and permits only the
configured `WEBCHAT_ALLOWED_ORIGIN` with credentials. For production, a same-origin `/api/chat/`
edge route is recommended; set `data-api-base="/api/chat/v1"` in that deployment. In either mode,
do not expose `NORTHSTAR_API_KEY` or `OPENAI_API_KEY` to the widget.

## Contract ownership

`dealership-platform/openapi.json` is authoritative for platform endpoints, request/response
fields, IDs, status values, and authentication requirements. The product brief and PRD remain
authoritative for conversation behaviour, explicit confirmation, privacy, accessibility, and how
platform outcomes are described. The model cannot override either contract.
