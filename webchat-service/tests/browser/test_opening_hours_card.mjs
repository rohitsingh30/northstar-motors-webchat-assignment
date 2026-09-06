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
    this.classList = {
      add: (...tokens) => {
        this.className = [this.className, ...tokens].filter(Boolean).join(" ");
      },
    };
  }

  get childElementCount() {
    return this.children.length;
  }

  append(...children) {
    this.children.push(...children);
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

const { renderMessage } = await import("../../webchat/widget/views/message.js");

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

test("holiday opening-hours cards omit regular weekday schedules", () => {
  const message = {
    role: "assistant",
    text: "Published holiday opening hours are shown below.",
    viewType: "opening_hours",
    view: {
      day: "Holiday",
      holidayOnly: true,
      items: [{
        name: "Northstar Bolton",
        town: "Bolton",
        day: "Holiday",
        holidayOnly: true,
        departments: [],
        holidayExceptions: [
          {
            department: "sales",
            date: "2026-09-19",
            label: "Bank holiday",
            opensAt: "10:00",
            closesAt: "16:00",
            closed: false,
          },
          {
            department: "service",
            date: "2026-09-19",
            label: "Bank holiday",
            closed: true,
          },
        ],
      }],
    },
  };

  const rendered = renderMessage(message);
  assert.equal(rendered.children[0].textContent, message.text);
  const card = descendantWithClass(rendered, "webchat-opening-hours-card");
  const heading = descendantWithClass(card, "webchat-opening-hours-heading");
  const holiday = descendantWithClass(card, "webchat-opening-holiday");

  assert.equal(heading.children[1].textContent, "Holiday opening hours");
  assert.deepEqual(
    card.children.map((child) => child.className),
    ["webchat-opening-hours-heading", "webchat-opening-holiday"],
  );
  assert.equal(holiday.children[0].textContent, "Bank holiday · 19 Sept");
});

test("a single dealership schedule keeps the server-grounded conversational prose", () => {
  const text = "Northstar Manchester on Saturday: Sales is open 09:00–17:00; Service is closed.";
  const rendered = renderMessage({
    role: "assistant",
    text,
    viewType: "opening_hours",
    view: {
      day: "Saturday",
      items: [{
        name: "Northstar Manchester",
        day: "Saturday",
        departments: [
          { name: "Sales", opensAt: "09:00", closesAt: "17:00", closed: false },
          { name: "Service", closed: true },
        ],
      }],
    },
  });
  assert.equal(rendered.children[0].textContent, text);
});
