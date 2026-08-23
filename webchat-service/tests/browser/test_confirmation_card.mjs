import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.id = "";
    this.textContent = "";
    this.type = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelector(selector) {
    const nameMatch = selector.match(/^\[name="(.+)"\]$/);
    const matches = (element) => nameMatch && element.name === nameMatch[1];
    for (const child of this.children) {
      if (matches(child)) return child;
      const nested = child?.querySelector?.(selector);
      if (nested) return nested;
    }
    return null;
  }
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const { receiptCard, renderWorkflowCard } = await import("../../webchat/widget/views/message.js");

function childWithClass(element, className) {
  return element.children.find((child) => child.className === className);
}

function summaryValues(card) {
  const summary = childWithClass(card, "webchat-enquiry-summary");
  return Object.fromEntries(summary.children.map((row) => [
    row.children[0].textContent,
    row.children[1].textContent,
  ]));
}

test("sales enquiry form can be cancelled into a specific receipt", () => {
  const formCard = renderWorkflowCard({
    draftId: "draft-sales-form-001",
    kind: "sales_enquiry",
    status: "collecting",
    summary: {},
    dealerships: [
      { id: "northstar-stockport", name: "Northstar Stockport", town: "Stockport" },
    ],
  });
  const form = formCard.children.find((child) => child.tagName === "FORM");
  const actions = childWithClass(form, "webchat-inline-actions");
  const cancel = actions.children[1];

  assert.equal(cancel.textContent, "Cancel");
  assert.equal(cancel.dataset.chatAction, "cancel-workflow-form");
  assert.equal(cancel.dataset.draftId, "draft-sales-form-001");

  const receipt = receiptCard({
    kind: "request_cancelled",
    requestKind: "sales_enquiry",
    status: "cancelled",
  });
  assert.equal(receipt.children[1].textContent, "Sales enquiry cancelled");
});

test("sales enquiry confirmation has polished copy, display values, and preserved actions", () => {
  const card = renderWorkflowCard({
    draftId: "draft-sales-001",
    kind: "sales_enquiry",
    status: "awaiting_confirmation",
    summary: {
      kind: "sales_enquiry",
      dealershipId: "northstar-manchester",
      enquiryType: "part_exchange",
      vehicleId: "veh-004",
      message: "I would like to discuss a BMW i4.\nPlease call tomorrow.",
      contactProvided: true,
    },
    vehicle: {
      id: "veh-004",
      year: 2026,
      make: "BMW",
      model: "i4",
      variant: "eDrive40 M Sport",
    },
    dealerships: [
      { id: "northstar-manchester", name: "Northstar Manchester", town: "Manchester" },
    ],
    privacyContact: "privacy@northstarmotors.example",
  });

  assert.match(card.className, /webchat-dealership-enquiry-review/);
  const header = childWithClass(card, "webchat-enquiry-review-heading");
  assert.equal(header.children[0].textContent, "Dealership enquiry");
  assert.equal(header.children[1].textContent, "Review before confirming");
  assert.equal(card.getAttribute("aria-labelledby"), header.children[1].id);
  assert.deepEqual(summaryValues(card), {
    Dealership: "Northstar Manchester",
    "Enquiry type": "Part Exchange",
    Vehicle: "2026 BMW i4 · eDrive40 M Sport",
  });

  const message = childWithClass(card, "webchat-enquiry-message");
  assert.equal(
    message.children[1].textContent,
    "I would like to discuss a BMW i4.\nPlease call tomorrow.",
  );

  const actions = childWithClass(card, "webchat-confirmation-actions");
  assert.equal(actions.children[0].className, "webchat-primary-action");
  assert.equal(actions.children[0].textContent, "Confirm enquiry");
  assert.equal(actions.children[0].dataset.chatAction, "confirm");
  assert.equal(actions.children[0].dataset.draftId, "draft-sales-001");
  assert.equal(actions.children[0].dataset.expectedKind, "sales_enquiry");
  assert.equal(actions.children[1].className, "webchat-secondary-action");
  assert.equal(actions.children[1].textContent, "Cancel");
  assert.equal(actions.children[1].dataset.chatAction, "cancel");

  const privacy = childWithClass(card, "webchat-privacy-footer");
  assert.equal(
    privacy.children[0].textContent,
    "Privacy contact · privacy@northstarmotors.example",
  );
});

