import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.textContent = "";
  }

  get childElementCount() { return this.children.length; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
}

globalThis.document = {
  createElement: (tagName) => new TestElement(tagName),
};

const { confirmationCard } = await import("../../webchat/widget/views/workflow-confirmations.js");

function all(element) {
  return [element, ...element.children.flatMap(all)];
}

test("workshop booking review shows customer labels instead of trusted selector fields", () => {
  const card = confirmationCard({
    draftId: "draft-1",
    kind: "workshop_booking",
    status: "awaiting_confirmation",
    privacyContact: "privacy@northstarmotors.example",
    summary: {
      dealershipId: "northstar-bolton",
      serviceTypeId: "mot",
      selectedDealershipId: "northstar-bolton",
      selectedDealershipName: "Northstar Bolton",
      selectedServiceTypeId: "mot",
      selectedServiceName: "MOT",
      selectedStartsAt: "2026-09-07T14:00:00Z",
    },
    localDetails: {
      fullName: "Test Customer",
      email: "test.customer@example.com",
      phone: "07700900123",
      registration: "AB12 CDE",
      mileage: 25000,
    },
  });

  const text = all(card).map((node) => node.textContent).filter(Boolean);
  assert.equal(card.className, "webchat-confirmation webchat-workshop-booking-review");
  assert.ok(text.includes("Review your appointment"));
  assert.ok(text.includes("Northstar Bolton"));
  assert.ok(text.includes("MOT"));
  assert.ok(text.some((value) => value.includes("7 Sept 2026") && value.includes("15:00")));
  assert.ok(text.includes("Your details"));
  assert.ok(text.includes("Only visible in this tab"));
  assert.ok(!text.includes("Selected dealership"));
  assert.ok(!text.includes("Selected Dealership Id"));
  assert.ok(!text.includes("northstar-bolton"));
  assert.ok(!text.includes("2026-09-07T14:00:00Z"));
  assert.ok(!text.some((value) => value.includes("privacy@")));
});
