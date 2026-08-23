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
    this.listeners = new Map();
    this.disabled = false;
    this.classList = {
      add: (...tokens) => {
        this.className = [this.className, ...tokens].filter(Boolean).join(" ");
      },
      toggle: (token, enabled) => {
        const tokens = new Set(this.className.split(" ").filter(Boolean));
        if (enabled) tokens.add(token);
        else tokens.delete(token);
        this.className = [...tokens].join(" ");
      },
    };
  }

  append(...children) {
    this.children.push(...children);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  get childElementCount() {
    return this.children.length;
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  querySelectorAll(selector) {
    const matches = [];
    for (const child of this.children) {
      if (!(child instanceof TestElement)) continue;
      if (selector === "button" && child.tagName === "BUTTON") matches.push(child);
      matches.push(...child.querySelectorAll(selector));
    }
    return matches;
  }
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const {
  bindBookedTestDriveAction,
  renderMessage,
  setBookedTestDriveActionState,
  testDriveSlotPicker,
  workshopSlotPicker,
} = await import("../../webchat/widget/views/message.js");
const {
  vehicleAvailabilityCard,
  vehicleCard,
} = await import("../../webchat/widget/views/vehicle.js");

function descendantWithClass(element, className) {
  if (element.className.split(" ").includes(className)) return element;
  for (const child of element.children) {
    if (child instanceof TestElement) {
      const match = descendantWithClass(child, className);
      if (match) return match;
    }
  }
  return null;
}

test("book-test-drive action controls its inline disclosure", () => {
  const card = vehicleCard({
    id: "veh-019",
    make: "Volvo",
    model: "XC40",
    availability: "available",
  });
  const actions = descendantWithClass(card, "webchat-vehicle-actions");
  const button = actions.children[1];
  const flow = descendantWithClass(card, "webchat-booking-flow");

  assert.equal(button.getAttribute("aria-expanded"), "false");
  assert.equal(button.getAttribute("aria-controls"), "webchat-booking-flow-veh-019");
  assert.equal(flow.id, "webchat-booking-flow-veh-019");
});

test("sold vehicle status does not imply a future return date", () => {
  const card = vehicleAvailabilityCard({
    availability: "sold",
    vehicle: {
      year: 2025,
      make: "BMW",
      model: "1 Series",
      variant: "118i M Sport",
      dealershipTown: "Manchester",
    },
  });

  assert.equal(card.children[1].textContent, "2025 BMW 1 Series");
  assert.equal(card.children[2].textContent, "118i M Sport · Manchester");
  assert.equal(card.children[3].textContent, "sold");
  assert.equal(
    card.children[4].textContent,
    "This vehicle has been sold and has no published return date. The sales team can help with alternatives.",
  );
});

test("test-drive slot card identifies the vehicle being booked", () => {
  const picker = testDriveSlotPicker({
    vehicleId: "veh-019",
    vehicle: {
      id: "veh-019",
      year: 2025,
      make: "MINI",
      model: "Countryman",
      variant: "Cooper Classic",
      dealershipTown: "Stockport",
    },
    items: [{
      id: "td-slot-0001",
      vehicleId: "veh-019",
      dealershipId: "northstar-stockport",
      dealershipName: "Northstar Stockport",
      startsAt: "2026-08-28T09:00:00Z",
    }],
  }, { inline: true });

  const header = descendantWithClass(picker, "webchat-test-drive-vehicle-header");
  assert.equal(header.tagName, "HEADER");
  assert.equal(header.children[0].textContent, "Book a test drive");
  assert.equal(header.children[1].textContent, "MINI Countryman");
  assert.equal(header.children[2].textContent, "2025 · Cooper Classic · Stockport");
});

test("typed test-drive result renders inside an interactive booking flow", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Available test-drive times are shown below.",
    viewType: "test_drive_slot_picker",
    view: {
      vehicle: {
        make: "BMW",
        model: "3 Series",
        variant: "320d M Sport",
        year: 2024,
        dealershipTown: "Stockport",
      },
      items: [{
        id: "td-slot-0001",
        vehicleId: "veh-026",
        make: "BMW",
        model: "3 Series",
        dealershipId: "northstar-stockport",
        dealershipName: "Northstar Stockport",
        startsAt: "2026-08-27T09:00:00Z",
      }],
    },
  });

  const flow = descendantWithClass(message, "webchat-test-drive-flow-standalone");
  const slot = descendantWithClass(flow, "webchat-time-button");
  assert.match(message.className, /webchat-test-drive-flow-message/);
  assert.equal(flow.dataset.bookingFlow, "true");
  assert.equal(flow.vehicleLabel, "BMW 3 Series");
  assert.equal(slot.dataset.chatAction, "select-test-drive-slot");
});

