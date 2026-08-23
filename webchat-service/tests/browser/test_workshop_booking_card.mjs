import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.textContent = "";
    this.type = "";
    this.classList = {
      add: (...tokens) => {
        this.className = [this.className, ...tokens].filter(Boolean).join(" ");
      },
    };
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const {
  inlineBookingReceipt,
  inlineWorkshopConfirmation,
  inlineWorkshopReceipt,
  receiptCard,
  renderMessage,
  setWorkshopFlowContent,
} = await import("../../webchat/widget/views/message.js");

function childWithClass(element, className) {
  return element.children.find((child) => child.className.split(" ").includes(className));
}

test("workshop flow gives exactly one surface ownership to nested cards", () => {
  const flow = new TestElement("div");
  const picker = new TestElement("section");
  const receipt = new TestElement("details");

  setWorkshopFlowContent(flow, picker);
  assert.deepEqual(flow.children, [picker]);
  assert.equal(flow.dataset.surfaceOwner, "flow");

  setWorkshopFlowContent(flow, receipt, "child");
  assert.deepEqual(flow.children, [receipt]);
  assert.equal(flow.dataset.surfaceOwner, "child");
});

test("workshop confirmation declares the expected write contract", () => {
  const card = inlineWorkshopConfirmation(
    { draftId: "draft-workshop-001", kind: "workshop_booking" },
    {
      serviceName: "Interim service",
      slotLabel: "Tuesday 25 August at 03:00 pm",
      dealershipName: "Northstar Stockport",
    },
    { registration: "ABC", mileage: 24000 },
  );
  const actions = childWithClass(card, "webchat-inline-actions");
  const confirm = actions.children[1];

  assert.equal(confirm.textContent, "Confirm booking");
  assert.equal(confirm.dataset.draftId, "draft-workshop-001");
  assert.equal(confirm.dataset.expectedKind, "workshop_booking");
});

test("confirmed workshop booking has structured details and distinct retained actions", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Your workshop booking has been verified.",
    viewType: "workshop_booking_details",
    view: {
      status: "confirmed",
      serviceTypeName: "Tyre fitting",
      reference: "WORK-07BF686C",
      startsAt: "2026-08-27T16:00:00Z",
      dealershipName: "Northstar Liverpool",
    },
  });

  const card = childWithClass(message, "webchat-workshop-booking-card");
  assert.equal(card.tagName, "DETAILS");
  const heading = childWithClass(card, "webchat-booking-confirmed-heading");
  assert.equal(heading.tagName, "SUMMARY");
  assert.equal(heading.children[0].textContent, "✓");
  assert.equal(heading.children[0].attributes.get("aria-hidden"), "true");
  assert.equal(heading.children[1].children[0].textContent, "Booking confirmed");
  assert.equal(heading.children[1].children[1].textContent, "Tyre fitting");
  assert.equal(heading.children[1].children[2].textContent, "Reference WORK-07BF686C");

  const details = childWithClass(card, "webchat-booking-confirmed-details");
  assert.deepEqual(
    details.children.map((row) => [row.children[0].textContent, row.children[1].textContent]),
    [
      ["Appointment", "Thu, 27 Aug 2026, 17:00"],
      ["Location", "Northstar Liverpool"],
    ],
  );

  const actions = childWithClass(card, "webchat-booking-actions");
  assert.equal(actions.children[0].textContent, "Reschedule");
  assert.equal(actions.children[0].className, "webchat-primary-action");
  assert.equal(actions.children[0].dataset.chatAction, "start-workshop-amendment");
  assert.equal(actions.children[0].dataset.bookingReference, "WORK-07BF686C");
  assert.equal(actions.children[1].textContent, "Cancel booking");
  assert.match(actions.children[1].className, /webchat-danger-action/);
  assert.equal(actions.children[1].dataset.chatAction, "start-workshop-cancellation");
  assert.equal(actions.children[1].dataset.bookingReference, "WORK-07BF686C");
  assert.equal(card.receiptView.kind, "workshop_booking");
  assert.equal(card.receiptView.serviceName, "Tyre fitting");
  assert.equal(card.receiptView.dealershipName, "Northstar Liverpool");
});

