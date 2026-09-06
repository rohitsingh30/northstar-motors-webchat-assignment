import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  answerWorkflow,
  buildWorkflowSubmission,
  clearWorkflowSession,
  editWorkflowField,
  extractEarlyContact,
  fieldDefinition,
  isPublicSteeringMessage,
  loadWorkflowSession,
  pauseWorkflow,
  privateCorrectionField,
  reconcileWorkflowActivation,
  requestedPrivateCorrectionField,
  resumeWorkflow,
  saveWorkflowSession,
  workflowFromView,
  workflowIsPrivate,
  workflowPrompt,
  workflowReviewDetails,
} from "../../webchat/widget/core/workflow-conversation.js";
import { FIELD_DEFINITIONS } from "../../webchat/widget/core/workflow-fields.js";
import {
  WORKFLOW_CONVERSATION_SPECS,
  workflowSteps,
} from "../../webchat/widget/core/workflow-specs.js";

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}

function secureDraft(kind, summary, extra = {}) {
  return workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "draft",
    kind,
    draftId: "draft-a",
    secureInputReady: true,
    summary,
    ...extra,
  });
}

test("protected state is versioned per conversation and clears explicitly", () => {
  const storage = new MemoryStorage();
  const state = secureDraft("vehicle_interest", { vehicleId: "veh-001", notes: null });
  saveWorkflowSession(state, storage);
  assert.equal(loadWorkflowSession("conversation-a", storage).currentField, "fullName");
  assert.equal(loadWorkflowSession("conversation-b", storage), null);
  clearWorkflowSession("conversation-a", storage);
  assert.equal(loadWorkflowSession("conversation-a", storage), null);
});

test("a server-issued secure activation skips every public capability field", () => {
  const state = secureDraft("sales_enquiry", {
    vehicleId: "veh-001",
    dealershipId: "northstar-manchester",
    enquiryType: "finance",
    message: "Please explain the deposit options.",
  });

  assert.equal(state.currentField, "fullName");
  assert.equal(state.currentQuestionGroup, "contact");
  assert.equal(workflowIsPrivate(state), true);
  assert.equal(
    workflowPrompt(state),
    "Thanks. What’s your first and last name? For example, Alex Morgan.",
  );
});

test("workshop protected capture asks only for private vehicle and contact values", () => {
  let state = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-manchester",
    slotId: "ws-slot-0001",
    notes: null,
  });

  assert.equal(
    workflowPrompt(state),
    "What’s your vehicle registration? For example, AB12 CDE.",
  );
  state = answerWorkflow(state, "Registration: AB12 CDE; Mileage: 24,000").state;
  assert.equal(
    workflowPrompt(state),
    "Thanks. What’s your first and last name? For example, Alex Morgan.",
  );
});

test("workshop private answers advance one field at a time with truthful masks", () => {
  let state = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-bolton",
    slotId: "ws-slot-0001",
  });

  const registration = answerWorkflow(state, "AB12 CDE");
  assert.equal(registration.accepted, true);
  assert.equal(registration.display, "Vehicle registration entered privately");
  assert.equal(registration.state.currentField, "mileage");

  const mileage = answerWorkflow(registration.state, "24000");
  assert.equal(mileage.accepted, true);
  assert.equal(mileage.display, "Current mileage entered privately");
  assert.equal(mileage.state.currentField, "fullName");
});

test("ordinary words are not accepted or displayed as a vehicle registration", () => {
  const state = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-bolton",
    slotId: "ws-slot-0001",
  });

  for (const value of ["tomorrow maybe", "interim", "hello there"]) {
    const result = answerWorkflow(state, value);
    assert.equal(result.accepted, false);
    assert.equal(result.state.currentField, "registration");
  }
});

test("labelled private details acknowledge the fields actually captured", () => {
  const state = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-bolton",
    slotId: "ws-slot-0001",
  });

  const result = answerWorkflow(state, "Registration: AB12 CDE; Mileage: 24,000");
  assert.equal(result.accepted, true);
  assert.equal(
    result.display,
    "Vehicle registration and current mileage entered privately",
  );
  assert.equal(result.state.currentField, "fullName");
});

test("part-exchange estimate asks for one protected answer at a time", () => {
  const state = workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "part_exchange_estimate_form",
    kind: "part_exchange_estimate",
    secureInputReady: true,
    secureFields: ["registration", "mileage", "condition"],
    values: {},
  });

  assert.equal(
    workflowPrompt(state),
    "What’s your vehicle registration? For example, AB12 CDE.",
  );
});

