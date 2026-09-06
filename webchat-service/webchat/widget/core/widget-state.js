export const WIDGET_STATE_VERSION = 1;

export function initialWidgetState() {
  return {
    version: WIDGET_STATE_VERSION,
    visibility: "closed",
    request: "idle",
    availability: "available",
    unread: 0,
    hostInteractionId: null,
    restoreToOpen: false,
    lastError: null,
  };
}

export function reduceWidgetState(current, event) {
  if (!current || current.version !== WIDGET_STATE_VERSION) throw new Error("Unsupported widget state");
  const state = { ...current };
  switch (event?.type) {
    case "OPEN":
      if (state.availability === "unavailable") return state;
      state.visibility = "open";
      state.unread = 0;
      state.lastError = null;
      return state;
    case "CLOSE":
      state.visibility = state.hostInteractionId ? "hidden_for_host" : "closed";
      return state;
    case "REQUEST_STARTED":
      state.request = "busy";
      state.lastError = null;
      return state;
    case "REQUEST_SUCCEEDED":
      state.request = "idle";
      return state;
    case "REQUEST_FAILED":
      state.request = "idle";
      state.lastError = String(event.code || "request_failed");
      return state;
    case "MESSAGE_RECEIVED":
      if (state.visibility !== "open") state.unread += Math.max(0, Number(event.count) || 0);
      return state;
    case "SET_UNREAD":
      state.unread = Math.max(0, Number(event.count) || 0);
      return state;
    case "HOST_MODAL_OPENED":
      if (!event.interactionId) throw new Error("Host modal requires an interaction ID");
      state.restoreToOpen = state.visibility === "open";
      state.visibility = "hidden_for_host";
      state.hostInteractionId = String(event.interactionId);
      return state;
    case "HOST_MODAL_FINISHED":
      if (!state.hostInteractionId || event.interactionId !== state.hostInteractionId) return state;
      state.visibility = state.restoreToOpen ? "restored" : "closed";
      state.hostInteractionId = null;
      state.restoreToOpen = false;
      state.lastError = event.failed ? "host_navigation_failed" : null;
      return state;
    case "RESTORE_COMPLETED":
      if (state.visibility === "restored") state.visibility = "open";
      return state;
    case "UNAVAILABLE":
      state.availability = "unavailable";
      state.request = "idle";
      state.lastError = String(event.code || "unavailable");
      return state;
    case "AVAILABLE":
      state.availability = "available";
      state.lastError = null;
      return state;
    default:
      throw new Error(`Unsupported widget event: ${event?.type || "missing"}`);
  }
}
