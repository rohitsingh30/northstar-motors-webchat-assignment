import { createChatApi } from "./core/api.js?v=20260906.1";
import { pageContext } from "./core/context.js?v=20260904.2";
import {
  advanceWorkflow, answerWorkflow, buildWorkflowSubmission, clearWorkflowSession,
  editWorkflowField, extractEarlyContact, fieldDefinition, loadWorkflowSession,
  isPublicSteeringMessage, pauseWorkflow, privateCorrectionField, resumeWorkflow,
  reconcileWorkflowActivation, requestedPrivateCorrectionField,
  saveWorkflowSession, workflowFromView, workflowIsPrivate, workflowLocalDetails,
  workflowPrompt, workflowQuickReplies, workflowReviewDetails,
} from "./core/workflow-conversation.js?v=20260906.1";
import { continuesRenderedTurn, renderMessage } from "./views/message.js?v=20260906.5";
import { initialWidgetState, reduceWidgetState } from "./core/widget-state.js?v=20260904.2";
import { requestFailureKey, turnFailureRecovery } from "./core/recovery.js?v=20260905.3";

const CONVERSATION_KEY = "northstarConversationId";
const EARLY_CONTACT_PREFIX = "northstarEarlyContact:v1:";
const STARTER_PROMPTS = [
  "Show me cars under £35,000", "Book a workshop appointment",
  "I’d like a part-exchange estimate", "Find an existing booking",
];
const CANCEL_WORKFLOW = /^(?:(?:please\s+)?(?:stop|cancel)(?:\s+(?:this|that|it|the))?(?:\s+(?:process|request|booking|workflow))?|i\s+(?:want|need|would like)\s+to\s+(?:stop|cancel)(?:\s+(?:this|the))?(?:\s+(?:process|request|booking|workflow))?|never mind|nevermind|forget it)[.! ]*$/i;
const PAUSE_WORKFLOW = /^(?:(?:please\s+)?pause(?:\s+(?:this|the))?(?:\s+(?:process|request|booking|workflow))?|i\s+(?:want|need|would like)\s+to\s+pause(?:\s+(?:this|the))?(?:\s+(?:process|request|booking|workflow))?|back to chat|return to chat|ask something else|talk about something else)[.! ]*$/i;
const RESUME_WORKFLOW = /^(?:(?:please\s+)?(?:continue|resume|go back to|return to)(?:\s+(?:my|the))?\s+(?:request|booking|test drive|callback|enquiry|details|workflow)|i\s+(?:want|need|would like)\s+to\s+(?:continue|resume)(?:\s+(?:my|the))?\s+(?:request|booking|test drive|callback|enquiry|details|workflow))[.! ]*$/i;
const COLLECTOR_VIEW_TYPES = new Set([
  "secure_input",
]);
const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function safeSessionGet(key) {
  try { return JSON.parse(sessionStorage.getItem(key) || "{}"); } catch { return {}; }
}
function privateStorageKey(id) { return `${EARLY_CONTACT_PREFIX}${id}`; }
export function createWebchat(root = document, options = {}) {
  const api = createChatApi(options.apiBase);
  const $ = (selector) => root.querySelector(selector);
  const launcher = $("#webchat-launcher");
  const panel = $("#webchat-panel");
  const closeButton = $("#webchat-close");
  const newChat = $("#webchat-new");
  const historyPanel = $("#webchat-history-panel");
  const historyList = $("#webchat-history-list");
  const transcript = $("#webchat-transcript");
  const status = $("#webchat-status");
  const progress = $("#webchat-progress");
  const progressBar = $("#webchat-progress-bar");
  const form = $("#webchat-form");
  const starterSuggestions = $("#webchat-starter-suggestions");
  const input = $("#webchat-input");
  const send = $("#webchat-send");
  const unread = $("#webchat-unread");
  const privacyHelper = $("#webchat-privacy-helper");
  const assistantMode = $("#webchat-mode");
  const defaultInputPlaceholder = input.placeholder;

  let conversationId = localStorage.getItem(CONVERSATION_KEY);
  let initialized = false;
  let busy = false;
  let unreadCount = 0;
  let activeWorkflow = null;
  let activeConfirmation = null;
  let latestInteractionIsConfirmation = false;
  let hostModalState = null;
  let lifecycle = initialWidgetState();
  let requestEpoch = 0;
  const retryTurns = new Map();

  function assertCurrentRequest(epoch) {
    if (epoch === requestEpoch) return;
    const error = new Error("Request superseded by a new conversation");
    error.name = "AbortError";
    throw error;
  }
  function requestWasCancelled(error, epoch) {
    return epoch !== requestEpoch || error?.name === "AbortError";
  }

  function syncComposerMode() {
    const privateField = activeWorkflow?.status === "collecting"
      && workflowIsPrivate(activeWorkflow)
      && activeWorkflow.currentField;
    privacyHelper.hidden = !privateField;
    privacyHelper.textContent = privateField
      ? "Private reply: kept in this tab and sent directly to Northstar, not through the AI conversation."
      : "";
    input.placeholder = privateField
      ? `Enter ${fieldDefinition(activeWorkflow.currentField).label} privately`
      : defaultInputPlaceholder;
  }

  function transition(event) {
    lifecycle = reduceWidgetState(lifecycle, event);
    panel.dataset.lifecycle = lifecycle.visibility;
    panel.toggleAttribute("aria-busy", lifecycle.request === "busy");
    unread.hidden = lifecycle.unread === 0;
    unread.textContent = lifecycle.unread ? String(lifecycle.unread) : "";
  }

  function setAssistantMode(mode) {
    const limited = mode === "limited_demo";
    assistantMode.hidden = !limited;
    assistantMode.textContent = limited ? "· Limited local demo" : "";
    panel.dataset.assistantMode = mode || "unknown";
  }

  function scrollToEnd() { transcript.scrollTop = transcript.scrollHeight; }
  function setInteractiveDisabled(disabled) {
    transition({ type: disabled ? "REQUEST_STARTED" : "REQUEST_SUCCEEDED" });
    busy = disabled;
    send.disabled = disabled;
    newChat.disabled = false;
    historyList.querySelectorAll("button").forEach((button) => { button.disabled = disabled; });
    transcript.querySelectorAll("button[data-chat-suggestion],button[data-workflow-choice],button[data-chat-action],button[data-confirm-intent],button[data-confirm-edit]")
      .forEach((button) => { button.disabled = disabled || Boolean(button.closest("[data-superseded]")); });
  }
  function setPending(label) {
    setInteractiveDisabled(true); status.textContent = label; progress.hidden = false; progressBar.removeAttribute("value");
  }
  function clearPending() {
    setInteractiveDisabled(false); status.textContent = ""; progress.hidden = true;
  }
  function setUnread(count) {
    unreadCount = count; transition({ type: "SET_UNREAD", count });
  }
  function setStarterSuggestionsVisible(visible) {
    starterSuggestions.hidden = !visible;
    if (!starterSuggestions.childElementCount) STARTER_PROMPTS.forEach((text) => {
      const button = document.createElement("button");
      Object.assign(button, { type: "button", className: "webchat-suggestion", textContent: text });
      button.dataset.chatSuggestion = text; starterSuggestions.append(button);
    });
  }
  function retireActiveConfirmation(label, { compact = true } = {}) {
    if (!activeConfirmation) return;
    const { element } = activeConfirmation;
    element.querySelector(".webchat-confirmation-replies")?.remove();
    const card = element.querySelector(".webchat-confirmation");
    if (card) {
      card.dataset[compact ? "superseded" : "completed"] = "true";
      if (compact) {
        card.replaceChildren();
        const notice = document.createElement("p");
        notice.className = "webchat-superseded";
        notice.textContent = label;
        card.append(notice);
      }
    }
    activeConfirmation = null;
    latestInteractionIsConfirmation = false;
  }
  function groundedCards(message) {
    return message?.viewType === "grounded_presentation" && Array.isArray(message.view?.cards)
      ? message.view.cards : [];
  }
  function cardData(message, type) {
    return groundedCards(message).find((card) => card?.type === type)?.data || null;
  }
  function messageHasCard(message, type) {
    return message?.viewType === type || Boolean(cardData(message, type));
  }
  function prepareMessageForLocalReview(message) {
    if (!activeWorkflow) return message;
    if (message.viewType === "confirmation") {
      return {
        ...message,
        view: {
          ...message.view,
          localDetails: workflowLocalDetails(activeWorkflow),
          localReviewDetails: workflowReviewDetails(activeWorkflow),
        },
      };
    }
    if (message.viewType !== "grounded_presentation" || !Array.isArray(message.view?.cards)) {
      return message;
    }
    return {
      ...message,
      view: {
        ...message.view,
        cards: message.view.cards.map((card) => card?.type === "confirmation"
          ? {
            ...card,
            data: {
              ...(card.data || {}),
              localDetails: workflowLocalDetails(activeWorkflow),
              localReviewDetails: workflowReviewDetails(activeWorkflow),
            },
          }
          : card),
      },
    };
  }
  function appendMessage(message, { preserveConfirmation = false } = {}) {
    setStarterSuggestionsVisible(false);
    const prepared = prepareMessageForLocalReview(message);
    if (prepared.role === "user") historyPanel.hidden = true;
    const previous = transcript.lastElementChild;
    const continuesTurn = continuesRenderedTurn(previous, prepared);
    const incomingConfirmation = prepared.viewType === "confirmation"
      ? prepared.view : cardData(prepared, "confirmation");
    if (prepared.role === "assistant" && !incomingConfirmation && !preserveConfirmation) {
      retireActiveConfirmation("Superseded — conversation moved on");
    }
    const element = renderMessage({ ...prepared, continuesTurn });
    if (prepared.turnId) element.dataset.turnId = String(prepared.turnId);
    element.dataset.messageRole = prepared.role;
    transcript.append(element);
    const confirmation = incomingConfirmation;
    if (confirmation?.draftId) {
      activeConfirmation = { draftId: confirmation.draftId, kind: confirmation.kind, element };
      latestInteractionIsConfirmation = true;
      if (activeWorkflow) {
        activeWorkflow = { ...activeWorkflow, status: "awaiting_confirmation", draftId: confirmation.draftId };
        saveWorkflowSession(activeWorkflow);
      }
    } else if (prepared.role === "assistant" && !preserveConfirmation) latestInteractionIsConfirmation = false;
    scrollToEnd();
    return element;
  }
  async function appendAssistantSequence(messages, options = {}, epoch = requestEpoch) {
    const assistant = messages.filter((message) => message.role === "assistant");
    for (let index = 0; index < assistant.length; index += 1) {
      if (index) await wait(140);
      assertCurrentRequest(epoch);
      let message = assistant[index];
      const messageOptions = message.viewType === "secure_input"
        && message.view?.editField
        ? { ...options, preserveConfirmation: true }
        : options;
      if (COLLECTOR_VIEW_TYPES.has(message.viewType)) {
        const activation = activateWorkflowForMessage(message);
        if (activation?.promptChanged && activeWorkflow?.status === "collecting") {
          message = { ...message, text: workflowPrompt(activeWorkflow) };
        }
      }
      appendMessage(message, messageOptions);
    }
    return assistant.length;
  }
  function replaceMessages(messages) {
    transcript.replaceChildren(); activeConfirmation = null; latestInteractionIsConfirmation = false;
    activeWorkflow = loadWorkflowSession(conversationId);
    const restoredSecureWorkflow = Boolean(activeWorkflow);
    syncComposerMode();
    if (!messages.length) {
      appendMessage({
        role: "assistant",
        text: "Hi — I’m the Northstar assistant. What can I help you with today?",
      });
    }
    messages.forEach((message) => appendMessage(message));
    if (!activeWorkflow) {
      const activation = [...messages].reverse().find((message) => (
        message.role === "assistant" && COLLECTOR_VIEW_TYPES.has(message.viewType)
      ));
      if (activation) activateWorkflowForMessage(activation);
    }
    setStarterSuggestionsVisible(messages.length === 0);
    if (restoredSecureWorkflow && ["collecting", "ready", "paused"].includes(activeWorkflow?.status)) renderWorkflowPrompt(true);
    historyPanel.hidden = messages.length > 0;
    scrollToEnd();
  }
  function historyDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? "Recent"
      : new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
  }
  async function refreshHistory(epoch = requestEpoch) {
    const result = await api.listConversations();
    assertCurrentRequest(epoch);
    historyList.replaceChildren();
    const current = result.items.find((item) => item.conversationId === conversationId);
    if (
      current
      && current.inProgress === false
      && activeWorkflow?.status === "estimate_ready"
    ) {
      clearWorkflowSession(conversationId);
      activeWorkflow = null;
      syncComposerMode();
    }
    result.items.filter((item) => item.messageCount > 0).forEach((item) => {
      const row = document.createElement("button");
      Object.assign(row, { type: "button", className: "webchat-history-row" });
      row.dataset.conversationId = item.conversationId;
      row.setAttribute("aria-current", String(item.conversationId === conversationId));
      const title = document.createElement("strong"); title.textContent = item.title || "Conversation";
      const meta = document.createElement("span");
      meta.textContent = `${historyDate(item.updatedAt)}${item.inProgress || loadWorkflowSession(item.conversationId) ? " · In progress" : ""}`;
      row.append(title, meta); historyList.append(row);
    });
  }
  async function ensureConversation(epoch = requestEpoch) {
    if (conversationId && initialized) return;
    if (conversationId) {
      try {
        const restored = await api.restoreConversation(conversationId);
        assertCurrentRequest(epoch);
        setAssistantMode(restored.assistantMode);
        initialized = true; replaceMessages(restored.messages); return;
      } catch (error) { if (error.status !== 404) throw error; }
    }
    const created = await api.createConversation(pageContext());
    assertCurrentRequest(epoch);
    setAssistantMode(created.assistantMode);
    conversationId = created.conversationId; localStorage.setItem(CONVERSATION_KEY, conversationId);
    initialized = true; replaceMessages(created.messages);
  }
  async function openPanel() {
    if (panel.open) return;
    const operationEpoch = requestEpoch;
    panel.show(); transition({ type: "OPEN" }); launcher.hidden = true; options.onOpen?.();
    setUnread(0); setPending("Loading your conversation…");
    try {
      await ensureConversation(operationEpoch);
      await refreshHistory(operationEpoch);
      assertCurrentRequest(operationEpoch);
      clearPending(); input.focus();
    } catch (error) {
      if (!requestWasCancelled(error, operationEpoch)) showRequestError(error);
    }
  }
  function finishClose() {
    transition({ type: "CLOSE" });
    launcher.hidden = Boolean(hostModalState);
    if (!hostModalState) launcher.focus();
    options.onClose?.();
  }
  function closePanel() { if (panel.open) panel.close(); else finishClose(); }
  async function startNewConversation() {
    requestEpoch += 1;
    api.cancelPendingRequests();
    const operationEpoch = requestEpoch;
    setPending("Starting a new conversation…");
    try {
      conversationId = null; initialized = false; activeWorkflow = null; activeConfirmation = null;
      retryTurns.clear(); input.value = "";
      syncComposerMode();
      localStorage.removeItem(CONVERSATION_KEY);
      replaceMessages([]);
      await ensureConversation(operationEpoch);
      await refreshHistory(operationEpoch);
      assertCurrentRequest(operationEpoch);
      clearPending(); input.focus();
      return true;
    } catch (error) {
      if (!requestWasCancelled(error, operationEpoch)) showRequestError(error);
      return false;
    }
  }
  function openHostVehicle(action) {
    if (
      action?.type !== "open_vehicle_detail"
      || !/^veh-[0-9]{3}$/.test(action.vehicleId || "")
      || typeof action.interactionId !== "string"
      || action.sameSiteUrl !== `/?vehicle=${action.vehicleId}`
    ) return false;
    hostModalState = {
      interactionId: action.interactionId,
      reopen: Boolean(panel.open),
      draft: input.value,
      scrollTop: transcript.scrollTop,
    };
    transition({ type: "HOST_MODAL_OPENED", interactionId: action.interactionId });
    if (panel.open) panel.close();
    launcher.hidden = true;
    const handled = root.host?.dispatchEvent(new CustomEvent("northstar-chat:navigate", {
      bubbles: true,
      composed: true,
      cancelable: true,
      detail: { ...action },
    })) === false;
    if (!handled) handleHostLifecycle("failed", { interactionId: action.interactionId });
    return handled;
  }
  function executeClientActions(actions = []) {
    actions.forEach(openHostVehicle);
  }
  function openVehicleCard(vehicleId) {
    if (!/^veh-[0-9]{3}$/.test(vehicleId || "")) return false;
    const interactionId = globalThis.crypto?.randomUUID?.()
      || `vehicle-card-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return openHostVehicle({
      type: "open_vehicle_detail",
      interactionId,
      vehicleId,
      sameSiteUrl: `/?vehicle=${vehicleId}`,
    });
  }
  async function handleHostLifecycle(type, detail = {}) {
    if (type === "external-opened") {
      hostModalState = {
        interactionId: "website-modal",
        reopen: false,
        draft: input.value,
        scrollTop: transcript.scrollTop,
        external: true,
      };
      transition({ type: "HOST_MODAL_OPENED", interactionId: "website-modal" });
      if (panel.open) panel.close();
      launcher.hidden = true;
      return true;
    }
    if (type === "external-closed" && hostModalState?.external) {
      const previous = hostModalState;
      hostModalState = null;
      transition({ type: "HOST_MODAL_FINISHED", interactionId: previous.interactionId });
      launcher.hidden = false;
      return true;
    }
    if (!hostModalState || detail.interactionId !== hostModalState.interactionId) return false;
    const previous = hostModalState;
    hostModalState = null;
    transition({
      type: "HOST_MODAL_FINISHED",
      interactionId: detail.interactionId,
      failed: type === "failed",
    });
    input.value = previous.draft;
    if (type === "failed") {
      appendMessage({
        role: "assistant",
        text: "I couldn’t open that vehicle page. You can continue here or ask me to try again.",
      });
    }
    if (previous.reopen) {
      await openPanel();
      transition({ type: "RESTORE_COMPLETED" });
      transcript.scrollTop = previous.scrollTop;
      input.focus();
    } else {
      launcher.hidden = false;
    }
    return true;
  }
  function appendRequestError(error, text, options = {}) {
    clearPending();
    const key = requestFailureKey(error);
    if (transcript.lastElementChild?.dataset?.requestFailureKey === key) {
      return { item: transcript.lastElementChild, appended: false };
    }
    const item = appendMessage({
      role: "assistant",
      text: text || error?.message || "Something went wrong. Please try again.",
    }, options);
    item.dataset.requestFailureKey = key;
    return { item, appended: true };
  }
  function clearRetryActions(item) {
    item.querySelectorAll("[data-retry-id]").forEach((button) => {
      retryTurns.delete(button.dataset.retryId);
      button.remove();
    });
    item.querySelectorAll('[data-chat-action="retry-workflow"]').forEach((button) => button.remove());
  }
  function appendRetryAction(item, action, retryId = null) {
    clearRetryActions(item);
    const button = document.createElement("button");
    Object.assign(button, { type: "button", className: "webchat-primary-action", textContent: "Retry" });
    button.dataset.chatAction = action;
    if (retryId) button.dataset.retryId = retryId;
    item.append(button);
  }
  function showRequestError(error) {
    appendRequestError(error);
  }
  function stashEarlyContact(values) {
    if (!conversationId || !Object.keys(values).length) return;
    const key = privateStorageKey(conversationId);
    sessionStorage.setItem(key, JSON.stringify({ ...safeSessionGet(key), ...values }));
  }
  function consumeEarlyContact() {
    const key = privateStorageKey(conversationId); const values = safeSessionGet(key);
    sessionStorage.removeItem(key); return values;
  }
  function activateWorkflowForMessage(message) {
    if (message.viewType !== "secure_input" || message.view?.secureInputReady !== true) return false;
    let next = workflowFromView(conversationId, message.viewType, message.view || {});
    if (!next) return false;
    const initialField = next.currentField;
    next = reconcileWorkflowActivation(activeWorkflow, next, consumeEarlyContact()).state;
    const editField = message.view?.editField;
    if (editField && fieldDefinition(editField).private) {
      next = editWorkflowField(
        { ...next, draftId: message.view?.draftId || activeConfirmation?.draftId || next.draftId },
        editField,
      );
    }
    if (activeConfirmation) {
      const card = activeConfirmation.element.querySelector(".webchat-confirmation");
      if (card) {
        card.dataset.superseded = "true";
        card.replaceChildren();
        const notice = document.createElement("p");
        notice.className = "webchat-superseded";
        notice.textContent = editField
          ? "Editing — replacement review pending"
          : "Superseded — details changed";
        card.append(notice);
      }
      activeConfirmation = null;
      latestInteractionIsConfirmation = false;
    }
    activeWorkflow = next; saveWorkflowSession(next); syncComposerMode();
    return {
      activated: true,
      promptChanged: initialField !== next.currentField,
      currentField: next.currentField,
    };
  }
  function appendWorkflowChoices(element) {
    const replies = workflowQuickReplies(activeWorkflow); if (!replies.length) return;
    const chips = document.createElement("div"); chips.className = "webchat-suggestions webchat-collector-replies";
    replies.forEach((reply) => {
      const button = document.createElement("button");
      Object.assign(button, { type: "button", className: "webchat-suggestion", textContent: reply.label });
      button.dataset.workflowChoice = activeWorkflow.currentField;
      button.dataset.workflowValue = reply.value ?? "";
      button.dataset.workflowLabel = reply.label;
      chips.append(button);
    });
    element.append(chips);
  }
  function renderWorkflowPrompt(restored = false) {
    if (!activeWorkflow) return;
    if (activeWorkflow.status === "ready") { submitWorkflow(); return; }
    if (activeWorkflow.status === "paused") {
      syncComposerMode();
      appendMessage({
        role: "assistant",
        text: "Your request is paused and its private details remain only in this tab. You can chat normally, or say you want to continue the request.",
      }, { preserveConfirmation: true });
      return;
    }
    syncComposerMode();
    const text = `${restored ? "Welcome back. " : ""}${workflowPrompt(activeWorkflow)}`;
    const element = appendMessage({ role: "assistant", text }, { preserveConfirmation: true });
    appendWorkflowChoices(element);
  }
  function restorePausedWorkflow() {
    const paused = activeWorkflow?.pausedWorkflow;
    if (!paused) {
      clearWorkflowSession(conversationId); activeWorkflow = null; syncComposerMode();
      return false;
    }
    activeWorkflow = resumeWorkflow(paused);
    saveWorkflowSession(activeWorkflow); syncComposerMode();
    renderWorkflowPrompt(true);
    return true;
  }
  async function cancelLocalWorkflow() {
    if (!activeWorkflow) return false;
    const operationEpoch = requestEpoch;
    setPending("Cancelling request…");
    const draftId = activeWorkflow.draftId || activeWorkflow.replacesDraftId;
    if (draftId) {
      try {
        await api.cancelDraft(conversationId, draftId);
        assertCurrentRequest(operationEpoch);
      } catch (error) {
        if (requestWasCancelled(error, operationEpoch)) return false;
      }
    }
    assertCurrentRequest(operationEpoch);
    retireActiveConfirmation("Cancelled", { compact: false });
    appendMessage({ role: "user", text: "Cancel request" }, { preserveConfirmation: true });
    appendMessage({ role: "assistant", text: "Okay — I’ve stopped that request and cleared its private details from this tab." }, { preserveConfirmation: true });
    restorePausedWorkflow();
    clearPending();
    await refreshHistory(operationEpoch).catch(() => null);
    return true;
  }
  async function pauseLocalWorkflow({ supersede = false } = {}) {
    if (!activeWorkflow) return false;
    const operationEpoch = requestEpoch;
    if (supersede && activeConfirmation?.draftId) {
      setPending("Pausing request…");
      try {
        await api.supersedeDraft(conversationId, activeConfirmation.draftId);
        assertCurrentRequest(operationEpoch);
      } catch (error) {
        if (requestWasCancelled(error, operationEpoch)) return false;
        showRequestError(error);
        return false;
      }
      retireActiveConfirmation("Paused — review no longer active");
      activeWorkflow = { ...activeWorkflow, draftId: null };
      clearPending();
    }
    activeWorkflow = pauseWorkflow(activeWorkflow);
    saveWorkflowSession(activeWorkflow); syncComposerMode();
    appendMessage({
      role: "assistant",
      text: "Okay — that request is paused. Its private details remain only in this tab, and you can continue chatting normally. Say you want to continue whenever you’re ready.",
    }, { preserveConfirmation: true });
    input.focus();
    return true;
  }
  async function resumeLocalWorkflow() {
    if (!activeWorkflow) return false;
    activeWorkflow = resumeWorkflow(activeWorkflow);
    saveWorkflowSession(activeWorkflow); syncComposerMode();
    if (activeWorkflow.status === "ready") await submitWorkflow();
    else renderWorkflowPrompt();
    return true;
  }
  async function handleWorkflowAnswer(text, explicitValue) {
    input.value = "";
    if (CANCEL_WORKFLOW.test(text)) {
      return cancelLocalWorkflow();
    }
    if (PAUSE_WORKFLOW.test(text)) {
      return pauseLocalWorkflow();
    }
    const correctionField = requestedPrivateCorrectionField(activeWorkflow, text);
    if (correctionField && fieldDefinition(correctionField).private) {
      activeWorkflow = editWorkflowField(activeWorkflow, correctionField);
      saveWorkflowSession(activeWorkflow); syncComposerMode();
      renderWorkflowPrompt();
      return true;
    }
    if (isPublicSteeringMessage(text)) {
      activeWorkflow = pauseWorkflow(activeWorkflow);
      saveWorkflowSession(activeWorkflow); syncComposerMode();
      return sendText(text);
    }
    const previousWorkflow = activeWorkflow;
    const wasPrivate = workflowIsPrivate(previousWorkflow);
    const result = answerWorkflow(previousWorkflow, text, explicitValue);
    activeWorkflow = result.state; saveWorkflowSession(activeWorkflow);
    if (result.accepted) appendMessage({ role: "user", text: result.display });
    else if (!wasPrivate) appendMessage({ role: "user", text });
    if (!result.accepted) {
      appendWorkflowChoices(appendMessage({ role: "assistant", text: result.error }, { preserveConfirmation: true }));
    } else {
      syncComposerMode();
      if (activeWorkflow.status === "ready") await submitWorkflow();
      else renderWorkflowPrompt();
    }
    return true;
  }
  async function submitWorkflow() {
    const operationEpoch = requestEpoch;
    setPending("Preparing your review…");
    try {
      const submission = buildWorkflowSubmission(activeWorkflow);
      const result = await api[submission.method](conversationId, submission.payload, {
        replacesDraftId: submission.replacesDraftId,
      });
      assertCurrentRequest(operationEpoch);
      const serverMessage = { role: "assistant", text: result.text || "Please review the details below.", viewType: result.viewType, view: result.view };
      if (COLLECTOR_VIEW_TYPES.has(result.viewType)) activateWorkflowForMessage(serverMessage);
      else appendMessage(serverMessage);
      if (result.viewType === "confirmation") {
        activeWorkflow = {
          ...activeWorkflow,
          status: "awaiting_confirmation",
          draftId: result.view.draftId,
          replacesDraftId: null,
        };
        saveWorkflowSession(activeWorkflow);
      } else if (COLLECTOR_VIEW_TYPES.has(result.viewType)) {
        // Collector activation above rendered one conversational prompt.
      } else if (activeWorkflow?.kind === "part_exchange_estimate") {
        activeWorkflow = { ...activeWorkflow, status: "estimate_ready", updatedAt: new Date().toISOString() };
        saveWorkflowSession(activeWorkflow);
        syncComposerMode();
      } else {
        clearWorkflowSession(conversationId); activeWorkflow = null; syncComposerMode();
      }
      syncComposerMode();
      clearPending(); await refreshHistory(operationEpoch).catch(() => null);
    } catch (error) {
      if (requestWasCancelled(error, operationEpoch)) return;
      if (error.code === "SLOT_UNAVAILABLE") {
        releaseUnavailableSelection(null, error.message); return;
      }
      clearPending();
      const field = Object.keys(error.fieldErrors || {})[0];
      if (field && activeWorkflow) {
        const answers = { ...activeWorkflow.answers }; delete answers[field];
        if (["firstName", "lastName"].includes(field)) delete answers.fullName;
        activeWorkflow = advanceWorkflow({ ...activeWorkflow, answers }); saveWorkflowSession(activeWorkflow);
        appendMessage({ role: "assistant", text: error.message || "Please check that answer." }, { preserveConfirmation: true });
        renderWorkflowPrompt();
        return;
      }
      const { item } = appendRequestError(
        error,
        error.message || "I couldn’t prepare that request. Your answers are still here so you can retry.",
        { preserveConfirmation: true },
      );
      if (error.retryable !== false && activeWorkflow?.status === "ready") {
        appendRetryAction(item, "retry-workflow");
      }
    }
  }
  function releaseUnavailableSelection(element, message) {
    pauseProtectedForPublicRecovery(element);
    clearPending();
    appendMessage({
      role: "assistant",
      text: `${message || "That time has just become unavailable."} Tell me what day or time would suit you instead.`,
    }, { preserveConfirmation: true });
    input.focus();
  }
  function pauseProtectedForPublicRecovery(element) {
    element?.querySelector(".webchat-confirmation-replies")?.remove();
    element?.querySelector(".webchat-confirmation")?.setAttribute("data-unavailable", "true");
    if (activeWorkflow) {
      activeWorkflow = {
        ...activeWorkflow,
        status: "awaiting_public_recovery",
        draftId: null,
        updatedAt: new Date().toISOString(),
      };
      saveWorkflowSession(activeWorkflow);
    }
    activeConfirmation = null; latestInteractionIsConfirmation = false;
  }
  function requestConfirmationEdit() {
    if (!activeConfirmation || !latestInteractionIsConfirmation || busy) return false;
    appendMessage({ role: "user", text: "Edit details" }, { preserveConfirmation: true });
    appendMessage({
      role: "assistant",
      text: "What would you like to change? For example, say ‘change my email’, ‘change my phone number’, or ‘change the appointment’.",
    }, { preserveConfirmation: true });
    input.focus();
    return true;
  }
  async function performConfirmation(intent, label) {
    if (!activeConfirmation || !latestInteractionIsConfirmation || busy) return false;
    const operationEpoch = requestEpoch;
    const { element } = activeConfirmation;
    const visibleLabel = label || (intent === "confirm" ? "Confirm" : "Cancel");
    const clientMessageId = activeConfirmation[`${intent}MessageId`] || crypto.randomUUID();
    activeConfirmation[`${intent}MessageId`] = clientMessageId;
    appendMessage({ role: "user", text: visibleLabel }, { preserveConfirmation: true });
    setPending(intent === "confirm" ? "Confirming…" : "Cancelling…");
    try {
      const result = await api.sendTurn(
        conversationId,
        clientMessageId,
        visibleLabel,
        pageContext(),
        { type: intent === "confirm" ? "confirm_active_draft" : "cancel_active_draft" },
      );
      assertCurrentRequest(operationEpoch);
      if (result.status !== "completed") {
        const error = new Error(result.error?.message || "That request could not be completed.");
        error.code = result.error?.code;
        throw error;
      }
      await appendAssistantSequence(
        result.messages || [],
        { preserveConfirmation: true },
        operationEpoch,
      );
      assertCurrentRequest(operationEpoch);
      const completedOperation = (result.messages || []).some(
        (message) => message.viewType === "receipt" || messageHasCard(message, "receipt"),
      );
      if (completedOperation) {
        retireActiveConfirmation("Completed", { compact: false });
        activeConfirmation = null; latestInteractionIsConfirmation = false;
        restorePausedWorkflow();
      } else {
        pauseProtectedForPublicRecovery(element);
      }
      clearPending(); await refreshHistory(operationEpoch).catch(() => null); return true;
    } catch (error) {
      if (requestWasCancelled(error, operationEpoch)) return false;
      if (error.code === "SLOT_UNAVAILABLE") {
        releaseUnavailableSelection(element, error.message);
      } else { clearPending(); showRequestError(error); }
      return false;
    }
  }
  async function sendText(rawText, action, retry = {}) {
    const text = String(rawText || "").trim(); if (!text || busy) return false;
    if (activeWorkflow?.status === "paused") {
      if (CANCEL_WORKFLOW.test(text)) {
        input.value = "";
        return cancelLocalWorkflow();
      }
      if (RESUME_WORKFLOW.test(text)) {
        input.value = "";
        return resumeLocalWorkflow();
      }
      const pausedCorrection = privateCorrectionField(text);
      if (pausedCorrection && fieldDefinition(pausedCorrection).private) {
        input.value = "";
        activeWorkflow = editWorkflowField(activeWorkflow, pausedCorrection);
        saveWorkflowSession(activeWorkflow); syncComposerMode(); renderWorkflowPrompt();
        return true;
      }
    }
    if (activeWorkflow && ["collecting", "ready"].includes(activeWorkflow.status)) {
      return handleWorkflowAnswer(text);
    }
    input.value = "";
    historyPanel.hidden = true;
    const operationEpoch = requestEpoch;
    setPending("Thinking…");
    try {
      await ensureConversation(operationEpoch);
      assertCurrentRequest(operationEpoch);
    } catch (error) {
      if (requestWasCancelled(error, operationEpoch)) return false;
      clearPending();
      if (!input.value) input.value = text;
      showRequestError(error); return false;
    }
    const extracted = extractEarlyContact(text); stashEarlyContact(extracted.values);
    const visibleText = Object.keys(extracted.values).length ? extracted.sanitizedText : text;
    const clientMessageId = retry.clientMessageId || crypto.randomUUID();
    if (retry.appendUser !== false) appendMessage({ role: "user", text: visibleText });
    try {
      const result = await api.sendTurn(conversationId, clientMessageId, visibleText, pageContext(), action);
      assertCurrentRequest(operationEpoch);
      const completedOperation = (result.messages || []).some(
        (message) => message.viewType === "receipt" || messageHasCard(message, "receipt"),
      );
      const count = await appendAssistantSequence(
        result.messages || [],
        completedOperation ? { preserveConfirmation: true } : {},
        operationEpoch,
      );
      assertCurrentRequest(operationEpoch);
      if (result.status !== "completed") {
        const failure = new Error(result.error?.message || "That message could not be completed.");
        failure.code = result.error?.code || "TURN_FAILED";
        failure.retryable = result.error?.retryable === true;
        throw failure;
      }
      if (completedOperation) {
        retireActiveConfirmation("Completed", { compact: false });
        activeConfirmation = null;
        latestInteractionIsConfirmation = false;
        restorePausedWorkflow();
      }
      clearPending();
      await refreshHistory(operationEpoch).catch(() => null);
      assertCurrentRequest(operationEpoch);
      executeClientActions(result.clientActions || []);
      if (!panel.open && !hostModalState && count) setUnread(unreadCount + count);
      return true;
    } catch (error) {
      if (requestWasCancelled(error, operationEpoch)) return false;
      clearPending();
      if (!input.value) input.value = text;
      const canRetry = turnFailureRecovery(error) === "retry";
      const suffix = canRetry
        ? " Your message is still in the composer."
        : " Your message is still in the composer so you can rephrase it.";
      const { item } = appendRequestError(
        error,
        `${error.message || "That message failed."}${suffix}`,
      );
      if (canRetry) {
        const retryId = crypto.randomUUID();
        retryTurns.set(retryId, { clientMessageId, text: visibleText, action, appendUser: false });
        appendRetryAction(item, "retry-turn", retryId);
      } else {
        clearRetryActions(item);
      }
      return false;
    } finally {
      if (operationEpoch === requestEpoch) input.focus();
    }
  }

  launcher.addEventListener("click", openPanel);
  closeButton.addEventListener("click", closePanel);
  panel.addEventListener("close", finishClose);
  panel.addEventListener("cancel", (event) => { event.preventDefault(); closePanel(); });
  newChat.addEventListener("click", startNewConversation);
  historyList.addEventListener("click", async (event) => {
    if (busy) return;
    const row = event.target.closest("[data-conversation-id]");
    if (!row || row.dataset.conversationId === conversationId) return;
    const operationEpoch = requestEpoch;
    setPending("Opening conversation…");
    try {
      const restored = await api.restoreConversation(row.dataset.conversationId);
      assertCurrentRequest(operationEpoch);
      setAssistantMode(restored.assistantMode);
      conversationId = restored.conversationId; initialized = true; localStorage.setItem(CONVERSATION_KEY, conversationId);
      replaceMessages(restored.messages); clearPending();
    } catch (error) {
      if (!requestWasCancelled(error, operationEpoch)) showRequestError(error);
    }
  });
  form.addEventListener("submit", async (event) => { event.preventDefault(); await sendText(input.value); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (!busy) form.requestSubmit(); }
  });
  root.addEventListener("click", async (event) => {
    const vehicleButton = event.target.closest("[data-vehicle-detail]");
    if (vehicleButton && !vehicleButton.disabled) {
      openVehicleCard(vehicleButton.dataset.vehicleDetail);
      return;
    }
    const suggestion = event.target.closest("[data-chat-suggestion]");
    if (suggestion && !suggestion.disabled) {
      let action; try { action = JSON.parse(suggestion.dataset.chatSuggestionAction || "null") || undefined; } catch { action = undefined; }
      await sendText(suggestion.dataset.chatSuggestion, action); return;
    }
    const choice = event.target.closest("[data-workflow-choice]");
    if (choice && !choice.disabled && activeWorkflow) {
      await handleWorkflowAnswer(choice.dataset.workflowLabel, choice.dataset.workflowValue || null); return;
    }
    const confirmation = event.target.closest("[data-confirm-intent]");
    if (confirmation && !confirmation.disabled) { await performConfirmation(confirmation.dataset.confirmIntent, confirmation.textContent); return; }
    const confirmationEdit = event.target.closest("[data-confirm-edit]");
    if (confirmationEdit && !confirmationEdit.disabled) { requestConfirmationEdit(); return; }
    const actionButton = event.target.closest("[data-chat-action]");
    if (actionButton?.dataset.chatAction === "confirm") { await performConfirmation("confirm", actionButton.textContent); return; }
    if (actionButton?.dataset.chatAction === "cancel") { await performConfirmation("cancel", actionButton.textContent); return; }
    if (actionButton?.dataset.chatAction === "retry-turn") {
      const retry = retryTurns.get(actionButton.dataset.retryId);
      if (retry) { retryTurns.delete(actionButton.dataset.retryId); await sendText(retry.text, retry.action, retry); }
    }
    if (actionButton?.dataset.chatAction === "retry-workflow" && activeWorkflow?.status === "ready") {
      await submitWorkflow();
    }
  });

  return {
    open: openPanel,
    close: closePanel,
    newConversation: startNewConversation,
    handleHostLifecycle,
  };
}