test("inline workshop receipt has management actions but no dismiss control", () => {
  const receipt = inlineWorkshopReceipt({
    reference: "WORK-8745DC30",
    serviceName: "Diagnostic inspection",
    slotLabel: "Thu, 27 Aug 2026, 17:00",
    dealershipName: "Northstar Manchester",
    registration: "ab12 cde",
  });

  assert.equal(receipt.tagName, "DETAILS");
  assert.equal(receipt.children[0].tagName, "SUMMARY");
  assert.equal(receipt.children[0].children[1].children[0].textContent, "Workshop booked");
  assert.equal(receipt.children[0].children[1].children[1].textContent, "Reference WORK-8745DC30");
  assert.deepEqual(
    receipt.children[1].children.map((row) => [row.children[0].textContent, row.children[1].textContent]),
    [
      ["Service", "Diagnostic inspection"],
      ["Appointment", "Thu, 27 Aug 2026, 17:00"],
      ["Location", "Northstar Manchester"],
      ["Vehicle", "AB12 CDE"],
    ],
  );
  assert.equal(receipt.children.some((child) => child.className.includes("webchat-flow-close")), false);
  const actions = childWithClass(receipt, "webchat-booking-actions");
  assert.equal(actions.children[0].textContent, "Reschedule");
  assert.equal(actions.children[0].dataset.chatAction, "start-workshop-amendment");
  assert.equal(actions.children[0].dataset.bookingReference, "WORK-8745DC30");
  assert.equal(actions.children[1].textContent, "Cancel booking");
  assert.equal(actions.children[1].dataset.chatAction, "start-workshop-cancellation");
  assert.equal(actions.children[1].dataset.bookingReference, "WORK-8745DC30");
});

test("inline test-drive receipt expands to a useful booking summary", () => {
  const receipt = inlineBookingReceipt({
    reference: "TEST-1234",
    vehicleLabel: "BMW i4",
    slotLabel: "Fri, 28 Aug 2026, 10:30",
    dealershipName: "Northstar Bolton",
  });

  assert.equal(receipt.tagName, "DETAILS");
  assert.deepEqual(
    receipt.children[1].children.map((row) => [row.children[0].textContent, row.children[1].textContent]),
    [
      ["Vehicle", "BMW i4"],
      ["Appointment", "Fri, 28 Aug 2026, 10:30"],
      ["Location", "Northstar Bolton"],
    ],
  );
});

test("persisted confirmed booking receipts use the same expandable card", () => {
  const receipt = receiptCard({
    kind: "workshop_amend",
    status: "confirmed",
    reference: "WORK-UPDATED",
    startsAt: "2026-08-29T09:00:00Z",
    dealershipName: "Northstar Stockport",
    serviceName: "Diagnostic inspection",
    registration: "AB12 CDE",
  });

  assert.equal(receipt.tagName, "DETAILS");
  assert.match(receipt.className, /webchat-booking-disclosure/);
  assert.equal(receipt.children[0].children[1].children[0].textContent, "Workshop booking updated");
  assert.deepEqual(
    receipt.children[1].children.map((row) => [row.children[0].textContent, row.children[1].textContent]),
    [
      ["Appointment", "Sat, 29 Aug 2026, 10:00"],
      ["Location", "Northstar Stockport"],
      ["Vehicle", "AB12 CDE"],
      ["Service", "Diagnostic inspection"],
    ],
  );
});

test("test-drive receipts show details without unsupported management actions", () => {
  const receipt = receiptCard({
    kind: "test_drive",
    status: "confirmed",
    reference: "TEST-LEGACY",
  });

  assert.equal(receipt.children.length, 1);
  assert.equal(childWithClass(receipt, "webchat-booking-actions"), undefined);
});

test("keeping a workshop booking is described as unchanged, not cancelled", () => {
  const receipt = receiptCard({
    kind: "workshop_change_abandoned",
  });

  assert.equal(receipt.children[1].textContent, "Current booking kept");
  assert.equal(receipt.children[2].textContent, "No changes were made to your booking.");
});
