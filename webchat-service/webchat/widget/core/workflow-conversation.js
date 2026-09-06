// Protected, per-conversation input capture. Public conversation never enters this module.
// Private values remain in the tab until buildSubmission() produces one validated payload.
import {
  fieldChoices,
  fieldDefinition as definitionForField,
} from "./workflow-fields.js?v=20260906.1";
import {
  WORKFLOW_CONVERSATION_SPECS,
  questionGroup,
  workflowSteps,
} from "./workflow-specs.js?v=20260906.1";

export const WORKFLOW_SESSION_VERSION = 1;
export const WORKFLOW_SESSION_PREFIX = "northstarWorkflowConversation:v1:";

export { WORKFLOW_CONVERSATION_SPECS } from "./workflow-specs.js?v=20260906.1";

function clean(value) {
  return String(value ?? "").trim();
}

function parseName(value) {
  const parts = clean(value).replace(/\s+/g, " ").split(" ").filter(Boolean);
  if (parts.length < 2 || parts.some((part) => /\d/.test(part))) return null;
  return { fullName: parts.join(" "), firstName: parts.slice(0, -1).join(" "), lastName: parts.at(-1) };
}

function parsePhone(value) {
  let phone = clean(value).replace(/[\s().-]/g, "");
  if (phone.startsWith("+44")) phone = `0${phone.slice(3)}`;
  return /^0(?:1|7)\d{8,9}$/.test(phone) ? phone : null;
}

export function privateCorrectionField(text) {
  const value = String(text).toLowerCase();
  const correction = /\b(?:change|update|correct|replace|edit)\s+(?:my\s+)?/;
  const wrong = /\b(?:entered|enetered|eneted|enterd|gave|typed|spelled|got)?\s*(?:the|my)?\s*(?:wrong|incorrect)\s+/;
  const matches = (field) => new RegExp(`${correction.source}${field}\\b`).test(value)
    || new RegExp(`${wrong.source}${field}\\b`).test(value)
    || new RegExp(`\\b${field}\\b[^.!?]{0,40}\\b(?:wrong|incorrect)\\b`).test(value);
  if (matches("(?:email|e-mail)") || /^\s*(?:my\s+)?(?:email|e-mail)\s*(?:is|:)/.test(value)) return "email";
  if (matches("(?:phone|mobile)(?:\\s+number)?") || /^\s*(?:my\s+)?(?:phone|mobile)(?:\s+number)?\s*(?:is|:)/.test(value)) return "phone";
  if (matches("(?:surname|last\\s+name)")) return "lastName";
  if (matches("(?:full\\s+)?name") || /^\s*(?:my\s+)?(?:full\s+)?name\s*(?:is|:)/.test(value)) return "fullName";
  if (matches("mileage") || /^\s*(?:my\s+)?mileage\s*(?:is|:)/.test(value)) return "mileage";
  if (matches("(?:registration|reg)") || /^\s*(?:my\s+)?(?:registration|reg)\s*(?:is|:)/.test(value)) return "registration";
  if (matches("condition") || /^\s*(?:my\s+)?condition\s*(?:is|:)/.test(value)) return "condition";
  return null;
}

export function requestedPrivateCorrectionField(state, text) {
  const field = privateCorrectionField(text);
  // State outranks wording. "My name is …" and "change my name to …" are values when the
  // protected collector is already asking for that name; treating them as another edit command
  // reopens the same field forever.
  return field && field !== state?.currentField ? field : null;
}

