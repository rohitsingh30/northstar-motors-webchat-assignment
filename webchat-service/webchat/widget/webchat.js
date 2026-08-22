// Controller state is private to the widget and independent of the host application.
import { createChatApi } from "./webchat-api.js";
import { pageContext } from "./webchat-context.js";
import {
  inlineBookingReceipt,
  inlineBookedTestDrive,
  inlineTestDriveConfirmation,
  inlineWorkshopConfirmation,
  inlineWorkshopReceipt,
  receiptCard,
  renderMessage,
  renderWorkflowCard,
  testDriveDetailsForm,
  testDriveSlotPicker,
  workshopDetailsForm,
  workshopSlotPicker,
} from "./webchat-view.js";

const CONVERSATION_KEY = "northstarConversationId";
const OPEN_KEY = "northstarChatOpen";
const FORM_PROFILE_KEY = "northstarFormProfileV1";
const INTERNAL_FORM_FIELDS = new Set([
  "draftId",
  "mode",
  "offerId",
  "serviceTypeId",
  "slotId",
  "vehicleId",
  "verifiedGrantId",
]);
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
  const progressValue = root.querySelector("#webchat-progress-value");
  const form = root.querySelector("#webchat-form");
  const starterSuggestions = root.querySelector("#webchat-starter-suggestions");
  const input = root.querySelector("#webchat-input");
  const send = root.querySelector("#webchat-send");
  const unread = root.querySelector("#webchat-unread");
  let conversationId = localStorage.getItem(CONVERSATION_KEY);
  let initialized = false;
  let pendingClientId = null;
  let unreadCount = 0;
  let progressTimer = null;

  function savedFormProfile() {
    try {
      const parsed = JSON.parse(localStorage.getItem(FORM_PROFILE_KEY) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  function reusableFormField(field) {
    const name = String(field?.name || "");
    const type = String(field?.type || "").toLowerCase();
    return Boolean(
      name
      && !INTERNAL_FORM_FIELDS.has(name)
      && !field.disabled
      && !field.readOnly
      && !["button", "file", "hidden", "password", "reset", "submit"].includes(type)
      && field.closest("form")
      && transcript.contains(field)
    );
  }

  function formProfileFields(container) {
    const selector = "input[name], select[name], textarea[name]";
    return [
      ...(container.matches?.(selector) ? [container] : []),
      ...container.querySelectorAll(selector),
    ];
  }

  function rememberFormFields(container) {
    const profile = savedFormProfile();
    let changed = false;
    formProfileFields(container).forEach((field) => {
      if (!reusableFormField(field)) return;
      const value = field.type === "checkbox"
        ? (field.checked ? field.value || "true" : "")
        : String(field.value || "").trim();
      if (value) {
        if (profile[field.name] !== value) {
          profile[field.name] = value.slice(0, 2_000);
          changed = true;
        }
      } else if (Object.hasOwn(profile, field.name)) {
        delete profile[field.name];
        changed = true;
      }
    });
    if (!changed) return;
    try {
      localStorage.setItem(FORM_PROFILE_KEY, JSON.stringify(profile));
    } catch {
      // Browser privacy settings or storage limits may disable local persistence.
    }
  }

  function applySavedFormFields(container) {
    const profile = savedFormProfile();
    formProfileFields(container).forEach((field) => {
      if (
        !reusableFormField(field)
        || field.dataset.profileValueSource === "server"
        || !Object.hasOwn(profile, field.name)
      ) return;
      const value = String(profile[field.name]);
      if (field instanceof HTMLSelectElement) {
        if ([...field.options].some((option) => option.value === value)) field.value = value;
        return;
      }
      if (field.type === "checkbox") {
        field.checked = value === (field.value || "true");
        return;
      }
      if (!field.value) field.value = value;
    });
  }

  const formProfileObserver = new MutationObserver((records) => {
    records.forEach((record) => {
      record.addedNodes.forEach((node) => {
        if (node instanceof Element) applySavedFormFields(node);
      });
    });
  });
  formProfileObserver.observe(transcript, { childList: true, subtree: true });
  transcript.addEventListener("change", (event) => {
    if (reusableFormField(event.target)) rememberFormFields(event.target);
  });

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
    window.clearInterval(progressTimer);
    panel.removeAttribute("aria-busy");
    progress.hidden = true;
    status.textContent = "";
    progressBar.value = 0;
    progressValue.textContent = "";
  }

  function setPending(label) {
    window.clearInterval(progressTimer);
    panel.setAttribute("aria-busy", "true");
    status.textContent = label;
    progress.hidden = false;
    let value = 14;
    progressBar.value = value;
    progressValue.textContent = `${value}%`;
    progressTimer = window.setInterval(() => {
      value = Math.min(88, value + Math.max(1, Math.round((90 - value) / 8)));
      progressBar.value = value;
      progressValue.textContent = `${value}%`;
    }, 450);
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
  }

  function setBookedTestDriveAction(flow, receipt) {
    const card = flow.closest(".webchat-vehicle-card");
    const action = card?.querySelector('[data-chat-action="test-drive"]');
    if (!action) return;
    action.disabled = false;
    action.dataset.chatAction = "view-booked-test-drive";
    action.textContent = "View booked test drive";
    flow.booking = {
      reference: receipt.reference,
      slotLabel: flow.selectedSlot?.slotLabel,
      dealershipName: flow.selectedSlot?.dealershipName,
    };
  }

  function enableBookedTestDriveAction(flow) {
    const action = flow?.closest(".webchat-vehicle-card")
      ?.querySelector('[data-chat-action="view-booked-test-drive"]');
    if (action) action.disabled = false;
  }

  function replaceMessages(messages) {
    transcript.replaceChildren(...messages.map(renderMessage));
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

  function showFieldErrors(detailsForm, fieldErrors = {}) {
    const formError = detailsForm.querySelector("[data-form-error]");
    if (formError) {
      formError.hidden = true;
      formError.textContent = "";
    }
    detailsForm.querySelectorAll("[data-field-error]").forEach((message) => {
      const field = detailsForm.elements.namedItem(message.dataset.fieldError);
      field?.removeAttribute("aria-invalid");
      message.hidden = true;
      message.textContent = "";
    });
    let firstInvalid = null;
    Object.entries(fieldErrors).forEach(([name, message]) => {
      const field = detailsForm.elements.namedItem(name);
      const error = detailsForm.querySelector(`[data-field-error="${name}"]`);
      if (!field || !error) return;
      field.setAttribute("aria-invalid", "true");
      error.textContent = message;
      error.hidden = false;
      firstInvalid ||= field;
    });
    firstInvalid?.focus();
  }

  function showFormError(form, message) {
    const error = form.querySelector("[data-form-error]");
    if (!error) return;
    error.textContent = message || "Check the booking details and try again.";
    error.hidden = false;
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
    const launch = flow.closest(".webchat-vehicle-card")
      ?.querySelector('[data-chat-action="test-drive"]');
    if (launch) {
      launch.disabled = false;
      launch.focus();
    }
  }

  async function cancelInlineWorkshop(button) {
    const flow = button.closest("[data-workshop-flow]");
    if (!flow) return;
    const draftId = button.dataset.draftId || flow.draftId;
    if (draftId) await api.cancelDraft(conversationId, draftId);
    flow.replaceChildren();
    flow.hidden = true;
    flow.workshopOptions = null;
    flow.selectedSlot = null;
    flow.contactDetails = null;
    flow.draftId = null;
    const launch = flow.closest(".webchat-service-card")
      ?.querySelector('[data-chat-action="workshop-service"]');
    if (launch) {
      launch.disabled = false;
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
      window.clearInterval(progressTimer);
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
      window.clearInterval(progressTimer);
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

  async function sendText(text, action) {
    if (!text || send.disabled) return;
    send.disabled = true;
    input.disabled = true;
    setPending("Thinking through your request…");
    try {
      await ensureConversation();
      if (!pendingClientId) {
        pendingClientId = crypto.randomUUID();
        appendMessage({ role: "user", text });
      }
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
      pendingClientId = null;
      await refreshHistory();
      if (result.status === "completed") clearPending();
      else {
        window.clearInterval(progressTimer);
        showRequestError({ message: "Please try that message again." });
      }
      if (!panel.open && assistant.length) setUnread(unreadCount + assistant.length);
      return true;
    } catch (error) {
      window.clearInterval(progressTimer);
      showRequestError(error, "Select Send to retry.");
      input.value = text;
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
      if (button.dataset.chatAction === "test-drive") {
        await ensureConversation();
        const result = await api.getTestDriveOptions(conversationId, button.dataset.vehicleId);
        const flow = button.closest(".webchat-vehicle-card").querySelector("[data-booking-flow]");
        flow.testDriveOptions = result.view;
        flow.hidden = false;
        flow.replaceChildren(testDriveSlotPicker(result.view, { inline: true }));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "workshop-service") {
        await ensureConversation();
        const result = await api.getWorkshopOptions(
          conversationId,
          button.dataset.serviceTypeId,
        );
        const flow = button.closest(".webchat-service-card").querySelector("[data-workshop-flow]");
        flow.workshopOptions = result.view;
        flow.serviceName = button.dataset.serviceName;
        flow.hidden = false;
        flow.replaceChildren(workshopSlotPicker(result.view, { inline: true }));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "view-booked-test-drive") {
        showBookedTestDrive(button.closest(".webchat-vehicle-card")?.querySelector("[data-booking-flow]"));
        button.disabled = false;
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "hide-booked-test-drive") {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren();
        flow.hidden = true;
        enableBookedTestDriveAction(flow);
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-test-drive-slot") {
        const flow = button.closest("[data-booking-flow]");
        flow.selectedSlot = {
          slotId: button.dataset.slotId,
          slotLabel: button.dataset.slotLabel,
          dealershipName: button.dataset.dealershipName,
        };
        flow.replaceChildren(testDriveDetailsForm(flow.selectedSlot));
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-workshop-slot") {
        const flow = button.closest("[data-workshop-flow]");
        flow.selectedSlot = {
          slotId: button.dataset.slotId,
          slotLabel: button.dataset.slotLabel,
          dealershipName: button.dataset.dealershipName,
          serviceName: button.dataset.serviceName || flow.serviceName,
        };
        flow.replaceChildren(workshopDetailsForm(flow.selectedSlot));
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "select-workshop-amendment-slot") {
        const flow = button.closest("[data-workshop-flow]");
        const result = await api.prepareWorkshopAmendment(conversationId, {
          slotId: button.dataset.slotId,
        });
        flow.replaceChildren(renderWorkflowCard(result.view));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "workshop-amendment-times") {
        const card = button.closest(".webchat-workflow-form-card");
        const result = await api.getWorkshopAmendmentOptions(conversationId);
        const flow = document.createElement("div");
        flow.className = "webchat-booking-flow webchat-workshop-flow-standalone";
        flow.dataset.workshopFlow = "true";
        flow.workshopOptions = result.view;
        flow.append(workshopSlotPicker(result.view));
        card.replaceWith(flow);
        clearPending();
        return;
      }
      if (["start-workshop-amendment", "start-workshop-cancellation"].includes(
        button.dataset.chatAction,
      )) {
        const mode = button.dataset.chatAction === "start-workshop-amendment"
          ? "amend"
          : "cancel";
        const result = await api.prepareExistingWorkshopAction(conversationId, mode);
        button.closest(".webchat-workshop-booking-card")?.replaceWith(
          renderWorkflowCard(result.view, result.viewType),
        );
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
        flow.replaceChildren(workshopSlotPicker(flow.workshopOptions, { inline: true }));
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "edit-test-drive-details") {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren(testDriveDetailsForm(flow.selectedSlot));
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
        flow.replaceChildren(workshopDetailsForm(flow.selectedSlot));
        Object.entries(flow.contactDetails || {}).forEach(([name, value]) => {
          const field = flow.querySelector(`[name="${name}"]`);
          if (field) field.value = value;
        });
        flow.querySelector("input")?.focus();
        clearPending();
        return;
      }
      if (button.dataset.chatAction === "confirm") {
        button.dataset.clientActionId ||= crypto.randomUUID();
        const receipt = await api.confirmDraft(
          conversationId,
          button.dataset.draftId,
          button.dataset.clientActionId,
        );
        if (button.dataset.inlineBooking === "true") {
          const flow = button.closest("[data-booking-flow]");
          flow.replaceChildren(inlineBookingReceipt(receipt.result));
          flow.draftId = null;
          setBookedTestDriveAction(flow, receipt.result);
          window.setTimeout(() => {
            if (
              flow.booking?.reference === receipt.result.reference
              && flow.querySelector(".webchat-inline-receipt")
            ) {
              flow.replaceChildren();
              flow.hidden = true;
            }
          }, 5000);
          clearPending();
          return;
        }
        if (button.dataset.inlineBooking === "workshop") {
          const flow = button.closest("[data-workshop-flow]");
          flow.replaceChildren(inlineWorkshopReceipt(receipt.result));
          flow.draftId = null;
          clearPending();
          return;
        }
        const confirmation = button.closest(".webchat-confirmation");
        const standaloneWorkshopFlow = confirmation?.closest(
          ".webchat-workshop-flow-standalone",
        );
        (standaloneWorkshopFlow || confirmation)?.replaceWith(receiptCard(receipt.result));
        clearPending();
        return;
      } else {
        await api.cancelDraft(conversationId, button.dataset.draftId);
        appendMessage({ role: "assistant", text: "That request has been cancelled." });
      }
      clearPending();
      button.closest(".webchat-confirmation")
        ?.querySelectorAll("button")
        .forEach((action) => { action.disabled = true; });
    } catch (error) {
      if (button.dataset.inlineBooking === "true" && Object.keys(error.fieldErrors || {}).length) {
        const flow = button.closest("[data-booking-flow]");
        flow.replaceChildren(testDriveDetailsForm(flow.selectedSlot));
        Object.entries(flow.contactDetails || {}).forEach(([name, value]) => {
          const field = flow.querySelector(`[name="${name}"]`);
          if (field) field.value = value;
        });
        showFieldErrors(flow.querySelector("[data-test-drive-details]"), error.fieldErrors);
      }
      window.clearInterval(progressTimer);
      showRequestError(error);
      button.disabled = false;
    }
  });

  transcript.addEventListener("submit", async (event) => {
    rememberFormFields(event.target);
    const testDriveForm = event.target.closest("[data-test-drive-details]");
    if (testDriveForm) {
      event.preventDefault();
      const submit = testDriveForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your booking…");
      const flow = testDriveForm.closest("[data-booking-flow]");
      const details = Object.fromEntries(new FormData(testDriveForm));
      flow.contactDetails = details;
      showFieldErrors(testDriveForm);
      try {
        const result = await api.prepareTestDrive(conversationId, {
          ...details,
          slotId: flow.selectedSlot.slotId,
        });
        flow.replaceChildren(
          inlineTestDriveConfirmation(result.view, flow.selectedSlot.slotLabel),
        );
        flow.draftId = result.view.draftId;
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(testDriveForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const workshopForm = event.target.closest("[data-workshop-details]");
    if (workshopForm) {
      event.preventDefault();
      const submit = workshopForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your workshop booking…");
      const flow = workshopForm.closest("[data-workshop-flow]");
      const details = Object.fromEntries(new FormData(workshopForm));
      details.mileage = Number(details.mileage);
      if (!details.notes) delete details.notes;
      flow.contactDetails = details;
      showFieldErrors(workshopForm);
      try {
        const result = await api.prepareWorkshopBooking(conversationId, {
          ...details,
          slotId: flow.selectedSlot.slotId,
        });
        flow.replaceChildren(
          inlineWorkshopConfirmation(result.view, flow.selectedSlot, details),
        );
        flow.draftId = result.view.draftId;
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(workshopForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const partExchangeForm = event.target.closest("[data-part-exchange-details]");
    if (partExchangeForm) {
      event.preventDefault();
      const submit = partExchangeForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your indicative estimate…");
      const details = Object.fromEntries(new FormData(partExchangeForm));
      details.mileage = Number(details.mileage);
      showFieldErrors(partExchangeForm);
      try {
        const result = await api.preparePartExchange(conversationId, details);
        partExchangeForm.closest(".webchat-part-exchange-card")?.replaceWith(
          renderWorkflowCard(result.view),
        );
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(partExchangeForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const partExchangeEstimateForm = event.target.closest("[data-part-exchange-estimate]");
    if (partExchangeEstimateForm) {
      event.preventDefault();
      const submit = partExchangeEstimateForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Calculating your indicative estimate…");
      const details = Object.fromEntries(new FormData(partExchangeEstimateForm));
      details.mileage = Number(details.mileage);
      showFieldErrors(partExchangeEstimateForm);
      try {
        const result = await api.estimatePartExchange(conversationId, details);
        partExchangeEstimateForm.closest(".webchat-part-exchange-card")?.replaceWith(
          renderWorkflowCard(result.view, result.viewType),
        );
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(partExchangeEstimateForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const callbackForm = event.target.closest("[data-callback-details]");
    if (callbackForm) {
      event.preventDefault();
      const submit = callbackForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your callback request…");
      const details = Object.fromEntries(new FormData(callbackForm));
      showFieldErrors(callbackForm);
      try {
        const result = await api.prepareCallback(conversationId, details);
        callbackForm.closest(".webchat-callback-card")?.replaceWith(renderWorkflowCard(result.view));
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(callbackForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const workflowForm = event.target.closest("[data-workflow-details]");
    if (workflowForm) {
      event.preventDefault();
      const submit = workflowForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your request…");
      const details = Object.fromEntries(new FormData(workflowForm));
      Object.keys(details).forEach((key) => {
        if (details[key] === "") delete details[key];
      });
      if (details.mileage !== undefined) details.mileage = Number(details.mileage);
      const prepare = {
        sales_enquiry: api.prepareSalesEnquiry,
        vehicle_interest: api.prepareVehicleInterest,
        dealership_message: api.prepareDealershipMessage,
        workshop_amend: api.prepareWorkshopAmendment,
      }[workflowForm.dataset.workflowDetails];
      showFieldErrors(workflowForm);
      try {
        if (!prepare) throw new Error("This request form is unavailable.");
        const result = await prepare(conversationId, details);
        workflowForm.closest(".webchat-workflow-form-card")?.replaceWith(
          renderWorkflowCard(result.view),
        );
        clearPending();
      } catch (error) {
        window.clearInterval(progressTimer);
        showRequestError(error);
        showFieldErrors(workflowForm, error.fieldErrors);
        submit.disabled = false;
      }
      return;
    }
    const lookupForm = event.target.closest("[data-private-lookup]");
    if (!lookupForm) return;
    event.preventDefault();
    const submit = lookupForm.querySelector("button[type='submit']");
    submit.disabled = true;
    setPending("Checking those booking details…");
    const proof = Object.fromEntries(new FormData(lookupForm));
    showFieldErrors(lookupForm);
    try {
      const result = await api.lookupWorkshopBooking(conversationId, proof);
      lookupForm.reset();
      lookupForm.remove();
      appendMessage({
        role: "assistant",
        text: result.text || "Your workshop booking has been verified.",
        viewType: result.viewType,
        view: result.view,
      });
      clearPending();
    } catch (error) {
      window.clearInterval(progressTimer);
      if (Object.keys(error.fieldErrors || {}).length) {
        clearPending();
        showFieldErrors(lookupForm, error.fieldErrors);
      } else if (error.status >= 400 && error.status < 500 && !error.retryable) {
        clearPending();
        showFormError(lookupForm, error.message);
      } else {
        showRequestError(error);
      }
      submit.disabled = false;
    }
  });

  if (localStorage.getItem(OPEN_KEY) === "true") openPanel();

  return {
    open: openPanel,
    close: closePanel,
    newConversation: () => newChat.click(),
  };
}
