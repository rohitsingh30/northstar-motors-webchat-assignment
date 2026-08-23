import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.name = "";
    this.textContent = "";
    this.type = "";
    this.value = "";
    this.classList = { add: (...tokens) => {
      this.className = [this.className, ...tokens].filter(Boolean).join(" ");
    } };
  }

  get childElementCount() {
    return this.children.length;
  }

  append(...children) {
    this.children.push(...children);
  }

  prepend(...children) {
    this.children.unshift(...children);
  }

  insertBefore(child, sibling) {
    const index = this.children.indexOf(sibling);
    if (index < 0) this.children.push(child);
    else this.children.splice(index, 0, child);
  }

  before() {}

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  querySelector(selector) {
    const name = selector.match(/^\[name="(.+)"\]$/)?.[1];
    const className = selector.match(/^\.(.+)$/)?.[1];
    for (const child of this.children) {
      if (name && child.name === name) return child;
      if (className && child.className.split(" ").includes(className)) return child;
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

const {
  renderWorkflowCard,
  testDriveDetailsForm,
  workshopDetailsForm,
} = await import("../../webchat/widget/views/message.js");
const { partExchangeEstimateForm } = await import(
  "../../webchat/widget/views/workflow-cards.js"
);
const { privateLookupForm } = await import(
  "../../webchat/widget/views/workflow-receipts.js"
);

function fieldNames(node) {
  const names = [];
  function visit(element) {
    if (element?.name) names.push(element.name);
    element?.children?.forEach(visit);
  }
  visit(node);
  return new Set(names);
}

const dealerships = [
  { id: "northstar-manchester", name: "Northstar Manchester", town: "Manchester" },
];

test("every rendered customer form exposes the fields required by its HTTP contract", () => {
  const forms = [
    [
      renderWorkflowCard({ kind: "callback", status: "collecting", summary: {}, dealerships }),
      ["dealershipId", "department", "firstName", "lastName", "phone", "email", "preferredTime", "reason"],
    ],
    [
      renderWorkflowCard({
        kind: "sales_enquiry",
        status: "collecting",
        summary: { vehicleId: "veh-001" },
        dealerships,
      }),
      ["dealershipId", "enquiryType", "message", "firstName", "lastName", "email", "phone", "vehicleId"],
    ],
    [
      renderWorkflowCard({ kind: "dealership_message", status: "collecting", summary: {}, dealerships }),
      ["dealershipId", "department", "subject", "message", "preferredContactMethod", "firstName", "lastName", "email", "phone"],
    ],
    [
      renderWorkflowCard({ kind: "part_exchange", status: "collecting", summary: {}, dealerships }),
      ["dealershipId", "registration", "mileage", "condition", "firstName", "lastName", "email", "phone"],
    ],
    [
      renderWorkflowCard({
        kind: "vehicle_interest",
        status: "collecting",
        summary: { vehicleId: "veh-001" },
        vehicle: { id: "veh-001", make: "BMW", model: "i4" },
      }),
      ["firstName", "lastName", "email", "phone", "notes", "vehicleId"],
    ],
    [
      partExchangeEstimateForm({ values: {} }),
      ["registration", "mileage", "condition"],
    ],
    [
      testDriveDetailsForm({
        slotId: "td-slot-0001",
        slotLabel: "28 August at 10:00 am",
        dealershipName: "Northstar Manchester",
        vehicleLabel: "BMW i4",
      }),
      ["firstName", "lastName", "email", "phone"],
    ],
    [
      workshopDetailsForm({
        slotId: "ws-slot-0001",
        slotLabel: "28 August at 10:00 am",
        dealershipName: "Northstar Manchester",
        serviceName: "Full service",
      }),
      ["firstName", "lastName", "email", "phone", "registration", "mileage", "notes"],
    ],
  ];

  forms.forEach(([form, expected]) => {
    assert.deepEqual(fieldNames(form), new Set(expected));
  });
});

test("unsafe customer and vehicle identifiers stay blank across every form", () => {
  const forms = [
    renderWorkflowCard({ kind: "callback", status: "collecting", summary: {}, dealerships }),
    renderWorkflowCard({ kind: "sales_enquiry", status: "collecting", summary: {}, dealerships }),
    renderWorkflowCard({ kind: "dealership_message", status: "collecting", summary: {}, dealerships }),
    renderWorkflowCard({ kind: "part_exchange", status: "collecting", summary: {}, dealerships }),
    renderWorkflowCard({
      kind: "vehicle_interest",
      status: "collecting",
      summary: { vehicleId: "veh-001" },
      vehicle: { id: "veh-001" },
    }),
    renderWorkflowCard({ kind: "workshop_amend", status: "collecting", summary: {} }),
    partExchangeEstimateForm({ values: {} }),
    testDriveDetailsForm({
      slotId: "td-slot-0001",
      slotLabel: "28 August at 10:00 am",
      dealershipName: "Northstar Manchester",
      vehicleLabel: "BMW i4",
    }),
    workshopDetailsForm({
      slotId: "ws-slot-0001",
      slotLabel: "28 August at 10:00 am",
      dealershipName: "Northstar Manchester",
      serviceName: "Full service",
    }),
  ];

  forms.forEach((form) => {
    ["firstName", "lastName", "email", "phone", "registration", "mileage"]
      .forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) assert.equal(field.value, "", `${name} must not be invented`);
      });
  });

  const lookup = privateLookupForm();
  ["reference", "lastName", "registration", "phone"].forEach((name) => {
    assert.equal(lookup.querySelector(`[name="${name}"]`).value, "");
  });

  const amendment = forms.find((form) => form.dataset?.workflowKind === "workshop_amend");
  assert.equal(amendment.querySelector('[name="mileage"]').value, "");
  assert.equal(amendment.querySelector('[name="notes"]').value, "");
  const workshop = forms.find((form) => form.dataset?.workshopDetails === "true");
  assert.equal(workshop.querySelector('[name="notes"]').value, "");
});

test("blank sensitive fields consistently provide examples instead of fabricated values", () => {
  const workshop = workshopDetailsForm({
    slotId: "ws-slot-0001",
    slotLabel: "28 August at 10:00 am",
    dealershipName: "Northstar Manchester",
    serviceName: "Full service",
  });

  assert.equal(workshop.querySelector('[name="firstName"]').placeholder, "e.g. Jamie");
  assert.equal(workshop.querySelector('[name="lastName"]').placeholder, "e.g. Taylor");
  assert.equal(workshop.querySelector('[name="email"]').placeholder, "e.g. jamie@example.com");
  assert.equal(workshop.querySelector('[name="phone"]').placeholder, "e.g. 07123 456789");
  assert.equal(workshop.querySelector('[name="registration"]').placeholder, "e.g. AB19 XYZ");
  assert.equal(workshop.querySelector('[name="mileage"]').placeholder, "e.g. 45000");
});