test("sales enquiry without an attached vehicle omits the vehicle row", () => {
  const card = renderWorkflowCard({
    draftId: "draft-sales-002",
    kind: "sales_enquiry",
    status: "awaiting_confirmation",
    summary: {
      dealershipId: "northstar-bolton",
      enquiryType: "general",
      message: "Please contact me about a general sales question.",
    },
    dealerships: [{ id: "northstar-bolton", name: "Northstar Bolton" }],
  });

  assert.deepEqual(summaryValues(card), {
    Dealership: "Northstar Bolton",
    "Enquiry type": "General",
  });
});

test("dealership message confirmation formats department and contact choices", () => {
  const card = renderWorkflowCard({
    draftId: "draft-message-001",
    kind: "dealership_message",
    status: "awaiting_confirmation",
    summary: {
      dealershipId: "northstar-bolton",
      department: "customer_service",
      subject: "Existing order",
      message: "Please send an update.",
      preferredContactMethod: "email",
    },
    dealerships: [{ id: "northstar-bolton", town: "Bolton" }],
  });

  assert.deepEqual(summaryValues(card), {
    Dealership: "Bolton",
    Department: "Customer Service",
    Subject: "Existing order",
    "Preferred contact": "Email",
  });
  const actions = childWithClass(card, "webchat-confirmation-actions");
  assert.equal(actions.children[0].textContent, "Send message");
  assert.equal(actions.children[0].dataset.chatAction, "confirm");
  assert.equal(actions.children[0].dataset.expectedKind, "dealership_message");
});

test("workshop cancellation review has structured details and preserved actions", () => {
  const card = renderWorkflowCard({
    draftId: "draft-workshop-cancel-001",
    kind: "workshop_cancel",
    status: "awaiting_confirmation",
    summary: {
      kind: "workshop_cancel",
      bookingReference: "WORK-07BF686C",
      currentAppointment: "2026-08-27T16:00:00Z",
      service: "Tyre fitting",
      dealership: "Northstar Liverpool",
    },
    privacyContact: "privacy@northstarmotors.example",
  });

  assert.match(card.className, /webchat-workshop-cancel-review/);
  const header = childWithClass(card, "webchat-cancel-review-heading");
  assert.equal(header.children[0].textContent, "!");
  assert.equal(header.children[0].getAttribute("aria-hidden"), "true");
  assert.equal(header.children[1].children[0].textContent, "Workshop cancellation");
  assert.equal(header.children[1].children[1].textContent, "Cancel this booking?");
  assert.equal(
    header.children[1].children[2].textContent,
    "The appointment will be cancelled and its time released.",
  );

  const details = childWithClass(card, "webchat-cancel-review-details");
  assert.deepEqual(
    details.children.map((row) => [row.children[0].textContent, row.children[1].textContent]),
    [
      ["Booking reference", "WORK-07BF686C"],
      ["Current appointment", "Thu, 27 Aug 2026, 17:00"],
      ["Service", "Tyre fitting"],
      ["Dealership", "Northstar Liverpool"],
    ],
  );

  const actions = childWithClass(card, "webchat-confirmation-actions webchat-cancel-review-actions");
  assert.match(actions.children[0].className, /webchat-danger-primary-action/);
  assert.equal(actions.children[0].textContent, "Confirm cancellation");
  assert.equal(actions.children[0].dataset.expectedKind, "workshop_cancel");
  assert.equal(actions.children[0].dataset.chatAction, "confirm");
  assert.equal(actions.children[0].dataset.draftId, "draft-workshop-cancel-001");
  assert.equal(actions.children[1].className, "webchat-secondary-action");
  assert.equal(actions.children[1].textContent, "Keep current booking");
  assert.equal(actions.children[1].dataset.chatAction, "cancel");
  assert.equal(actions.children[1].dataset.draftId, "draft-workshop-cancel-001");

  const privacy = childWithClass(card, "webchat-privacy-footer");
  assert.equal(
    privacy.children[0].textContent,
    "Privacy contact · privacy@northstarmotors.example",
  );
});

