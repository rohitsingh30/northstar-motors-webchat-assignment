// Controller state is private to the widget and independent of the host application.
import { createChatApi } from "./core/api.js";
import { pageContext } from "./core/context.js";
import { createFormProfile } from "./core/form-profile.js";
import { createFormSubmitHandler } from "./core/form-submit.js";
import { applyRetainedDetails, showFieldErrors } from "./core/form-state.js";
import { bookingRecoveryKind, retryTurnState } from "./core/recovery.js";
import {
  bindBookedTestDriveAction,
  inlineBookedTestDrive,
  inlineOfferEnquiryForm,
  receiptCard,
  renderMessage,
  renderWorkflowCard,
  setBookedTestDriveActionState,
  setOfferEnquiryActionState,
  setWorkshopFlowContent,
  testDriveDetailsForm,
  testDriveSlotPicker,
  workshopDetailsForm,
  workshopSlotPicker,
} from "./views/message.js";

const CONVERSATION_KEY = "northstarConversationId";
const OPEN_KEY = "northstarChatOpen";
const STARTER_PROMPTS = [
  { label: "Show me cars under £35,000.", text: "Show me cars under £35,000." },
  { label: "I need a petrol automatic SUV with low mileage.", text: "I need a petrol automatic SUV with low mileage." },
  { label: "Compare the BMW 1 Series and BMW 3 Series.", text: "Compare the BMW 1 Series and BMW 3 Series." },
  { label: "I want to book a workshop appointment.", text: "I want to book a workshop appointment." },
  { label: "What new-car offers are currently published?", text: "What new-car offers are currently published?" },
  { label: "Where is the Stockport dealership?", text: "Where is the Stockport dealership?" },
];