test("part-exchange continuation consumes the estimate and asks only for missing contact", () => {
  let estimate = workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "part_exchange_estimate_form",
    kind: "part_exchange_estimate",
    secureInputReady: true,
    secureFields: ["registration", "mileage", "condition"],
    values: {},
  });
  estimate = answerWorkflow(estimate, "AB12 CDE").state;
  estimate = answerWorkflow(estimate, "25000").state;
  estimate = answerWorkflow(estimate, "good").state;
  estimate = {
    ...estimate,
    status: "estimate_ready",
    pausedWorkflow: pauseWorkflow(secureDraft("callback", {
      dealershipId: "northstar-stockport",
      department: "sales",
      reason: "Existing request",
    })),
  };
  const incoming = secureDraft("part_exchange", {
    dealershipId: "northstar-bolton",
  });

  const activation = reconcileWorkflowActivation(estimate, incoming);

  assert.equal(activation.promptChanged, true);
  assert.equal(activation.state.currentField, "fullName");
  assert.equal(
    workflowPrompt(activation.state),
    "Thanks. What’s your first and last name? For example, Alex Morgan.",
  );
  assert.equal(activation.state.answers.registration, "AB12 CDE");
  assert.equal(activation.state.answers.mileage, 25000);
  assert.equal(activation.state.answers.condition, "good");
  assert.equal(activation.state.pausedWorkflow.kind, "callback");
  assert.equal(activation.state.pausedWorkflow.pausedWorkflow, undefined);
});

test("unrelated secure workflow activation still pauses rather than consumes active work", () => {
  const current = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-bolton",
    slotId: "ws-slot-0001",
  });
  const incoming = secureDraft("sales_enquiry", {
    dealershipId: "northstar-stockport",
    enquiryType: "general",
    message: "Please contact me.",
  });

  const activation = reconcileWorkflowActivation(current, incoming);

  assert.equal(activation.state.currentField, "fullName");
  assert.equal(activation.state.pausedWorkflow.kind, "workshop_booking");
  assert.equal(activation.state.pausedWorkflow.status, "paused");
});

test("protected review details derive every material field from the workflow schema", () => {
  const incoming = secureDraft("part_exchange", {
    dealershipId: "northstar-bolton",
  });
  const activation = reconcileWorkflowActivation(
    {
      ...workflowFromView("conversation-a", "part_exchange_estimate_form", {
        kind: "part_exchange_estimate",
        secureInputReady: true,
        values: {
          registration: "AB12 CDE",
          mileage: 25000,
          condition: "good",
        },
      }),
      status: "estimate_ready",
    },
    incoming,
    {
      fullName: "Alex Morgan",
      firstName: "Alex",
      lastName: "Morgan",
      email: "alex@example.com",
      phone: "07700900123",
    },
  );

  assert.deepEqual(workflowReviewDetails(activation.state), [
    { field: "registration", label: "Registration", value: "AB12 CDE" },
    { field: "mileage", label: "Mileage", value: "25,000 miles" },
    { field: "condition", label: "Condition", value: "Good" },
    { field: "fullName", label: "Full name", value: "Alex Morgan" },
    { field: "email", label: "Email", value: "alex@example.com" },
    { field: "phone", label: "Phone", value: "07700900123" },
  ]);
});

test("every protected workflow review is complete by construction", () => {
  for (const [kind, spec] of Object.entries(WORKFLOW_CONVERSATION_SPECS)) {
    const protectedFields = spec.groups
      .flatMap((group) => group.fields)
      .filter((field) => FIELD_DEFINITIONS[field]?.private);
    const answers = Object.fromEntries(protectedFields.map((field) => [field, `${field}-value`]));
    const details = workflowReviewDetails({ kind, answers, context: {} });
    assert.deepEqual(
      details.map((item) => item.field),
      protectedFields,
      kind,
    );
  }
});

