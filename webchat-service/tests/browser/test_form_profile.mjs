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

test("ordinary values persist and private booking proof never persists or prefills", () => {
  const storage = new MemoryStorage();
  const profile = createFormProfile(transcript(), storage);
  const firstName = field("firstName", "Rohit");
  const bookingReference = field("reference", "WORK-10001", { privateLookup: true });

  profile.remember(container([firstName, bookingReference]));

  assert.deepEqual(JSON.parse(storage.getItem("northstarFormProfileV1")), {
    firstName: "Rohit",
  });

  const emptyFirstName = field("firstName");
  const emptyBookingReference = field("reference", "", { privateLookup: true });
  profile.apply(container([emptyFirstName, emptyBookingReference]));

  assert.equal(emptyFirstName.value, "Rohit");
  assert.equal(emptyBookingReference.value, "");
  profile.disconnect();
});