test("every protected confirmation identifies its expected draft kind", () => {
  const examples = [
    { kind: "sales_enquiry", summary: {} },
    { kind: "dealership_message", summary: {} },
    { kind: "callback", summary: {} },
    { kind: "vehicle_interest", summary: {}, vehicle: {} },
    { kind: "part_exchange", summary: {} },
    { kind: "workshop_amend", summary: {} },
    { kind: "workshop_cancel", summary: {} },
  ];

  examples.forEach((view, index) => {
    const card = renderWorkflowCard({
      ...view,
      draftId: `draft-${index}`,
      status: "awaiting_confirmation",
    });
    const actions = card.children.find((child) => (
      child.className.split(" ").includes("webchat-confirmation-actions")
    ));
    assert.equal(actions.children[0].dataset.expectedKind, view.kind);
  });
});

test("callback dealership is selected only from a valid server prefill", () => {
  const dealerships = [
    { id: "northstar-liverpool", name: "Northstar Liverpool", town: "Liverpool" },
    { id: "northstar-stockport", name: "Northstar Stockport", town: "Stockport" },
  ];
  const render = (dealershipId) => renderWorkflowCard({
    kind: "callback",
    status: "collecting",
    summary: { dealershipId, reason: "Volvo availability in Stockport" },
    missingFields: ["firstName", "lastName", "email", "phone"],
    dealerships,
  });

  const selectedCard = render("northstar-stockport");
  const selected = selectedCard.querySelector('[name="dealershipId"]');
  assert.equal(selected.children.length, 2);
  assert.equal(selected.children[0].selected, false);
  assert.equal(selected.children[1].selected, true);
  const selectedDepartment = selectedCard.querySelector('[name="department"]');
  assert.equal(selectedDepartment.children[0].value, "sales");
  assert.equal(selectedDepartment.children[0].textContent, "Sales");
  assert.equal(selectedDepartment.children[0].selected, true);

  const emptyCard = render(undefined);
  const empty = emptyCard.querySelector('[name="dealershipId"]');
  assert.equal(empty.children[0].value, "");
  assert.equal(empty.children[0].textContent, "Choose dealership");
  assert.equal(empty.children[0].disabled, true);
  assert.equal(empty.children[0].selected, true);
  assert.equal(empty.children[1].selected, false);
  assert.equal(empty.children[2].selected, false);
});