test("secure handoffs transfer only fields declared by both protected workflow schemas", () => {
  Object.entries(WORKFLOW_CONVERSATION_SPECS).forEach(([targetKind, targetSpec]) => {
    const targetFields = new Set(workflowSteps(targetSpec));
    (targetSpec.secureHandoffs || []).forEach((handoff) => {
      const sourceSpec = WORKFLOW_CONVERSATION_SPECS[handoff.source];
      assert.ok(sourceSpec, `${targetKind} has an unknown handoff source`);
      assert.ok(["consume", "pause"].includes(handoff.disposition));
      const sourceFields = new Set(workflowSteps(sourceSpec));
      handoff.transferFields.forEach((field) => {
        assert.ok(sourceFields.has(field), `${field} is absent from ${handoff.source}`);
        assert.ok(targetFields.has(field), `${field} is absent from ${targetKind}`);
        assert.equal(fieldDefinition(field).private, true);
      });
    });
  });
});

test("booking verification accepts all protected values together", () => {
  let state = workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "private_booking_lookup",
    kind: "booking_lookup",
    mode: "lookup",
    secureInputReady: true,
  });
  assert.equal(
    workflowPrompt(state),
    "What’s your booking reference? It usually looks like WORK-12345 and is shown in your confirmation email.",
  );

  state = answerWorkflow(
    state,
    "Reference: WORK-10001; Surname: Taylor; Registration: AB12 CDE; Phone: 07700 900123",
  ).state;
  assert.equal(state.status, "ready");
  assert.deepEqual(buildWorkflowSubmission(state), {
    method: "lookupWorkshopBooking",
    payload: {
      reference: "WORK-10001",
      lastName: "Taylor",
      registration: "AB12 CDE",
      phone: "07700900123",
      mode: "lookup",
    },
  });
});

test("a partial protected reply retains valid values and asks only for missing details", () => {
  let state = workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "private_booking_lookup",
    kind: "booking_lookup",
    mode: "lookup",
    secureInputReady: true,
  });
  state = answerWorkflow(state, "Reference: WORK-10001; Surname: Taylor").state;

  assert.equal(
    workflowPrompt(state),
    "What’s your vehicle registration? For example, AB12 CDE.",
  );
});

test("early contact details are removed from AI-visible text and retained in session memory", () => {
  const result = extractEarlyContact(
    "Book this. My name is Rohit Singh, email rohit@example.com and phone +44 7123 456789",
  );
  assert.doesNotMatch(result.sanitizedText, /rohit@example\.com|7123 456789|Rohit Singh/);
  assert.deepEqual(result.values, {
    email: "rohit@example.com",
    phone: "07123456789",
    fullName: "Rohit Singh",
    firstName: "Rohit",
    lastName: "Singh",
  });
});

test("early protected workflow values are masked only for a relevant workflow intent", () => {
  const cancellation = extractEarlyContact(
    "Please cancel booking appointment_C97EF8BB for surname Example.",
  );
  assert.doesNotMatch(cancellation.sanitizedText, /appointment_C97EF8BB|Example/);
  assert.deepEqual(cancellation.values, { reference: "appointment_C97EF8BB", lastName: "Example" });

  const workshop = extractEarlyContact(
    "Book an MOT for registration AB12 CDE with mileage 25,000.",
  );
  assert.doesNotMatch(workshop.sanitizedText, /AB12 CDE|25,000/);
  assert.deepEqual(workshop.values, { registration: "AB12 CDE", mileage: 25000 });

  const unrelated = extractEarlyContact("The reference appointment_C97EF8BB appears in this example.");
  assert.match(unrelated.sanitizedText, /appointment_C97EF8BB/);
  assert.deepEqual(unrelated.values, {});
});

test("product attribute names are not mistaken for customer names", () => {
  const product = extractEarlyContact(
    "Model name: Range Rover Evoque. Show me available ones under £40,000.",
  );
  assert.match(product.sanitizedText, /Range Rover Evoque/);
  assert.deepEqual(product.values, {});

  const person = extractEarlyContact("Name: Alex Example, please call me");
  assert.doesNotMatch(person.sanitizedText, /Alex Example/);
  assert.equal(person.values.fullName, "Alex Example");
});

test("booking verification rejects prose-shaped private answers", () => {
  let state = workflowFromView("conversation-a", "secure_input", {
    sourceViewType: "private_booking_lookup",
    kind: "booking_lookup",
    mode: "cancel",
    secureInputReady: true,
  });

  const question = answerWorkflow(state, "Actually, what time does Bolton service close today?");
  assert.equal(question.accepted, false);
  assert.equal(question.state.currentField, "reference");

  state = answerWorkflow(state, "WORK-10001").state;
  const command = answerWorkflow(state, "Cancel this process.");
  assert.equal(command.accepted, false);
  assert.equal(command.state.currentField, "lastName");
});

