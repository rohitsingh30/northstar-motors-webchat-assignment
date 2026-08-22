import { mountNorthstarChat } from "./northstar-chat-widget.js";

const script = document.querySelector("script[data-northstar-chat]");
const widgetOrigin = new URL(import.meta.url).origin;
const widget = mountNorthstarChat({
  apiBase: script?.dataset.apiBase || `${widgetOrigin}/api/chat/v1`,
});

if (script?.dataset.navigation === "history") {
  widget.addEventListener("northstar-chat:navigate", (event) => {
    if (!event.detail?.href) return;
    event.preventDefault();
    const target = new URL(event.detail.href, window.location.href);
    window.history.pushState(
      { vehicleId: event.detail.vehicleId || null },
      "",
      `${target.pathname}${target.search}${target.hash}`,
    );
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  });
}

// A deliberately small host API keeps integrations independent of widget internals.
window.NorthstarChat = {
  open: () => widget.open(),
  close: () => widget.close(),
  newConversation: () => widget.startNewConversation(),
  setContext: (context) => widget.setContext(context),
  onNavigate: (handler) => {
    const listener = (event) => {
      if (handler(event.detail) === true) event.preventDefault();
    };
    widget.addEventListener("northstar-chat:navigate", listener);
    return () => widget.removeEventListener("northstar-chat:navigate", listener);
  },
};