test("test-drive empty state explains online availability and keeps recovery actions", () => {
  const picker = testDriveSlotPicker({
    vehicleId: "veh-019",
    vehicle: {
      id: "veh-019",
      year: 2025,
      make: "MINI",
      model: "Countryman",
      variant: "Cooper Classic",
      dealershipTown: "Stockport",
    },
    items: [],
    emptyMessage: "No online test-drive times are currently available for this vehicle.",
    suggestions: [
      { label: "Send a sales enquiry", text: "Send a sales enquiry about this vehicle" },
      { label: "Find another car", text: "help me find another car" },
      { label: "Part-exchange estimate", text: "I want a part-exchange estimate" },
      { label: "Current offers", text: "show me current offers" },
    ],
  }, { inline: true });

  assert.equal(
    picker.children[1].textContent,
    "No online test-drive times are currently available for this vehicle.",
  );
  const suggestions = descendantWithClass(picker, "webchat-suggestions");
  assert.deepEqual(
    suggestions.children.map((choice) => choice.textContent),
    ["Send a sales enquiry", "Find another car", "Part-exchange estimate", "Current offers"],
  );
  const close = descendantWithClass(picker, "webchat-flow-close");
  assert.equal(close.getAttribute("aria-label"), "Close test-drive booking");
});

test("standalone workshop slot card identifies the selected service", () => {
  const picker = workshopSlotPicker({
    items: [{
      id: "ws-slot-0001",
      serviceName: "Tyre fitting",
      dealershipId: "northstar-liverpool",
      dealershipName: "Northstar Liverpool",
      startsAt: "2026-08-24T14:00:00Z",
    }],
  }, {
    inline: true,
    showContext: true,
    serviceName: "Tyre fitting",
  });

  assert.equal(descendantWithClass(picker, "webchat-slot-title").textContent, "Tyre fitting");
  assert.equal(
    descendantWithClass(picker, "webchat-slot-location").textContent,
    "Choose a location, date and time",
  );
  assert.equal(descendantWithClass(picker, "webchat-workshop-location").textContent, "At Liverpool");
});

test("booked test-drive action exposes matching open and closed toggle states", () => {
  const action = new TestElement("button");
  const flow = {
    closest() {
      return { querySelector: () => action };
    },
  };

  setBookedTestDriveActionState(flow, true);
  assert.equal(action.textContent, "Hide booked test drive");
  assert.equal(action.getAttribute("aria-expanded"), "true");

  setBookedTestDriveActionState(flow, false);
  assert.equal(action.textContent, "View booked test drive");
  assert.equal(action.getAttribute("aria-expanded"), "false");
});

test("confirmed test drive converts the disabled booking CTA into a view toggle", () => {
  const action = new TestElement("button");
  action.disabled = true;
  action.dataset.chatAction = "test-drive";
  const flow = new TestElement("div");
  flow.id = "webchat-booking-flow-veh-019";
  flow.append(new TestElement("form"));

  assert.equal(bindBookedTestDriveAction(flow, action, {
    kind: "test_drive",
    vehicleId: "veh-019",
    vehicleLabel: "MINI Countryman",
    reference: "TEST-1234",
    startsAt: "2026-08-28T09:00:00Z",
    dealershipName: "Northstar Stockport",
  }), true);

  assert.equal(action.disabled, false);
  assert.equal(action.textContent, "View booked test drive");
  assert.equal(action.dataset.chatAction, "view-booked-test-drive");
  assert.equal(action.getAttribute("aria-expanded"), "false");
  assert.equal(action.getAttribute("aria-controls"), flow.id);
  assert.equal(flow.hidden, true);
  assert.equal(flow.children.length, 0);
  assert.equal(flow.booking.reference, "TEST-1234");
});