test("invalid protected values fail locally without entering the ordinary conversation", () => {
  let state = secureDraft("vehicle_interest", { vehicleId: "veh-001", notes: null });
  state = answerWorkflow(state, "Rohit Singh").state;
  const invalid = answerWorkflow(state, "not-an-email");

  assert.equal(invalid.accepted, false);
  assert.equal(invalid.state.currentField, "email");
  assert.match(invalid.error, /missing an @ sign/i);
});

test("repeating a completed unlabelled name cannot masquerade as the requested email", () => {
  let state = secureDraft("callback", {
    dealershipId: "northstar-stockport",
    department: "parts",
    reason: "It’s about parts.",
  });
  state = answerWorkflow(state, "Alex Example").state;

  const repeated = answerWorkflow(state, "Alex Example");

  assert.equal(repeated.accepted, false);
  assert.equal(repeated.state.currentField, "email");
  assert.match(repeated.error, /email address/i);
});

test("a surname can be corrected locally without exposing or re-entering other contact fields", () => {
  let state = secureDraft("callback", {
    dealershipId: "northstar-stockport",
    department: "parts",
    reason: "It’s about parts.",
  });
  state = answerWorkflow(state, "Alex Wrong").state;
  state = answerWorkflow(state, "alex@example.com").state;
  state = answerWorkflow(state, "07700 900123").state;
  assert.equal(state.status, "ready");

  state = editWorkflowField(state, "lastName");
  assert.equal(state.currentField, "lastName");
  assert.equal(workflowPrompt(state), "What’s the correct surname?");
  assert.equal(state.answers.email, "alex@example.com");
  assert.equal(state.answers.phone, "07700900123");

  state = answerWorkflow(state, "Correct").state;
  assert.equal(state.status, "ready");
  assert.equal(state.answers.fullName, "Alex Correct");
  assert.equal(buildWorkflowSubmission(state).payload.lastName, "Correct");
});

test("protected collection can pause and resume without dropping private values", () => {
  let state = secureDraft("callback", {
    dealershipId: "northstar-stockport",
    department: "parts",
    reason: "It’s about parts.",
  });
  state = answerWorkflow(state, "Alex Example").state;
  const paused = pauseWorkflow(state);

  assert.equal(paused.status, "paused");
  assert.equal(paused.answers.email, undefined);
  assert.equal(paused.answers.lastName, "Example");

  const resumed = resumeWorkflow(paused);
  assert.equal(resumed.status, "collecting");
  assert.equal(resumed.currentField, "email");
  assert.equal(resumed.answers.lastName, "Example");
});

test("natural correction and steering language is separated before private parsing", () => {
  assert.equal(privateCorrectionField("I entered the wrong surname"), "lastName");
  assert.equal(privateCorrectionField("I eneted wrong surname"), "lastName");
  assert.equal(privateCorrectionField("my email is wrong"), "email");
  assert.equal(isPublicSteeringMessage("Actually, what time does Stockport close?"), true);
  assert.equal(isPublicSteeringMessage("Change the dealership to Bolton"), true);
  assert.equal(isPublicSteeringMessage("Use Bolton instead"), true);
  assert.equal(isPublicSteeringMessage("Switch to a BMW X5 instead"), true);
  assert.equal(isPublicSteeringMessage("Alex Example"), false);
  assert.equal(isPublicSteeringMessage("Can Yilmaz"), false);
  assert.equal(isPublicSteeringMessage("alex@example.com"), false);
});

test("a correction-shaped answer for the current private field is consumed as its value", () => {
  let state = secureDraft("test_drive", {
    vehicleId: "veh-001",
    slotId: "td-slot-0001",
  });

  assert.equal(requestedPrivateCorrectionField(state, "My name is Alex Morgan"), null);
  const answered = answerWorkflow(state, "My name is Alex Morgan");
  assert.equal(answered.accepted, true);
  assert.equal(answered.state.answers.fullName, "Alex Morgan");
  assert.equal(answered.state.currentField, "email");

  state = editWorkflowField({
    ...answered.state,
    answers: {
      ...answered.state.answers,
      email: "alex@example.com",
      phone: "07700900123",
    },
    status: "awaiting_confirmation",
    draftId: "draft-review-1",
  }, "fullName");
  assert.equal(state.replacesDraftId, "draft-review-1");
  assert.equal(requestedPrivateCorrectionField(state, "Change my name to Alex Taylor"), null);
  const replacement = answerWorkflow(state, "Change my name to Alex Taylor");
  assert.equal(replacement.accepted, true);
  assert.equal(replacement.state.answers.fullName, "Alex Taylor");
  assert.equal(replacement.state.status, "ready");
  assert.equal(buildWorkflowSubmission(replacement.state).replacesDraftId, "draft-review-1");
});

