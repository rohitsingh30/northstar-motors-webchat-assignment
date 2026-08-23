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
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const { renderWorkflowCard } = await import("../../webchat/widget/views/message.js");

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
});