test("workflow defaults remain selected unless valid conversation context overrides them", () => {
  const dealerships = [
    { id: "northstar-liverpool", name: "Northstar Liverpool", town: "Liverpool" },
  ];
  const callback = renderWorkflowCard({
    kind: "callback",
    status: "collecting",
    summary: {
      department: "service",
      preferredTime: "Tuesday morning",
      reason: "Discuss an MOT",
    },
    dealerships,
  });
  const callbackDepartment = callback.querySelector('[name="department"]');
  assert.equal(callbackDepartment.children[0].selected, false);
  assert.equal(callbackDepartment.children[1].selected, true);
  assert.equal(callback.querySelector('[name="preferredTime"]').value, "Tuesday morning");
  assert.equal(callback.querySelector('[name="reason"]').value, "Discuss an MOT");

  const sales = renderWorkflowCard({
    kind: "sales_enquiry",
    status: "collecting",
    summary: { message: "Please tell me about available finance." },
    dealerships,
  });
  const enquiryType = sales.querySelector('[name="enquiryType"]');
  assert.equal(enquiryType.children[0].value, "general");
  assert.equal(enquiryType.children[0].selected, true);
  assert.equal(sales.querySelector('[name="message"]').value, "Please tell me about available finance.");

  const message = renderWorkflowCard({
    kind: "dealership_message",
    status: "collecting",
    summary: { department: "parts", preferredContactMethod: "phone" },
    dealerships,
  });
  assert.equal(message.querySelector('[name="department"]').children[3].selected, true);
  assert.equal(message.querySelector('[name="preferredContactMethod"]').children[1].selected, true);
  assert.equal(message.querySelector('[name="subject"]').value, "Parts enquiry");
  assert.equal(
    message.querySelector('[name="message"]').value,
    "Please contact me about a parts enquiry.",
  );

  const contextualMessage = renderWorkflowCard({
    kind: "dealership_message",
    status: "collecting",
    summary: {
      department: "service",
      subject: "MOT availability",
      message: "Please tell me whether you have an MOT slot next Tuesday.",
    },
    dealerships,
  });
  assert.equal(contextualMessage.querySelector('[name="department"]').children[2].selected, true);
  assert.equal(contextualMessage.querySelector('[name="subject"]').value, "MOT availability");
  assert.equal(
    contextualMessage.querySelector('[name="message"]').value,
    "Please tell me whether you have an MOT slot next Tuesday.",
  );
});

test("a generic dealership-message request receives neutral editable text", () => {
  const card = renderWorkflowCard({
    kind: "dealership_message",
    status: "collecting",
    summary: {},
    dealerships: [
      { id: "northstar-stockport", name: "Northstar Stockport", town: "Stockport" },
    ],
  });

  assert.equal(card.querySelector('[name="subject"]').value, "General enquiry");
  assert.equal(
    card.querySelector('[name="message"]').value,
    "Please contact me about my enquiry.",
  );
});

test("safe editable text defaults are available across collecting workflows", () => {
  const dealerships = [
    { id: "northstar-stockport", name: "Northstar Stockport", town: "Stockport" },
  ];
  const callback = renderWorkflowCard({
    kind: "callback",
    status: "collecting",
    summary: {},
    dealerships,
  });
  assert.equal(callback.querySelector('[name="reason"]').value, "Discuss a sales enquiry");

  const sales = renderWorkflowCard({
    kind: "sales_enquiry",
    status: "collecting",
    summary: {},
    dealerships,
  });
  assert.equal(
    sales.querySelector('[name="message"]').value,
    "Please contact me about my sales enquiry.",
  );

  const interest = renderWorkflowCard({
    kind: "vehicle_interest",
    status: "collecting",
    summary: { vehicleId: "veh-001" },
    vehicle: { id: "veh-001", make: "BMW", model: "i4" },
  });
  assert.equal(
    interest.querySelector('[name="notes"]').value,
    "Please contact me if this vehicle becomes available.",
  );
});

test("part-exchange condition keeps its default unless context supplies a valid value", () => {
  const dealerships = [
    { id: "northstar-liverpool", name: "Northstar Liverpool", town: "Liverpool" },
  ];
  const render = (condition) => renderWorkflowCard({
    kind: "part_exchange",
    status: "collecting",
    summary: { condition },
    dealerships,
  });

  const defaultCondition = render(undefined).querySelector('[name="condition"]');
  assert.equal(defaultCondition.children[1].value, "good");
  assert.equal(defaultCondition.children[1].selected, true);

  const contextualCondition = render("fair").querySelector('[name="condition"]');
  assert.equal(contextualCondition.children[1].selected, false);
  assert.equal(contextualCondition.children[2].selected, true);

  const invalidCondition = render("rough").querySelector('[name="condition"]');
  assert.equal(invalidCondition.children[1].selected, true);
  assert.equal(invalidCondition.dataset.profileValueSource, undefined);
});