export function isPublicSteeringMessage(text) {
  const value = String(text).trim();
  return /^(?:actually[, ]*)?(?:what|when|where|which|who|why|how|show|find|tell|help|compare|book|arrange)\b/i.test(value)
    || /^(?:actually[, ]*)?(?:can|could|would|do|does|is|are)\s+(?:you|i|we|northstar|the|this|that)\b/i.test(value)
    || /^(?:actually[, ]*)?i\s+(?:want|need|would like)\b/i.test(value)
    || /\b(?:change|update|correct|replace|edit|switch|move)\s+(?:(?:the|my)\s+)?(?:dealership|location|department|reason|vehicle|car|appointment|service|message|subject|request)\b/i.test(value)
    || /^(?:actually[, ]*)?(?:use|switch to|move to|make it)\b[\s\S]{0,80}\b(?:instead|dealership|location|department|vehicle|car|appointment|service)\b/i.test(value);
}

// Booking references are platform-owned opaque identifiers. The widget deliberately
// knows nothing about their prefix, alphabet, or suffix length; it only applies the
// transport limits of the booking-lookup API before sending the value unchanged.
function parseBookingReference(value) {
  const reference = clean(value);
  return reference.length >= 3 && reference.length <= 80 && !/\s/.test(reference)
    ? reference
    : null;
}