test("invalid phone answers explain the specific problem on every retry", () => {
  let state = secureDraft("callback", {
    dealershipId: "northstar-stockport",
    department: "sales",
    reason: "used_vehicle",
  });
  state = answerWorkflow(state, "Alex Example").state;
  state = answerWorkflow(state, "alex@example.com").state;

  const tooShort = answerWorkflow(state, "07700 123");
  assert.equal(tooShort.accepted, false);
  assert.match(tooShort.error, /too few digits/i);
  assert.doesNotMatch(tooShort.error, /cancel/i);

  const wrongPrefix = answerWorkflow(tooShort.state, "5551234567");
  assert.equal(wrongPrefix.accepted, false);
  assert.match(wrongPrefix.error, /UK 01 landline or 07 mobile number/i);
  assert.doesNotMatch(wrongPrefix.error, /cancel/i);
});

test("every protected field gives actionable validation guidance", () => {
  const contact = secureDraft("callback", {
    dealershipId: "northstar-stockport",
    department: "sales",
    reason: "used_vehicle",
  });
  assert.match(answerWorkflow(contact, "Alex").error, /first and last name/i);

  const email = answerWorkflow(contact, "Alex Example").state;
  assert.match(answerWorkflow(email, "alex.example.com").error, /missing an @ sign/i);

  const phone = answerWorkflow(email, "alex@example.com").state;
  assert.match(answerWorkflow(phone, "07700 123").error, /too few digits/i);

  const vehicle = workflowFromView("conversation-a", "part_exchange_estimate_form", {
    kind: "part_exchange_estimate",
    secureInputReady: true,
    values: {},
  });
  assert.match(answerWorkflow(vehicle, "AB!").error, /letters, numbers, and spaces/i);

  const mileage = answerWorkflow(vehicle, "AB12 CDE").state;
  assert.match(answerWorkflow(mileage, "-1").error, /can’t be negative/i);

  const condition = answerWorkflow(mileage, "24000").state;
  assert.match(answerWorkflow(condition, "average").error, /Excellent, Good, or Fair/i);

  const booking = workflowFromView("conversation-a", "private_booking_lookup", {
    kind: "booking_lookup",
    mode: "lookup",
    secureInputReady: true,
  });
  assert.match(answerWorkflow(booking, "not a booking reference").error, /one value/i);

  const surname = answerWorkflow(booking, "any-platform-reference_123").state;
  assert.match(answerWorkflow(surname, "Smith2").error, /can’t contain numbers/i);
});

test("booking references are opaque platform identifiers", () => {
  const booking = workflowFromView("conversation-a", "private_booking_lookup", {
    kind: "booking_lookup",
    mode: "amend",
    secureInputReady: true,
  });

  for (const reference of ["WORK-C97EF8BB", "12345", "vendor_ref-2026_AZ"]) {
    const result = answerWorkflow(booking, reference);
    assert.equal(result.accepted, true, reference);
    assert.equal(result.state.answers.reference, reference);
    assert.equal(result.state.currentField, "lastName");
  }
});

test("protected capture cannot accept or alter public capability fields", () => {
  let state = secureDraft("workshop_booking", {
    serviceTypeId: "mot",
    dealershipId: "northstar-manchester",
    slotId: "ws-slot-0001",
    notes: null,
  });
  state = answerWorkflow(
    state,
    "Registration: AB12 CDE; Mileage: 24,000; Notes: replace the brakes",
  ).state;

  assert.equal(state.answers.notes, null);
  assert.equal(state.answers.registration, "AB12 CDE");
  assert.equal(state.answers.mileage, 24000);
});

