// This module owns the complete browser widget; host applications only load embed.js.
import { setPageContext } from "./core/context.js";
import { createWebchat } from "./webchat.js";

const HOST_STYLE_ID = "northstar-chat-host-style";

function installHostStyle() {
  if (document.getElementById(HOST_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = HOST_STYLE_ID;
  style.textContent = `
    @media (min-width: 800px) {
      html[data-northstar-chat-open] body {
        margin-right: var(--northstar-chat-panel-width, 380px);
      }
    }
    @media (prefers-reduced-motion: no-preference) and (min-width: 800px) {
      body { transition: margin-right 180ms ease; }
    }
  `;
  document.head.append(style);
}

const template = document.createElement("template");
template.innerHTML = `
  <link rel="stylesheet" href="${new URL("./webchat.css", import.meta.url).href}" />
  <button id="webchat-launcher" class="webchat-launcher" type="button"
    aria-haspopup="dialog" aria-controls="webchat-panel">
    <span>Chat with Northstar</span>
    <span id="webchat-unread" class="webchat-unread" aria-label="unread messages" hidden></span>
  </button>
  <dialog id="webchat-panel" class="webchat-panel" aria-labelledby="webchat-title">
    <div class="webchat-layout">
      <header class="webchat-header">
        <div>
          <p class="eyebrow">Northstar assistant</p>
          <h2 id="webchat-title">How can we help?</h2>
        </div>
        <div class="webchat-header-actions">
          <button id="webchat-new" type="button">＋ New chat</button>
          <button id="webchat-close" type="button" aria-label="Close chat">×</button>
        </div>
      </header>
      <section id="webchat-history-panel" class="webchat-history" aria-label="Conversation history">
        <div class="webchat-history-heading">
          <div>
            <h3>Recent chats</h3>
          </div>
        </div>
        <div id="webchat-history-list" class="webchat-history-list"></div>
      </section>
      <ol id="webchat-transcript" class="webchat-transcript" role="log" aria-label="Conversation"
        aria-live="polite" aria-relevant="additions text"></ol>
      <div id="webchat-progress" class="webchat-progress" hidden>
        <div class="webchat-progress-copy">
          <span class="webchat-thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>
          <p id="webchat-status" class="webchat-status" role="status" aria-live="polite"></p>
        </div>
        <progress id="webchat-progress-bar" max="100" aria-label="Request in progress"></progress>
      </div>
      <form id="webchat-form" class="webchat-form">
        <label for="webchat-input">Your message</label>
        <div id="webchat-starter-suggestions" class="webchat-suggestions webchat-starter-suggestions"
          aria-label="Suggested questions" hidden></div>
        <div class="webchat-form-input">
          <textarea id="webchat-input" rows="2" maxlength="4000"
            placeholder="Ask about vehicles, offers, locations, or servicing" required></textarea>
          <button id="webchat-send" type="submit">Send</button>
        </div>
      </form>
    </div>
  </dialog>
`;

export class NorthstarChatWidget extends HTMLElement {
  connectedCallback() {
    if (this.controller) return;
    installHostStyle();
    const root = this.attachShadow({ mode: "open" });
    root.append(template.content.cloneNode(true));
    this.controller = createWebchat(root, {
      apiBase: this.getAttribute("api-base") || undefined,
      onOpen: () => document.documentElement.setAttribute("data-northstar-chat-open", ""),
      onClose: () => document.documentElement.removeAttribute("data-northstar-chat-open"),
    });
  }

  disconnectedCallback() {
    document.documentElement.removeAttribute("data-northstar-chat-open");
  }

  open() {
    return this.controller?.open();
  }

  close() {
    return this.controller?.close();
  }

  startNewConversation() {
    return this.controller?.newConversation();
  }

  setContext(context = {}) {
    setPageContext(context);
  }
}

if (!customElements.get("northstar-chat")) {
  customElements.define("northstar-chat", NorthstarChatWidget);
}

export function mountNorthstarChat(options = {}) {
  const widget = document.createElement("northstar-chat");
  if (options.apiBase) widget.setAttribute("api-base", options.apiBase);
  (options.target || document.body).append(widget);
  return widget;
}
