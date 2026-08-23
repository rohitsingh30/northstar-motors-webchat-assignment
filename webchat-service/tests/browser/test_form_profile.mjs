import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const formProfileSource = await readFile(
  new URL("../../webchat/widget/core/form-profile.js", import.meta.url),
  "utf8",
);
const { createFormProfile } = await import(
  `data:text/javascript;base64,${Buffer.from(formProfileSource).toString("base64")}`
);

globalThis.HTMLSelectElement = class HTMLSelectElement {};
globalThis.MutationObserver = class MutationObserver {
  observe() {}
  disconnect() {}
};

class MemoryStorage {
  constructor(initial = {}) {
    this.values = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    this.values.set(key, value);
  }
}

function field(name, value = "", { privateLookup = false } = {}) {
  return {
    name,
    value,
    type: "text",
    disabled: false,
    readOnly: false,
    dataset: {},
    closest(selector) {
      if (selector === "form") return {};
      if (selector === "[data-private-lookup]") return privateLookup ? {} : null;
      return null;
    },
  };
}

function container(fields) {
  return {
    matches: () => false,
    querySelectorAll: () => fields,
  };
}

function transcript() {
  return {
    contains: () => true,
    addEventListener: () => {},
  };
}

test("ordinary profile values persist and private booking proof never persists or prefills", () => {
  const storage = new MemoryStorage();
  const profile = createFormProfile(transcript(), storage);
  const firstName = field("firstName", "Rohit");
  const registration = field("registration", "AB12 CDE");
  const bookingReference = field("reference", "WORK-10001", { privateLookup: true });
  const lookupRegistration = field("registration", "PRIVATE REG", { privateLookup: true });

  profile.remember(container([firstName, registration, bookingReference, lookupRegistration]));

  assert.deepEqual(JSON.parse(storage.getItem("northstarFormProfileV1")), {
    firstName: "Rohit",
    registration: "AB12 CDE",
  });

  const emptyFirstName = field("firstName");
  const emptyRegistration = field("registration");
  const emptyBookingReference = field("reference", "", { privateLookup: true });
  const emptyLookupRegistration = field("registration", "", { privateLookup: true });
  profile.apply(container([
    emptyFirstName,
    emptyRegistration,
    emptyBookingReference,
    emptyLookupRegistration,
  ]));

  assert.equal(emptyFirstName.value, "Rohit");
  assert.equal(emptyRegistration.value, "AB12 CDE");
  assert.equal(emptyBookingReference.value, "");
  assert.equal(emptyLookupRegistration.value, "");
  profile.disconnect();
});

test("workflow content and choices never leak into a later form", () => {
  const storage = new MemoryStorage({
    northstarFormProfileV1: JSON.stringify({
      firstName: "Rohit",
      dealershipId: "northstar-liverpool",
      department: "sales",
      subject: "Question about vehicle collection for part exchange",
      message: "Please collect my vehicle.",
    }),
  });
  const profile = createFormProfile(transcript(), storage);
  const firstName = field("firstName");
  const dealership = field("dealershipId");
  const department = field("department");
  const subject = field("subject");
  const message = field("message");

  profile.apply(container([firstName, dealership, department, subject, message]));

  assert.equal(firstName.value, "Rohit");
  assert.equal(dealership.value, "");
  assert.equal(department.value, "");
  assert.equal(subject.value, "");
  assert.equal(message.value, "");
  assert.deepEqual(JSON.parse(storage.getItem("northstarFormProfileV1")), {
    firstName: "Rohit",
  });
  profile.disconnect();
});

test("remember stores reusable customer and vehicle details but not workflow-specific values", () => {
  const storage = new MemoryStorage();
  const profile = createFormProfile(transcript(), storage);

  profile.remember(container([
    field("firstName", "Rohit"),
    field("lastName", "Sharma"),
    field("email", "rohit@example.com"),
    field("phone", "07123456789"),
    field("dealershipId", "northstar-liverpool"),
    field("subject", "Question about collection"),
    field("message", "Please call me about collection."),
    field("registration", "AB12 CDE"),
    field("notes", "Old request notes"),
  ]));

  assert.deepEqual(JSON.parse(storage.getItem("northstarFormProfileV1")), {
    firstName: "Rohit",
    lastName: "Sharma",
    email: "rohit@example.com",
    phone: "07123456789",
    registration: "AB12 CDE",
  });
  profile.disconnect();
});