test("one labelled protected reply creates one strictly mapped draft payload", () => {
  let state = secureDraft("test_drive", {
    vehicleId: "veh-001",
    slotId: "td-slot-0001",
  });
  state = answerWorkflow(
    state,
    "Name: Rohit Singh; Email: rohit@example.com; Phone: +44 7123 456789",
  ).state;

  assert.equal(state.status, "ready");
  assert.deepEqual(buildWorkflowSubmission(state), {
    method: "prepareTestDrive",
    replacesDraftId: "draft-a",
    payload: {
      slotId: "td-slot-0001",
      vehicleId: "veh-001",
      firstName: "Rohit",
      lastName: "Singh",
      email: "rohit@example.com",
      phone: "07123456789",
    },
  });
});

test("the browser collector schema contains protected fields only", () => {
  const fields = Object.values(WORKFLOW_CONVERSATION_SPECS)
    .flatMap((spec) => spec.groups)
    .flatMap((group) => group.fields);

  assert.ok(fields.length > 0);
  fields.forEach((field) => assert.equal(FIELD_DEFINITIONS[field]?.private, true, field));
  for (const publicField of [
    "vehicleId", "serviceTypeId", "dealershipId", "slotId", "notes", "message",
    "reason", "preferredTime", "subject", "preferredContactMethod", "requestedChanges",
  ]) {
    assert.equal(FIELD_DEFINITIONS[publicField], undefined, publicField);
  }
});

test("the widget does not interpret public part-exchange follow-up language", async () => {
  const source = await readFile(
    new URL("../../webchat/widget/webchat.js", import.meta.url),
    "utf8",
  );
  const viewSource = await readFile(
    new URL("../../webchat/widget/views/message.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(source, /continuePartExchangeEstimate/);
  assert.doesNotMatch(source, /proceed\|go ahead\|continue/);
  assert.doesNotMatch(source, /appointmentPreferenceValue|workflowSlotChoices|requestedChanges/);
  assert.doesNotMatch(source, /getWorkshopOptions|getWorkshopAmendmentOptions|getTestDriveOptions/);
  assert.doesNotMatch(source, /data-workflow-(?:control|edit)/);
  assert.doesNotMatch(viewSource, /trustedChoicePanel|workflowShowMore/);
});

test("retired form and slot-picker UI cannot return through stale styles", async () => {
  const styles = await readFile(
    new URL("../../webchat/widget/webchat.css", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(
    styles,
    /webchat-(?:workflow-form|slot-picker|date-button|time-button|details-grid|confirmation-actions|booking-actions|private-lookup|collection-chip)/,
  );
});

test("busy state gates overlapping controls while New chat cancels the active request", async () => {
  const source = await readFile(
    new URL("../../webchat/widget/webchat.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /newChat\.disabled = false/);
  assert.match(source, /requestEpoch \+= 1;\s+api\.cancelPendingRequests\(\)/);
  assert.match(source, /localStorage\.removeItem\(CONVERSATION_KEY\);\s+replaceMessages\(\[\]\)/);
  assert.doesNotMatch(source, /historyToggle/);
  assert.doesNotMatch(source, /async function startNewConversation\(\) \{\s+if \(busy\) return false/);
  assert.match(source, /await refreshHistory\(operationEpoch\)\.catch\(\(\) => null\)/);
  assert.match(source, /dataset\?\.requestFailureKey === key/);
  assert.match(source, /const \{ item \} = appendRequestError\(\s*error,/);
  assert.match(source, /appendRetryAction\(item, "retry-turn", retryId\)/);
  assert.match(source, /appendRetryAction\(item, "retry-workflow"\)/);
  assert.doesNotMatch(
    source,
    /catch \(error\) \{[\s\S]*?I couldn.t prepare that request[\s\S]*?if \(activeWorkflow\) renderWorkflowPrompt\(\)/,
  );
});

test("recent chats is visible only on the empty new-chat surface", async () => {
  const widget = await readFile(
    new URL("../../webchat/widget/northstar-chat-widget.js", import.meta.url),
    "utf8",
  );
  const controller = await readFile(
    new URL("../../webchat/widget/webchat.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(widget, /webchat-history-toggle|>Recent<|>Recent<\/button>/);
  assert.match(widget, /id="webchat-history-panel"[^>]*aria-label="Conversation history"/);
  assert.match(controller, /historyPanel\.hidden = messages\.length > 0/);
  assert.match(controller, /prepared\.role === "user"\) historyPanel\.hidden = true/);
});
