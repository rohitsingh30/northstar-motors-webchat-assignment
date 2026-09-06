import { mountNorthstarChat } from "./northstar-chat-widget.js?v=20260906.7";

const script = document.querySelector("script[data-northstar-chat]");
const widgetOrigin = new URL(import.meta.url).origin;
const widget = mountNorthstarChat({
  apiBase: script?.dataset.apiBase || `${widgetOrigin}/api/chat/v1`,
});

if (script?.dataset.navigation === "history") {
  widget.addEventListener("northstar-chat:navigate", (event) => {
    const detail = event.detail || {};
    if (
      detail.type !== "open_vehicle_detail"
      || !/^veh-[0-9]{3}$/.test(detail.vehicleId || "")
      || typeof detail.interactionId !== "string"
      || detail.sameSiteUrl !== `/?vehicle=${detail.vehicleId}`
    ) return;
    event.preventDefault();
    const target = new URL(detail.sameSiteUrl, window.location.href);
    if (target.origin !== window.location.origin) return;
    window.history.pushState(
      {
        vehicleId: detail.vehicleId,
        northstarChatInteractionId: detail.interactionId,
      },
      "",
      `${target.pathname}${target.search}${target.hash}`,
    );
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  });

  window.addEventListener("northstar-chat:vehicle-modal-closed", (event) => {
    widget.handleHostLifecycle("closed", event.detail || {});
  });
  window.addEventListener("northstar-chat:vehicle-modal-failed", (event) => {
    widget.handleHostLifecycle("failed", event.detail || {});
  });
  window.addEventListener("northstar-chat:vehicle-modal-opened-externally", () => {
    widget.handleHostLifecycle("external-opened");
  });
  window.addEventListener("northstar-chat:vehicle-modal-closed-externally", () => {
    widget.handleHostLifecycle("external-closed");
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