function validationError(field, value, repeated = false) {
  const input = clean(value);
  const prefix = repeated ? "That still doesn’t look right. " : "That doesn’t look right. ";

  if (!input) return `${prefix}Please enter your ${fieldDefinition(field).label}.`;

  if (field === "fullName") {
    if (/\d/.test(input)) return `${prefix}Names can’t contain numbers. Please enter your first and last name.`;
    return `${prefix}Please enter both your first and last name, separated by a space.`;
  }
  if (field === "email") {
    if (/\s/.test(input)) return `${prefix}Email addresses can’t contain spaces. Use a format like name@example.com.`;
    if (!input.includes("@")) return `${prefix}The email address is missing an @ sign. Use a format like name@example.com.`;
    if (!/\.[A-Za-z]{2,}$/.test(input.split("@").at(-1) || "")) {
      return `${prefix}The email address needs a complete domain, such as example.com.`;
    }
    return `${prefix}Please enter a complete email address, such as name@example.com.`;
  }
  if (field === "phone") {
    const compact = input.replace(/[\s().-]/g, "");
    if (/[^\d+]/.test(compact) || (compact.includes("+") && !compact.startsWith("+44"))) {
      return `${prefix}Phone numbers can only contain digits, spaces, brackets, hyphens, and an optional +44 prefix.`;
    }
    const normalized = compact.startsWith("+44") ? `0${compact.slice(3)}` : compact;
    if (!normalized.startsWith("01") && !normalized.startsWith("07")) {
      return `${prefix}Please enter a UK 01 landline or 07 mobile number, starting with 0 or +44.`;
    }
    if (!/^\d+$/.test(normalized) || normalized.length < 10) {
      return `${prefix}The phone number has too few digits. Enter a 10 or 11 digit UK number, for example 07700 900123.`;
    }
    if (normalized.length > 11) {
      return `${prefix}The phone number has too many digits. Enter a 10 or 11 digit UK number, for example 07700 900123.`;
    }
    return `${prefix}Please enter a valid UK 01 landline or 07 mobile number, for example 07700 900123.`;
  }
  if (field === "registration") {
    const compact = input.toUpperCase().replace(/\s+/g, "");
    if (/[^A-Z0-9]/.test(compact)) {
      return `${prefix}Vehicle registrations can only contain letters, numbers, and spaces.`;
    }
    if (compact.length < 5) return `${prefix}The vehicle registration is too short. Use a UK format such as AB12 CDE.`;
    if (compact.length > 7) return `${prefix}The vehicle registration is too long. Use a UK format such as AB12 CDE.`;
    return `${prefix}Please enter a valid UK vehicle registration, for example AB12 CDE.`;
  }
  if (field === "mileage") {
    if (!/\d/.test(input)) return `${prefix}Mileage must be a number, for example 24000.`;
    if (/^-/.test(input)) return `${prefix}Mileage can’t be negative.`;
    if (/[.]/.test(input)) return `${prefix}Mileage must be a whole number, without decimal places.`;
    const mileage = Number(input.replace(/[,\s]/g, "").replace(/\s*miles?$/i, ""));
    if (mileage > 2_000_000) return `${prefix}Mileage can’t be more than 2,000,000.`;
    return `${prefix}Enter a whole-number mileage between 0 and 2,000,000.`;
  }
  if (field === "condition") {
    return `${prefix}Please choose Excellent, Good, or Fair.`;
  }
  if (field === "reference") {
    if (input.length < 3) return `${prefix}The booking reference is too short.`;
    if (input.length > 80) return `${prefix}The booking reference is too long.`;
    return `${prefix}Enter the booking reference as one value, exactly as shown on your confirmation.`;
  }
  if (field === "lastName") {
    if (/\d/.test(input)) return `${prefix}A surname can’t contain numbers.`;
    if (/[^A-Za-z' -]/.test(input)) return `${prefix}A surname can only contain letters, apostrophes, hyphens, and spaces.`;
    if (input.split(/\s+/).filter(Boolean).length > 4) return `${prefix}Please enter no more than four words for the surname.`;
    return `${prefix}Please enter a surname using letters, apostrophes, or hyphens only.`;
  }
  return `${prefix}${workflowPrompt({ currentField: field })}`;
}

function parseField(field, value) {
  const input = clean(value);
  if (!input) return null;
  if (field === "condition") {
    return ["excellent", "good", "fair"].includes(input.toLocaleLowerCase("en-GB"))
      ? input.toLocaleLowerCase("en-GB") : null;
  }
  if (field === "fullName") return parseName(input);
  if (field === "email") return /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(input) ? input : null;
  if (field === "phone") return parsePhone(input);
  if (field === "mileage") {
    const mileage = Number(input.replace(/[,\s]/g, "").replace(/\s*miles?$/i, ""));
    return Number.isInteger(mileage) && mileage >= 0 && mileage <= 2_000_000 ? mileage : null;
  }
  if (field === "registration") {
    const compact = input.toUpperCase().replace(/\s+/g, "");
    const ukRegistration = /^(?:[A-Z]{2}\d{2}[A-Z]{3}|[A-Z]\d{1,3}[A-Z]{3}|[A-Z]{3}\d{1,3}[A-Z]|[A-Z]{1,3}\d{1,4}|\d{1,4}[A-Z]{1,3}|\d{3}[DX]\d{3})$/;
    if (!ukRegistration.test(compact)) return null;
    return /^[A-Z]{2}\d{2}[A-Z]{3}$/.test(compact)
      ? `${compact.slice(0, 4)} ${compact.slice(4)}`
      : compact;
  }
  if (field === "reference") {
    return parseBookingReference(input);
  }
  if (field === "lastName") {
    const parts = input.split(/\s+/).filter(Boolean);
    return parts.length <= 4 && parts.every((part) => /^[A-Za-z][A-Za-z'-]{1,49}$/.test(part))
      ? parts.join(" ") : null;
  }
  return input;
}

function storageKey(conversationId) {
  return `${WORKFLOW_SESSION_PREFIX}${conversationId}`;
}

export function loadWorkflowSession(conversationId, storage = globalThis.sessionStorage) {
  if (!conversationId || !storage) return null;
  try {
    const value = JSON.parse(storage.getItem(storageKey(conversationId)) || "null");
    return value?.version === WORKFLOW_SESSION_VERSION && value.conversationId === conversationId ? value : null;
  } catch {
    return null;
  }
}

export function saveWorkflowSession(state, storage = globalThis.sessionStorage) {
  if (!state?.conversationId || !storage) return;
  storage.setItem(storageKey(state.conversationId), JSON.stringify(state));
}

export function clearWorkflowSession(conversationId, storage = globalThis.sessionStorage) {
  if (conversationId && storage) storage.removeItem(storageKey(conversationId));
}

export function createWorkflowSession(conversationId, kind, options = {}) {
  if (!WORKFLOW_CONVERSATION_SPECS[kind]) throw new Error(`Unsupported workflow: ${kind}`);
  const state = {
    version: WORKFLOW_SESSION_VERSION,
    conversationId,
    kind,
    status: "collecting",
    answers: { ...(options.answers || {}) },
    context: { ...(options.context || {}) },
    choices: { ...(options.choices || {}) },
    corrections: {},
    draftId: options.draftId || null,
    mode: options.mode || null,
    updatedAt: new Date().toISOString(),
  };
  return advanceWorkflow(state);
}

export function fieldDefinition(field) {
  const definition = definitionForField(field);
  return {
    ...definition,
    prompt: definition.prompt || `Please provide your ${definition.label || field}.`,
  };
}

function workflowFieldIsResolved(state, field) {
  return state.answers[field] !== undefined || state.context[field] !== undefined;
}

export function nextQuestionGroup(state) {
  const spec = WORKFLOW_CONVERSATION_SPECS[state?.kind];
  return (spec?.groups || []).find((group) => group.fields.some(
    (field) => !workflowFieldIsResolved(state, field),
  )) || null;
}

export function advanceWorkflow(state) {
  if (state?.editingField) {
    return {
      ...state,
      currentQuestionGroup: "edit",
      currentField: state.editingField,
      status: "collecting",
      updatedAt: new Date().toISOString(),
    };
  }
  const group = nextQuestionGroup(state);
  const currentField = group?.fields.find(
    (field) => !workflowFieldIsResolved(state, field),
  ) || null;
  return {
    ...state,
    currentQuestionGroup: group?.id || null,
    currentField,
    status: group ? "collecting" : "ready",
    updatedAt: new Date().toISOString(),
  };
}

function selectedAnswers(answers = {}, fields = []) {
  return Object.fromEntries(
    fields
      .filter((field) => answers[field] !== undefined)
      .map((field) => [field, answers[field]]),
  );
}

export function reconcileWorkflowActivation(previous, incoming, earlyAnswers = {}) {
  const targetSpec = WORKFLOW_CONVERSATION_SPECS[incoming?.kind];
  const targetFields = workflowSteps(targetSpec);
  const acceptedEarlyFields = targetFields.includes("fullName")
    ? [...targetFields, "firstName", "lastName"]
    : targetFields;
  const handoff = (targetSpec?.secureHandoffs || []).find(
    (candidate) => candidate.source === previous?.kind,
  );
  const sameWorkflow = previous?.kind === incoming?.kind;
  const retainedAnswers = sameWorkflow
    ? { ...(previous.answers || {}) }
    : handoff
      ? selectedAnswers(previous?.answers, handoff.transferFields)
      : {};
  const pausedWorkflow = sameWorkflow
    ? previous?.pausedWorkflow
    : handoff?.disposition === "consume"
      ? previous?.pausedWorkflow
      : previous
        ? pauseWorkflow(previous)
        : incoming?.pausedWorkflow;
  const initialField = incoming?.currentField || null;
  const state = advanceWorkflow({
    ...incoming,
    answers: {
      ...(incoming?.answers || {}),
      ...retainedAnswers,
      ...selectedAnswers(earlyAnswers, acceptedEarlyFields),
    },
    ...(pausedWorkflow ? { pausedWorkflow } : {}),
  });
  return {
    state,
    promptChanged: initialField !== state.currentField,
    disposition: handoff?.disposition || (sameWorkflow ? "continue" : "pause"),
  };
}

export function answerWorkflow(state, rawValue, explicitValue) {
  const field = state.currentField;
  if (!field) return { state, accepted: false, error: "This request is ready to review." };
  const definition = fieldDefinition(field);
  if (!definition.private) {
    throw new Error("Public capability fields must be handled by the server conversation");
  }
  const labelled = parseLabelledDetails(rawValue, state);
  const allowedFields = new Set(workflowSteps(WORKFLOW_CONVERSATION_SPECS[state.kind]));
  const retained = Object.fromEntries(
    Object.entries(labelled).filter(([key]) => (
      (allowedFields.has(key) && fieldDefinition(key).private)
      || ["firstName", "lastName"].includes(key)
    )),
  );
  const groupFields = new Set(
    questionGroup(WORKFLOW_CONVERSATION_SPECS[state.kind], state.currentQuestionGroup)?.fields || [field],
  );
  const retainedForGroup = Object.keys(retained).some(
    (key) => groupFields.has(key) || (field === "fullName" && ["firstName", "lastName"].includes(key)),
  );
  if (Object.keys(retained).length > 0 && retainedForGroup) {
    const nextState = { ...state, answers: { ...state.answers, ...retained } };
    // A labelled value can satisfy the field currently being edited. Clear the edit latch before
    // advancing or advanceWorkflow() will deliberately reopen that same field.
    delete nextState.editingField;
    const next = advanceWorkflow(nextState);
    return {
      state: next,
      accepted: true,
      display: privateAcknowledgement(Object.keys(retained)),
    };
  }
  const parsed = explicitValue ?? parseField(field, rawValue);
  if (parsed === null || parsed === undefined) {
    const count = (state.corrections[field] || 0) + 1;
    return {
      state: { ...state, corrections: { ...state.corrections, [field]: count } },
      accepted: false,
      error: validationError(field, rawValue, count > 1),
    };
  }
  const additions = typeof parsed === "object" && !Array.isArray(parsed) ? parsed : { [field]: parsed };
  const answers = { ...state.answers, ...additions };
  if (state.editingField === "lastName" && answers.firstName && answers.lastName) {
    answers.fullName = `${answers.firstName} ${answers.lastName}`;
  }
  const nextState = { ...state, answers };
  delete nextState.editingField;
  const next = advanceWorkflow(nextState);
  return { state: next, accepted: true, display: definition.private ? definition.mask : clean(rawValue) };
}

export function editWorkflowField(state, field) {
  const editable = new Set([
    ...workflowSteps(WORKFLOW_CONVERSATION_SPECS[state?.kind]),
    "lastName",
  ]);
  if (!state || !editable.has(field) || !fieldDefinition(field).private) return state;
  const answers = { ...state.answers };
  const context = { ...state.context };
  delete answers[field];
  delete context[field];
  if (field === "fullName") {
    for (const key of ["fullName", "firstName", "lastName"]) {
      delete answers[key];
      delete context[key];
    }
  } else if (field === "lastName") {
    delete answers.fullName;
    delete context.fullName;
  }
  return advanceWorkflow({
    ...state,
    answers,
    context,
    editingField: field,
    replacesDraftId: state.replacesDraftId || state.draftId || null,
    draftId: null,
  });
}

export function pauseWorkflow(state) {
  if (!state) return state;
  return {
    ...state,
    status: "paused",
    editingField: null,
    updatedAt: new Date().toISOString(),
  };
}

export function resumeWorkflow(state) {
  if (!state) return state;
  const next = { ...state };
  delete next.editingField;
  return advanceWorkflow(next);
}

function privateAcknowledgement(fields) {
  const normalized = [...new Set(fields.map((field) => (
    ["firstName", "lastName"].includes(field) && fields.includes("fullName") ? "fullName" : field
  )))];
  const labels = normalized.map((field) => fieldDefinition(field).label || field);
  const joined = labels.length > 1
    ? `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}`
    : labels[0] || "details";
  return `${joined.charAt(0).toLocaleUpperCase("en-GB")}${joined.slice(1)} entered privately`;
}

function parseLabelledDetails(value, state = {}) {
  const text = clean(value);
  const values = {};
  const email = text.match(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i);
  const phone = text.match(/(?:\+44|0)(?:[\s().-]*\d){9,11}/);
  const name = text.match(/\b(?:full\s+name|name)\s*(?::|\bis\b|\bto\b)\s*([A-Za-z][A-Za-z' -]+?)(?=\s*(?:,|;|\bemail\b|\bphone\b|$))/i);
  const registration = text.match(/\b(?:registration|reg)\s*:\s*([A-Za-z0-9 -]{2,20}?)(?=\s*(?:,|;|\bmileage\b|$))/i);
  const mileage = text.match(/\bmileage\s*:\s*([0-9][0-9,]*)/i);
  const reference = text.match(/\b(?:booking\s+)?(?:reference|ref)\s*:\s*([^\s,;]{3,80})(?=\s*(?:,|;|$))/i);
  const lastName = text.match(/\b(?:surname|last\s+name)\s*:\s*([A-Za-z][A-Za-z' -]{0,99}?)(?=\s*(?:,|;|\b(?:registration|reg|phone|mobile)\b|$))/i);
  if (email) values.email = email[0];
  if (phone) values.phone = parsePhone(phone[0]) || undefined;
  if (name) Object.assign(values, parseName(name[1]) || {});
  if (registration) values.registration = parseField("registration", registration[1]);
  if (mileage) values.mileage = parseField("mileage", mileage[1]);
  if (reference) values.reference = parseField("reference", reference[1]);
  if (lastName) values.lastName = parseField("lastName", lastName[1]);
  const labelledPatterns = {
    condition: /\bcondition\s*:\s*([^,;]+)/i,
  };
  Object.entries(labelledPatterns).forEach(([field, pattern]) => {
    const match = text.match(pattern);
    if (!match) return;
    const parsed = parseField(field, match[1]);
    if (parsed !== null && parsed !== undefined) values[field] = parsed;
  });
  const currentGroupFields = questionGroup(
    WORKFLOW_CONVERSATION_SPECS[state.kind],
    state.currentQuestionGroup,
  )?.fields || [];
  if (state.currentField === "fullName" && !values.fullName) {
    const residual = text
      .replace(email?.[0] || "", "")
      .replace(phone?.[0] || "", "")
      .replace(/\b(?:email|e-mail|phone|mobile|name)\s*:?/gi, "")
      .replace(/[;,]+/g, " ")
      .trim();
    const parsed = parseName(residual);
    if (parsed) Object.assign(values, parsed);
  }
  return Object.fromEntries(Object.entries(values).filter(([, item]) => item !== undefined && item !== null));
}

export function workflowPrompt(state) {
  if (!state?.currentField) return "Please review the details below.";
  if (state.editingField === "lastName") return "What’s the correct surname?";
  return fieldDefinition(state.currentField).prompt;
}

export function workflowQuickReplies(state) {
  if (!state?.currentField) return [];
  return fieldChoices(state.currentField);
}

export function workflowIsPrivate(state) {
  const group = questionGroup(
    WORKFLOW_CONVERSATION_SPECS[state?.kind],
    state?.currentQuestionGroup,
  );
  return Boolean((group?.fields || [state?.currentField]).some(
    (field) => field && state.answers?.[field] === undefined
      && !workflowFieldIsResolved(state, field) && fieldDefinition(field).private,
  ));
}

export function workflowLocalDetails(state) {
  const all = { ...state.context, ...state.answers };
  return Object.fromEntries(Object.entries(all).filter(([, value]) => value !== undefined && value !== null && value !== ""));
}

function reviewDisplayValue(field, value) {
  const definition = fieldDefinition(field);
  const choice = fieldChoices(field).find((item) => item.value === value);
  if (choice) return choice.label;
  if (definition.reviewFormat === "mileage" && Number.isFinite(Number(value))) {
    return `${Number(value).toLocaleString("en-GB")} miles`;
  }
  return String(value);
}

export function workflowReviewDetails(state) {
  const spec = WORKFLOW_CONVERSATION_SPECS[state?.kind];
  const values = workflowLocalDetails(state);
  return workflowSteps(spec)
    .filter((field) => fieldDefinition(field).private && values[field] !== undefined)
    .map((field) => {
      const definition = fieldDefinition(field);
      return {
        field,
        label: definition.reviewLabel
          || `${definition.label.charAt(0).toLocaleUpperCase("en-GB")}${definition.label.slice(1)}`,
        value: reviewDisplayValue(field, values[field]),
      };
    });
}

export function buildWorkflowSubmission(state) {
  const values = workflowLocalDetails(state);
  const contact = {
    firstName: values.firstName,
    lastName: values.lastName,
    email: values.email,
    phone: values.phone,
  };
  const pick = (...keys) => Object.fromEntries(keys.filter((key) => values[key] !== undefined).map((key) => [key, values[key]]));
  const replacesDraftId = state.replacesDraftId || state.draftId || null;
  const submission = (method, payload) => ({ method, payload, replacesDraftId });
  if (state.kind === "test_drive") return submission("prepareTestDrive", { ...pick("slotId", "vehicleId"), ...contact });
  if (state.kind === "workshop_booking") return submission("prepareWorkshopBooking", { ...pick("slotId", "serviceTypeId", "dealershipId", "registration", "mileage", "notes"), ...contact });
  if (state.kind === "sales_enquiry") return submission("prepareSalesEnquiry", { ...pick("dealershipId", "enquiryType", "message", "vehicleId"), ...contact });
  if (state.kind === "vehicle_interest") return submission("prepareVehicleInterest", { ...pick("vehicleId", "notes"), ...contact });
  if (state.kind === "callback") return submission("prepareCallback", { ...pick("dealershipId", "department", "reason", "preferredTime", "vehicleId"), ...contact });
  if (state.kind === "dealership_message") return submission("prepareDealershipMessage", { ...pick("dealershipId", "department", "subject", "message", "preferredContactMethod"), ...contact });
  if (state.kind === "part_exchange_estimate") return { method: "estimatePartExchange", payload: pick("registration", "mileage", "condition") };
  if (state.kind === "part_exchange") return submission("preparePartExchange", { ...pick("dealershipId", "registration", "mileage", "condition"), ...contact });
  if (state.kind === "booking_lookup") return { method: "lookupWorkshopBooking", payload: { ...pick("reference", "lastName", "registration", "phone"), mode: state.mode || "lookup" } };
  throw new Error(`Unsupported workflow: ${state.kind}`);
}

export function workflowFromView(conversationId, viewType, view = {}) {
  if (viewType === "secure_input") {
    const sourceViewType = view.sourceViewType;
    if (!["draft", "private_booking_lookup", "part_exchange_estimate_form"].includes(sourceViewType)) return null;
    return workflowFromView(conversationId, sourceViewType, view);
  }
  if (viewType === "private_booking_lookup") {
    return createWorkflowSession(conversationId, "booking_lookup", { mode: view.mode || "lookup" });
  }
  if (viewType === "part_exchange_estimate_form") {
    return createWorkflowSession(conversationId, "part_exchange_estimate", {
      answers: { ...(view.values || {}) },
    });
  }
  if (viewType !== "draft") return null;
  const kind = view.kind;
  if (!WORKFLOW_CONVERSATION_SPECS[kind]) return null;
  const answers = { ...(view.summary || {}) };
  const context = { ...(view.context || {}) };
  ["vehicleId", "serviceTypeId", "dealershipId", "slotId"].forEach((key) => {
    if (answers[key]) { context[key] = answers[key]; delete answers[key]; }
  });
  return createWorkflowSession(conversationId, kind, { answers, context, draftId: view.draftId });
}

export function extractEarlyContact(text) {
  let sanitized = String(text || "");
  const values = {};
  const email = sanitized.match(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i);
  const phone = sanitized.match(/(?:\+44|0)(?:[\s().-]*\d){9,11}/);
  const named = sanitized.match(/\b(?:my name is|name\s*:)\s*([A-Za-z][A-Za-z' -]+?)(?=\s*(?:,|\.|\band\b|\bemail\b|\bphone\b|$))/i);
  if (email) { values.email = email[0]; sanitized = sanitized.replace(email[0], "[email shared securely]"); }
  if (phone) { values.phone = parsePhone(phone[0]) || phone[0]; sanitized = sanitized.replace(phone[0], "[phone shared securely]"); }
  const namePrefix = named ? sanitized.slice(Math.max(0, named.index - 24), named.index) : "";
  if (named && !/(?:model|dealership|service|company)\s*$/i.test(namePrefix)) {
    const parsed = parseName(named[1]);
    if (parsed) Object.assign(values, parsed);
    sanitized = sanitized.replace(named[0], "[name shared securely]");
  }
  const cancellationIntent = /\b(?:cancel|change|amend|find|lookup|look up)\b[\s\S]{0,40}\b(?:booking|appointment)\b|\bbooking\b[\s\S]{0,40}\b(?:cancel|change|amend|find|lookup|look up)\b/i.test(sanitized);
  const workshopIntent = /\b(?:book|booking|appointment|mot|service|workshop)\b/i.test(sanitized);
  const partExchangeIntent = /\b(?:part[ -]?exchange|trade[ -]?in|valuation)\b/i.test(sanitized);
  if (cancellationIntent) {
    // Detect the value from booking language, not from a particular platform ID shape.
    const labelledReference = sanitized.match(
      /\b(?:booking\s+)?(?:reference|ref)\s*(?::|is)?\s*([^\s,;]{3,80})/i,
    );
    const bookingReference = sanitized.match(
      /\bbooking\s+([^\s,;]{3,80}?)(?=\s+(?:for|under|surname)\b|\s*[,;.]|$)/i,
    );
    const reference = labelledReference || bookingReference;
    const surname = sanitized.match(/\b(?:surname|last\s+name)\s*(?::|is)?\s*([A-Za-z][A-Za-z'-]{1,49}(?:\s+[A-Za-z][A-Za-z'-]{1,49}){0,3})\b/i);
    if (reference) {
      const parsed = parseBookingReference(reference[1]);
      if (parsed) {
        values.reference = parsed;
        sanitized = sanitized.replace(reference[1], "[booking reference shared securely]");
      }
    }
    if (surname) {
      const parsed = parseField("lastName", surname[1]);
      if (parsed) {
        values.lastName = parsed;
        sanitized = sanitized.replace(surname[1], "[surname shared securely]");
      }
    }
  }
  if (workshopIntent || partExchangeIntent) {
    const registration = sanitized.match(/\b(?:registration|reg)\s*(?::|is)?\s*([A-Z]{2}\d{2}\s*[A-Z]{3})\b/i);
    const mileage = sanitized.match(/\bmileage\s*(?::|is)?\s*([0-9][0-9,]*(?:\s*miles?)?)\b/i);
    if (registration) {
      const parsed = parseField("registration", registration[1]);
      if (parsed) {
        values.registration = parsed;
        sanitized = sanitized.replace(registration[1], "[registration shared securely]");
      }
    }
    if (mileage) {
      const parsed = parseField("mileage", mileage[1]);
      if (parsed !== null) {
        values.mileage = parsed;
        sanitized = sanitized.replace(mileage[1], "[mileage shared securely]");
      }
    }
  }
  if (partExchangeIntent) {
    const condition = sanitized.match(/\bcondition\s*(?::|is)?\s*(excellent|good|fair)\b/i);
    if (condition) {
      values.condition = condition[1].toLocaleLowerCase("en-GB");
      sanitized = sanitized.replace(condition[1], "[condition shared securely]");
    }
  }
  return { sanitizedText: sanitized.replace(/\s+/g, " ").trim(), values };
}