export function createWebchat(root = document, options = {}) {
  const api = createChatApi(options.apiBase);
  const launcher = root.querySelector("#webchat-launcher");
  const panel = root.querySelector("#webchat-panel");
  const close = root.querySelector("#webchat-close");
  const newChat = root.querySelector("#webchat-new");
  const historyPanel = root.querySelector("#webchat-history-panel");
  const historyList = root.querySelector("#webchat-history-list");
  const transcript = root.querySelector("#webchat-transcript");
  const status = root.querySelector("#webchat-status");
  const progress = root.querySelector("#webchat-progress");
  const progressBar = root.querySelector("#webchat-progress-bar");
  const form = root.querySelector("#webchat-form");
  const starterSuggestions = root.querySelector("#webchat-starter-suggestions");
  const input = root.querySelector("#webchat-input");
  const send = root.querySelector("#webchat-send");
  const unread = root.querySelector("#webchat-unread");
  let conversationId = localStorage.getItem(CONVERSATION_KEY);
  let initialized = false;
  let pendingClientId = null;
  let unreadCount = 0;
  const retryTurns = new Map();
  const formProfile = createFormProfile(transcript);

  function setStarterSuggestionsVisible(visible) {
    starterSuggestions.hidden = !visible;
    if (!starterSuggestions.childElementCount) {
      STARTER_PROMPTS.forEach((suggestion) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "webchat-suggestion";
        button.textContent = suggestion.label;
        button.dataset.chatSuggestion = suggestion.text;
        starterSuggestions.append(button);
      });
    }
    starterSuggestions.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  }

  function hasMessages() {
    return Boolean(transcript.querySelector(".webchat-message"));
  }

  function clearPending() {
    panel.removeAttribute("aria-busy");
    progress.hidden = true;
    status.textContent = "";
    progressBar.removeAttribute("value");
  }

  function setPending(label) {
    panel.setAttribute("aria-busy", "true");
    status.textContent = label;
    progress.hidden = false;
    progressBar.removeAttribute("value");
  }

  function historyDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "Recent";
    return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  async function refreshHistory() {
    const result = await api.listConversations();
    // A conversation is created when the panel opens, before the customer has
    // sent anything. Keep that placeholder out of Recent chats.
    const conversations = result.items.filter((item) => item.messageCount > 0);
    historyList.replaceChildren();
    if (!conversations.length) {
      historyPanel.hidden = true;
      return;
    }
    // The chooser is only a pre-chat surface. Once the active transcript has a
    // message, history refreshes must not reopen it underneath the conversation.
    historyPanel.hidden = hasMessages();
    conversations.forEach((item) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "webchat-history-row";
      row.dataset.conversationId = item.conversationId;
      row.setAttribute("aria-current", String(item.conversationId === conversationId));
      const title = document.createElement("strong");
      title.textContent = item.title || "New conversation";
      const meta = document.createElement("span");
      meta.textContent = `${historyDate(item.updatedAt)} · ${item.messageCount} ${item.messageCount === 1 ? "message" : "messages"}`;
      row.append(title, meta);
      historyList.append(row);
    });
  }

  async function showHistory() {
    historyPanel.hidden = false;
    setPending("Loading conversations…");
    try {
      await refreshHistory();
      clearPending();
      historyList.querySelector("button")?.focus();
    } catch (error) {
      showRequestError(error);
    }
  }

  function hideHistory() {
    // Recent chats stay in the layout; switching a conversation only replaces the transcript.
  }

  function showBookedTestDrive(flow) {
    if (!flow?.booking) return;
    flow.hidden = false;
    flow.replaceChildren(inlineBookedTestDrive(flow.booking));
    setBookedTestDriveActionState(flow, true);
  }

  function setBookedTestDriveAction(flow, receipt) {
    const action = flow?.launchButton;
    const bookingFlow = action
      ?.closest(".webchat-vehicle-card")
      ?.querySelector("[data-booking-flow]");
    bindBookedTestDriveAction(bookingFlow, action, {
      reference: receipt.reference,
      slotLabel: receipt.slotLabel || flow?.selectedSlot?.slotLabel,
      startsAt: receipt.startsAt,
      dealershipName: receipt.dealershipName || flow?.selectedSlot?.dealershipName,
      vehicleLabel: receipt.vehicleLabel || action?.dataset.vehicleLabel,
      vehicleId: receipt.vehicleId || action?.dataset.vehicleId,
    });
  }

  function restoreBookedTestDriveActions(messages) {
    const actions = [...transcript.querySelectorAll('[data-chat-action="test-drive"]')];
    messages
      .filter((message) => message.viewType === "receipt" && message.view?.kind === "test_drive")
      .forEach((message) => {
        const action = actions.find(
          (candidate) => candidate.dataset.vehicleId === message.view.vehicleId,
        );
        const flow = action
          ?.closest(".webchat-vehicle-card")
          ?.querySelector("[data-booking-flow]");
        bindBookedTestDriveAction(flow, action, message.view);
      });
  }

  function focusOfferEnquiry(flow) {
    flow?.querySelector("select, textarea, input, button, a, [tabindex]")?.focus();
  }

  function replaceMessages(messages) {
    transcript.replaceChildren(...messages.map(renderMessage));
    restoreBookedTestDriveActions(messages);
    setStarterSuggestionsVisible(messages.length === 0);
    historyPanel.hidden = messages.length > 0;
    transcript.lastElementChild?.scrollIntoView({ block: "nearest" });
  }

  function appendMessage(message) {
    setStarterSuggestionsVisible(false);
    transcript.append(renderMessage(message));
    if (message.role === "user") historyPanel.hidden = true;
    transcript.lastElementChild?.scrollIntoView({ block: "nearest" });
  }

  function appendTestDriveFlowMessage(button, view) {
    const message = document.createElement("li");
    message.className = "webchat-message webchat-message--assistant webchat-message--rich webchat-test-drive-flow-message";
    const flow = document.createElement("div");
    flow.className = "webchat-booking-flow webchat-test-drive-flow-standalone";
    flow.dataset.bookingFlow = "true";
    flow.originalControls = button.getAttribute("aria-controls") || "";
    flow.id = `${flow.originalControls || "webchat-test-drive-flow"}-message`;
    flow.testDriveOptions = view;
    flow.launchButton = button;
    flow.vehicleLabel = button.dataset.vehicleLabel;
    flow.replaceChildren(testDriveSlotPicker(view, { inline: true }));
    message.append(flow);
    transcript.append(message);
    button.setAttribute("aria-controls", flow.id);
    button.activeBookingFlow = flow;
    setActiveBookingFlowVisible(flow, true, "test-drive");
    return flow;
  }

  function appendWorkshopFlowMessage(button, view) {
    const message = document.createElement("li");
    message.className = "webchat-message webchat-message--assistant webchat-message--rich webchat-workshop-flow-message";
    const flow = document.createElement("div");
    flow.className = "webchat-booking-flow webchat-workshop-flow-standalone";
    flow.dataset.workshopFlow = "true";
    flow.workshopOptions = view;
    flow.serviceName = button.dataset.serviceName;
    flow.launchButton = button;
    setWorkshopFlowContent(flow, workshopSlotPicker(view, {
      inline: true,
      showContext: true,
      serviceName: flow.serviceName,
    }));
    message.append(flow);
    transcript.append(message);
    button.activeBookingFlow = flow;
    setActiveBookingFlowVisible(flow, true, "workshop");
    return flow;
  }

  function setActiveBookingFlowVisible(flow, visible, kind) {
    const messageClass = kind === "workshop"
      ? ".webchat-workshop-flow-message"
      : ".webchat-test-drive-flow-message";
    const message = flow?.closest(messageClass);
    const launch = flow?.launchButton;
    if (message) message.hidden = !visible;
    if (!launch) return;
    launch.disabled = false;
    launch.textContent = visible
      ? kind === "workshop" ? "Hide workshop booking" : "Hide test-drive booking"
      : kind === "workshop" ? "View workshop booking" : "View test-drive booking";
    launch.setAttribute("aria-expanded", String(visible));
    if (visible) message?.scrollIntoView({ block: "nearest" });
  }

  function setOfferEnquiryVisible(
    flow,
    visible,
    hasEnquiry = Boolean(flow?.enquiryReceipt || flow?.draftId || flow?.childElementCount),
  ) {
    const message = flow?.closest(".webchat-offer-enquiry-flow-message");
    if (message) message.hidden = !visible;
    if (flow) flow.hidden = false;
    setOfferEnquiryActionState(flow, visible, hasEnquiry);
    if (visible) {
      message?.scrollIntoView({ block: "nearest" });
      focusOfferEnquiry(flow);
    }
  }

  function appendOfferEnquiryFlowMessage(button, view) {
    const message = document.createElement("li");
    message.className = "webchat-message webchat-message--assistant webchat-message--rich webchat-offer-enquiry-flow-message";
    const flow = document.createElement("div");
    flow.className = "webchat-booking-flow webchat-offer-enquiry-flow";
    flow.dataset.offerEnquiryFlow = "true";
    const contextualView = {
      ...view,
      contextLabel: button.dataset.offerLabel || "",
    };
    flow.draftId = view.draftId;
    flow.launchButton = button;
    flow.replaceChildren(inlineOfferEnquiryForm(contextualView));
    message.append(flow);
    transcript.append(message);
    button.offerEnquiryFlow = flow;
    setOfferEnquiryVisible(flow, true);
    return flow;
  }

  function showRequestError(error, retryHint = "") {
    clearPending();
    const message = error?.message || "Something went wrong.";
    appendMessage({ role: "assistant", text: `${message}${retryHint ? ` ${retryHint}` : ""}` });
  }

  function setUnread(count) {
    unreadCount = count;
    unread.hidden = count === 0;
    unread.textContent = count ? String(count) : "";
  }


  function recoveryNotice(message) {
    const notice = document.createElement("p");
    notice.className = "webchat-form-error";
    notice.setAttribute("role", "alert");
    notice.textContent = message;
    return notice;
  }

  function showBookingRecovery(button, error) {
    const inlineBooking = button.dataset.inlineBooking;
    const kind = bookingRecoveryKind(error, inlineBooking);
    if (kind === "none") return false;
    const workshop = inlineBooking === "workshop";
    const flow = button.closest(workshop ? "[data-workshop-flow]" : "[data-booking-flow]");
    if (!flow) return false;

    if (kind === "fields" && flow.selectedSlot) {
      const detailsForm = workshop
        ? workshopDetailsForm(flow.selectedSlot)
        : testDriveDetailsForm({ ...flow.selectedSlot, vehicleLabel: flow.vehicleLabel });
      applyRetainedDetails(detailsForm, flow.contactDetails);
      if (workshop) setWorkshopFlowContent(flow, detailsForm);
      else flow.replaceChildren(detailsForm);
      showFieldErrors(detailsForm, error.fieldErrors);
      clearPending();
      return true;
    }

    flow.draftId = null;
    flow.selectedSlot = null;
    if (kind === "slot") {
      const view = error.recovery?.view || { version: 1, items: [] };
      if (workshop) flow.workshopOptions = view;
      else flow.testDriveOptions = view;
      const picker = workshop
        ? workshopSlotPicker(view, { inline: true })
        : testDriveSlotPicker(view, { inline: true });
      if (workshop) {
        setWorkshopFlowContent(flow, [recoveryNotice(error.message), picker]);
      } else {
        flow.replaceChildren(recoveryNotice(error.message), picker);
      }
      if (!(view.items || []).length) {
        const closeFlow = document.createElement("button");
        closeFlow.type = "button";
        closeFlow.className = "webchat-secondary-action";
        closeFlow.textContent = workshop ? "Choose another service" : "Choose another vehicle";
        closeFlow.dataset.chatAction = workshop
          ? "cancel-inline-workshop"
          : "cancel-inline-test-drive";
        flow.append(closeFlow);
      }
      clearPending();
      return true;
    }

    const recovery = document.createElement("section");
    recovery.className = "webchat-inline-confirmation";
    recovery.append(recoveryNotice(error.message));
    const suggestions = document.createElement("div");
    suggestions.className = "webchat-suggestions";
    (error.recovery?.suggestions || []).forEach((suggestion) => {
      const choice = document.createElement("button");
      choice.type = "button";
      choice.className = "webchat-suggestion";
      choice.textContent = suggestion.label;
      choice.dataset.chatSuggestion = suggestion.text;
      if (suggestion.action) {
        choice.dataset.chatSuggestionAction = JSON.stringify(suggestion.action);
      }
      suggestions.append(choice);
    });
    const closeFlow = document.createElement("button");
    closeFlow.type = "button";
    closeFlow.className = "webchat-secondary-action";
    closeFlow.textContent = "Close booking";
    closeFlow.dataset.chatAction = "cancel-inline-test-drive";
    recovery.append(suggestions, closeFlow);
    if (workshop) setWorkshopFlowContent(flow, recovery);
    else flow.replaceChildren(recovery);
    clearPending();
    return true;
  }

  function showTurnRetry(error, turn, definitiveFailure) {
    clearPending();
    const retryId = crypto.randomUUID();
    retryTurns.set(retryId, retryTurnState(turn, definitiveFailure));
    const item = renderMessage({
      role: "assistant",
      text: error?.message || "That message could not be completed.",
    });
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "webchat-primary-action";
    retry.textContent = "Retry";
    retry.dataset.chatAction = "retry-turn";
    retry.dataset.retryId = retryId;
    item.append(retry);
    setStarterSuggestionsVisible(false);
    transcript.append(item);
    transcript.lastElementChild?.scrollIntoView({ block: "nearest" });
  }

  async function cancelInlineTestDrive(button) {
    const flow = button.closest("[data-booking-flow]");
    if (!flow) return;
    const draftId = button.dataset.draftId || flow.draftId;
    if (draftId) await api.cancelDraft(conversationId, draftId);
    flow.replaceChildren();
    flow.hidden = true;
    flow.testDriveOptions = null;
    flow.selectedSlot = null;
    flow.contactDetails = null;
    flow.draftId = null;
    const message = flow.closest(".webchat-test-drive-flow-message");
    const launch = flow.launchButton || flow.closest(".webchat-vehicle-card")
      ?.querySelector('[data-chat-action="test-drive"]');
    message?.remove();
    if (launch) {
      launch.activeBookingFlow = null;
      launch.disabled = false;
      launch.textContent = "Book test drive";
      launch.setAttribute("aria-expanded", "false");
      if (flow.originalControls) launch.setAttribute("aria-controls", flow.originalControls);
      launch.focus();
    }
  }

  function closeInlineWorkshopFlow(flow) {
    if (!flow) return;
    const message = flow.closest(".webchat-workshop-flow-message");
    flow.replaceChildren();
    flow.hidden = true;
    flow.workshopOptions = null;
    flow.selectedSlot = null;
    flow.contactDetails = null;
    flow.draftId = null;
    const launch = flow.launchButton || flow.closest(".webchat-service-card")
      ?.querySelector('[data-chat-action="workshop-service"]');
    message?.remove();
    if (launch) {
      launch.activeBookingFlow = null;
      launch.disabled = false;
      launch.textContent = "Find times";
      launch.setAttribute("aria-expanded", "false");
      launch.focus();
    }
  }

  async function cancelInlineWorkshop(button) {
    const flow = button.closest("[data-workshop-flow]");
    if (!flow) return;
    const draftId = button.dataset.draftId || flow.draftId;
    if (draftId) await api.cancelDraft(conversationId, draftId);
    closeInlineWorkshopFlow(flow);
  }

  async function cancelInlineOfferEnquiry(button) {
    const flow = button.closest("[data-offer-enquiry-flow]");
    if (!flow) return;
    const draftId = button.dataset.draftId || flow.draftId;
    if (draftId) await api.cancelDraft(conversationId, draftId);
    const launch = flow.launchButton;
    const message = flow.closest(".webchat-offer-enquiry-flow-message");
    flow.draftId = null;
    flow.enquiryReceipt = null;
    if (launch) launch.offerEnquiryFlow = null;
    message?.remove();
    if (launch) {
      setOfferEnquiryActionState(flow, false);
      launch.focus();
    }
  }

  async function ensureConversation() {
    if (conversationId && initialized) return;
    if (conversationId) {
      try {
        const restored = await api.restoreConversation(conversationId);
        replaceMessages(restored.messages);
        initialized = true;
        return;
      } catch (error) {
        if (error.status !== 404) throw error;
      }
    }
    const created = await api.createConversation(pageContext());
    conversationId = created.conversationId;
    localStorage.setItem(CONVERSATION_KEY, conversationId);
    replaceMessages(created.messages);
    initialized = true;
  }

  async function openPanel() {
    if (panel.open) return;
    panel.show();
    launcher.hidden = true;
    options.onOpen?.();
    localStorage.setItem(OPEN_KEY, "true");
    setUnread(0);
    setPending("Loading your conversation…");
    try {
      await ensureConversation();
      await refreshHistory();
      clearPending();
      input.focus();
    } catch (error) {
      showRequestError(error);
    }
  }

  async function startNewConversation() {
    newChat.disabled = true;
    setPending("Starting a new conversation…");
    try {
      conversationId = null;
      initialized = false;
      localStorage.removeItem(CONVERSATION_KEY);
      await ensureConversation();
      await refreshHistory();
      // Clear the transient state without showing a completion banner.
      clearPending();
      input.focus();
    } catch (error) {
      showRequestError(error);
    } finally {
      newChat.disabled = false;
    }
  }

  launcher.addEventListener("click", openPanel);
  historyList.addEventListener("click", async (event) => {
    const row = event.target.closest("[data-conversation-id]");
    if (!row || row.dataset.conversationId === conversationId) {
      hideHistory();
      input.focus();
      return;
    }
    setPending("Opening conversation…");
    try {
      const restored = await api.restoreConversation(row.dataset.conversationId);
      conversationId = restored.conversationId;
      initialized = true;
      pendingClientId = null;
      localStorage.setItem(CONVERSATION_KEY, conversationId);
      replaceMessages(restored.messages);
      await refreshHistory();
      clearPending();
      input.focus();
    } catch (error) {
      showRequestError(error);
    }
  });
  function finishClose() {
    launcher.hidden = false;
    options.onClose?.();
    localStorage.setItem(OPEN_KEY, "false");
    launcher.focus();
  }

  function closePanel() {
    if (!panel.open) {
      finishClose();
      return;
    }
    try {
      panel.close();
    } catch {
      panel.removeAttribute("open");
      finishClose();
    }
  }

  close.addEventListener("click", closePanel);
  panel.addEventListener("close", finishClose);
  panel.addEventListener("cancel", (event) => {
    event.preventDefault();
    closePanel();
  });
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
    }
  });

  newChat.addEventListener("click", startNewConversation);

  async function sendText(text, action, { appendUser = true } = {}) {
    if (!text || send.disabled) return;
    send.disabled = true;
    input.disabled = true;
    setPending("Thinking through your request…");
    try {
      await ensureConversation();
      if (!pendingClientId) {
        pendingClientId = crypto.randomUUID();
        if (appendUser) appendMessage({ role: "user", text });
      }
      const attemptedClientId = pendingClientId;
      input.value = "";
      const result = await api.sendTurn(
        conversationId,
        pendingClientId,
        text,
        pageContext(),
        action,
      );
      const assistant = result.messages.filter((message) => message.role === "assistant");
      assistant.forEach(appendMessage);
      await refreshHistory();
      if (result.status === "completed") {
        pendingClientId = null;
        clearPending();
        if (!panel.open && assistant.length) setUnread(unreadCount + assistant.length);
        return true;
      }
      pendingClientId = null;
      input.value = text;
      showTurnRetry(
        {
          message: `${result.error?.message || "That message could not be completed. Please retry."} Your text has been kept.`,
        },
        { clientMessageId: attemptedClientId, text, action },
        true,
      );
      return false;
    } catch (error) {
      input.value = text;
      showTurnRetry(
        error,
        { clientMessageId: pendingClientId, text, action },
        false,
      );
      return false;
    } finally {
      send.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendText(input.value.trim());
  });

  async function sendSuggestion(suggestion) {
    if (suggestion.disabled || send.disabled) return;
    const group = suggestion.closest(".webchat-suggestions");
    group?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    let action;
    if (suggestion.dataset.chatSuggestionAction) {
      try {
        action = JSON.parse(suggestion.dataset.chatSuggestionAction);
      } catch {
        action = undefined;
      }
    }
    const sent = await sendText(suggestion.dataset.chatSuggestion.trim(), action);
    if (sent) {
      if (group !== starterSuggestions) group?.remove();
    } else {
      group?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    }
  }

  starterSuggestions.addEventListener("click", async (event) => {
    const suggestion = event.target.closest("[data-chat-suggestion]");
    if (suggestion) await sendSuggestion(suggestion);
  });

  transcript.addEventListener("click", async (event) => {
    const suggestion = event.target.closest("[data-chat-suggestion]");
    if (suggestion) {
      await sendSuggestion(suggestion);
      return;
    }
    const button = event.target.closest("[data-chat-action]");
    if (!button || button.disabled) return;
    if (button.dataset.chatAction === "retry-turn") {
      const retry = retryTurns.get(button.dataset.retryId);
      if (!retry) return;
      retryTurns.delete(button.dataset.retryId);
      button.disabled = true;
      pendingClientId = retry.clientMessageId;
      await sendText(retry.text, retry.action, { appendUser: retry.appendUser });
      return;
    }
    button.disabled = true;
    setPending("Working…");
    try {
      if (button.dataset.chatAction === "cancel-inline-test-drive") {
        await cancelInlineTestDrive(button);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "cancel-inline-workshop") {
        await cancelInlineWorkshop(button);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "cancel-inline-offer-enquiry") {
        await cancelInlineOfferEnquiry(button);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "cancel-workflow-form") {
        const card = button.closest(".webchat-workflow-form-card");
        const cancelled = await api.cancelDraft(conversationId, button.dataset.draftId);
        card?.replaceWith(receiptCard(cancelled.result));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "offer-enquiry") {
        await ensureConversation();
        const existingFlow = button.offerEnquiryFlow;
        if (existingFlow) {
          const message = existingFlow.closest(".webchat-offer-enquiry-flow-message");
          if (message && !message.hidden) {
            setOfferEnquiryVisible(existingFlow, false);
          } else {
            setOfferEnquiryVisible(existingFlow, true);
          }
          clearPending();
          return;
        }
        const result = await api.getOfferEnquiryOptions(
          conversationId,
          button.dataset.offerId,
        );
        appendOfferEnquiryFlowMessage(button, result.view);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "test-drive") {
        await ensureConversation();
        if (button.activeBookingFlow) {
          const message = button.activeBookingFlow.closest(".webchat-test-drive-flow-message");
          setActiveBookingFlowVisible(
            button.activeBookingFlow,
            Boolean(message?.hidden),
            "test-drive",
          );
          clearPending();
          return;
        }
        const result = await api.getTestDriveOptions(conversationId, button.dataset.vehicleId);
        appendTestDriveFlowMessage(button, result.view);
        button.setAttribute("aria-expanded", "true");
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "workshop-service") {
        await ensureConversation();
        if (button.activeBookingFlow) {
          const message = button.activeBookingFlow.closest(".webchat-workshop-flow-message");
          setActiveBookingFlowVisible(
            button.activeBookingFlow,
            Boolean(message?.hidden),
            "workshop",
          );
          clearPending();
          return;
        }
        const result = await api.getWorkshopOptions(
          conversationId,
          button.dataset.serviceTypeId,
          button.dataset.dealershipId,
        );
        appendWorkshopFlowMessage(button, result.view);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "view-booked-test-drive") {
        const flow = button.closest(".webchat-vehicle-card")
          ?.querySelector("[data-booking-flow]");
        if (flow && !flow.hidden && flow.childElementCount) {
          flow.replaceChildren();
          flow.hidden = true;
          setBookedTestDriveActionState(flow, false);
          button.focus();
        } else {
          showBookedTestDrive(flow);
        }
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "hide-booked-test-drive") {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren();
        flow.hidden = true;
        setBookedTestDriveActionState(flow, false);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-test-drive-slot") {
        const flow = button.closest("[data-booking-flow]");
        if (!flow) {
          throw new Error(
            "This test-drive selection is no longer active. Please choose Book test drive again.",
          );
        }
        flow.selectedSlot = {
          slotId: button.dataset.slotId,
          vehicleId: button.dataset.vehicleId,
          slotLabel: button.dataset.slotLabel,
          dealershipName: button.dataset.dealershipName,
        };
        flow.replaceChildren(testDriveDetailsForm({
          ...flow.selectedSlot,
          vehicleLabel: flow.vehicleLabel,
        }));
        applyRetainedDetails(flow.querySelector("[data-test-drive-details]"), flow.contactDetails);
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-workshop-slot") {
        const flow = button.closest("[data-workshop-flow]");
        if (!flow) {
          throw new Error(
            "This workshop selection is no longer active. Please choose Find times again.",
          );
        }
        flow.selectedSlot = {
          slotId: button.dataset.slotId,
          serviceTypeId: button.dataset.serviceTypeId,
          dealershipId: button.dataset.dealershipId,
          slotLabel: button.dataset.slotLabel,
          dealershipName: button.dataset.dealershipName,
          serviceName: button.dataset.serviceName || flow.serviceName,
        };
        setWorkshopFlowContent(flow, workshopDetailsForm(flow.selectedSlot));
        applyRetainedDetails(flow.querySelector("[data-workshop-details]"), flow.contactDetails);
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-workshop-amendment-slot") {
        const flow = button.closest("[data-workshop-flow]");
        const result = await api.prepareWorkshopAmendment(conversationId, {
          slotId: button.dataset.slotId,
        });
        const review = renderWorkflowCard(result.view);
        review.retainedReceiptView = flow.retainedReceiptView;
        setWorkshopFlowContent(flow, review, "child");
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "workshop-amendment-times") {
        const card = button.closest(".webchat-workflow-form-card");
        const result = await api.getWorkshopAmendmentOptions(conversationId);
        const existingFlow = card.closest("[data-workshop-flow]");
        const flow = existingFlow || document.createElement("div");
        if (!existingFlow) {
          flow.className = "webchat-booking-flow webchat-workshop-flow-standalone";
          flow.dataset.workshopFlow = "true";
        }
        flow.workshopOptions = result.view;
        flow.retainedReceiptView = card.retainedReceiptView;
        setWorkshopFlowContent(flow, workshopSlotPicker(result.view));
        if (!existingFlow) card.replaceWith(flow);
        clearPending();
        return;
      }
      if (["start-workshop-amendment", "start-workshop-cancellation"].includes(
        button.dataset.chatAction,
      )) {
        const mode = button.dataset.chatAction === "start-workshop-amendment"
          ? "amend"
          : "cancel";
        const result = await api.prepareExistingWorkshopAction(
          conversationId,
          mode,
          button.dataset.bookingReference,
        );
        const bookingCard = button.closest(
          ".webchat-workshop-booking-card, .webchat-booking-disclosure",
        );
        const workflowCard = renderWorkflowCard(result.view, result.viewType);
        if (bookingCard?.receiptView) {
          workflowCard.retainedReceiptView = bookingCard.receiptView;
        }
        const workshopFlow = bookingCard?.closest("[data-workshop-flow]");
        if (workshopFlow) setWorkshopFlowContent(workshopFlow, workflowCard, "child");
        else bookingCard?.replaceWith(workflowCard);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "back-to-test-drive-slots") {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren(testDriveSlotPicker(flow.testDriveOptions, { inline: true }));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "back-to-workshop-slots") {
        const flow = button.closest("[data-workshop-flow]");
        setWorkshopFlowContent(
          flow,
          workshopSlotPicker(flow.workshopOptions, { inline: true }),
        );
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "edit-test-drive-details") {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren(testDriveDetailsForm({
          ...flow.selectedSlot,
          vehicleLabel: flow.vehicleLabel,
        }));
        Object.entries(flow.contactDetails || {}).forEach(([name, value]) => {
          const field = flow.querySelector(`[name="${name}"]`);
          if (field) field.value = value;
        });
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "edit-workshop-details") {
        const flow = button.closest("[data-workshop-flow]");
        setWorkshopFlowContent(flow, workshopDetailsForm(flow.selectedSlot));
        Object.entries(flow.contactDetails || {}).forEach(([name, value]) => {
          const field = flow.querySelector(`[name="${name}"]`);
          if (field) field.value = value;
        });
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (
        button.dataset.chatAction === "cancel"
        && button.closest("[data-offer-enquiry-flow]")
      ) {
        await cancelInlineOfferEnquiry(button);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "confirm") {
        button.dataset.clientActionId ||= crypto.randomUUID();
        const receipt = await api.confirmDraft(
          conversationId,
          button.dataset.draftId,
          button.dataset.clientActionId,
          button.dataset.expectedKind,
        );
        if (
          button.dataset.expectedKind
          && receipt.result?.kind !== button.dataset.expectedKind
        ) {
          throw new Error("The confirmation response did not match this booking. Please retry.");
        }
        if (button.dataset.inlineBooking === "true") {
          const flow = button.closest("[data-booking-flow]");
          const booking = {
            ...receipt.result,
            slotLabel: flow.selectedSlot?.slotLabel,
            dealershipName: flow.selectedSlot?.dealershipName,
            vehicleLabel: flow.vehicleLabel,
          };
          const confirmed = renderMessage({
            role: "assistant",
            text: "Your test drive is confirmed.",
            viewType: "receipt",
            view: booking,
          });
          setBookedTestDriveAction(flow, booking);
          if (flow.launchButton) flow.launchButton.activeBookingFlow = null;
          flow.closest(".webchat-test-drive-flow-message")?.replaceWith(confirmed);
          clearPending();
          return;
        }
        if (button.dataset.inlineBooking === "workshop") {
          const flow = button.closest("[data-workshop-flow]");
          const booking = {
            ...receipt.result,
            slotLabel: flow.selectedSlot?.slotLabel,
            dealershipName: flow.selectedSlot?.dealershipName,
            serviceName: flow.selectedSlot?.serviceName || flow.serviceName,
            registration: flow.contactDetails?.registration,
          };
          setWorkshopFlowContent(flow, receiptCard(booking), "child");
          flow.draftId = null;
          if (flow.launchButton) {
            setActiveBookingFlowVisible(flow, true, "workshop");
          }
          clearPending();
          return;
        }
        const offerFlow = button.closest("[data-offer-enquiry-flow]");
        if (offerFlow) {
          offerFlow.enquiryReceipt = receipt.result;
          offerFlow.replaceChildren(receiptCard(receipt.result));
          offerFlow.draftId = null;
          setOfferEnquiryActionState(offerFlow, true, true);
          window.setTimeout(() => {
            if (
              offerFlow.enquiryReceipt?.reference === receipt.result.reference
              && offerFlow.querySelector(".webchat-receipt-card")
            ) {
              setOfferEnquiryVisible(offerFlow, false, true);
            }
          }, 5000);
          clearPending();
          return;
        }
        const confirmation = button.closest(".webchat-confirmation");
        const inlineWorkshopFlow = confirmation?.closest("[data-workshop-flow]");
        if (receipt.result.kind === "workshop_cancel" && inlineWorkshopFlow) {
          closeInlineWorkshopFlow(inlineWorkshopFlow);
          clearPending();
          return;
        }
        const standaloneWorkshopFlow = confirmation?.closest(
          ".webchat-workshop-flow-standalone",
        );
        (standaloneWorkshopFlow || confirmation)?.replaceWith(receiptCard(receipt.result));
        clearPending();
        return;
      } else {
        const cancelled = await api.cancelDraft(conversationId, button.dataset.draftId);
        const confirmation = button.closest(".webchat-confirmation");
        if (
          cancelled.result.kind === "workshop_change_abandoned"
          && confirmation?.retainedReceiptView
        ) {
          confirmation.replaceWith(receiptCard(confirmation.retainedReceiptView));
          clearPending();
          return;
        }
        const standaloneWorkshopFlow = confirmation?.closest(
          ".webchat-workshop-flow-standalone",
        );
        (standaloneWorkshopFlow || confirmation)?.replaceWith(
          receiptCard(cancelled.result),
        );
        clearPending();
        return;
      }
    } catch (error) {
      if (showBookingRecovery(button, error)) return;
      showRequestError(error);
      button.disabled = false;
    }
  });


  transcript.addEventListener("submit", createFormSubmitHandler({
    api,
    getConversationId: () => conversationId,
    formProfile,
    setPending,
    clearPending,
    showRequestError,
    appendMessage,
  }));

  if (localStorage.getItem(OPEN_KEY) === "true") openPanel();

  return {
    open: openPanel,
    close: closePanel,
    newConversation: () => newChat.click(),
  };
}
