import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.classList = {
      add: (...tokens) => {
        this.className = [this.className, ...tokens].filter(Boolean).join(" ");
      },
    };
  }

  append(...children) {
    this.children.push(...children);
  }
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const { businessInformationCard, renderMessage } = await import("../../webchat/widget/views/message.js");

test("scoped business information renders only matched facts", () => {
  const card = businessInformationCard({
    version: 2,
    organisation: "Northstar Motors",
    topic: "part_exchange",
    facts: [
      {
        key: "part_exchange.estimate_notice",
        label: "Part-exchange estimates",
        value: "Estimates are indicative.",
      },
    ],
  });

  assert.deepEqual(
    card.children.map((child) => child.textContent),
    ["Northstar Motors", "Part-exchange estimates", "Estimates are indicative."],
  );
});

test("stored version 1 business information remains renderable", () => {
  const card = businessInformationCard({
    organisation: "Northstar Motors",
    finance: { notice: "Finance notice." },
    partExchange: { estimateNotice: "Estimate notice." },
    privacyContact: "privacy@example.test",
  });

  assert.deepEqual(
    card.children.map((child) => child.textContent),
    [
      "Northstar Motors",
      "Finance notice.",
      "Estimate notice.",
      "Privacy: privacy@example.test",
    ],
  );
});

test("the renderer preserves the grounded assistant answer", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Here is the confirmed finance information.",
    viewType: "business_information",
    view: { facts: [{ label: "Finance", value: "Finance is available subject to status." }] },
  });
  assert.equal(message.children[0].textContent, "Here is the confirmed finance information.");
});
