# Browser widget

This folder is served at `/widget` by `webchat-service`. The host website loads `embed.js`, which
mounts an isolated `<northstar-chat>` web component.

## Direct files and folders

| Path | Responsibility |
| --- | --- |
| [`embed.js`](./embed.js) | Public one-script entry point, API base selection, optional history navigation, `window.NorthstarChat` API |
| [`northstar-chat-widget.js`](./northstar-chat-widget.js) | Custom element, Shadow DOM template, host layout integration, public methods |
| [`webchat.js`](./webchat.js) | Conversation lifecycle, transcript/history state, sending, typed actions, and inline flow sequencing |
| [`webchat.css`](./webchat.css) | Component layout, responsive panel, cards, forms, status, and accessibility presentation |
| [`core/`](./core/README.md) | Transport, context, DOM/form state, deterministic form submission, saved profile, formatting |
| [`views/`](./views/README.md) | Closed message/card/form renderer modules |

## Host integration

```html
<script type="module" data-northstar-chat
  src="http://localhost:4020/widget/embed.js"></script>
```

Public host methods:

```js
NorthstarChat.open()
NorthstarChat.close()
NorthstarChat.newConversation()
NorthstarChat.setContext({ section, vehicleId, controls, entities })
NorthstarChat.onNavigate(handler)
```

## Safety rules

- Do not put API keys or protected dealership requests in widget code.
- Use `textContent`/safe DOM helpers for runtime content.
- Add rich output through a closed view type, not model HTML.
- Keep private booking proof out of chat text, saved profiles, and logs.
- Include new nested asset directories in `pyproject.toml` package data.

## Verification

Run `node --check` across every `.js` file, the Python suite, packaged-asset verification, and the
manual browser matrix described in `docs/LLD.md`.
