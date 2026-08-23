import assert from "node:assert/strict";
import test from "node:test";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.classList = {
      add: (...names) => {
        this.className = [this.className, ...names].filter(Boolean).join(" ");
      },
    };
    this.dataset = {};
    this.textContent = "";
    this.type = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  get childElementCount() {
    return this.children.length;
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

const { suggestionChips } = await import("../../webchat/widget/views/suggestions.js");
const { renderMessage, richSummary } = await import("../../webchat/widget/views/message.js");
const { messageContent } = await import("../../webchat/widget/views/message-content.js");

test("a single valid follow-up suggestion remains visible", () => {
  const group = suggestionChips([
    {
      label: "Search full inventory",
      text: "search the full vehicle inventory with these filters",
      action: { type: "search_vehicle_inventory" },
    },
  ]);

  assert.equal(group.children.length, 1);
  assert.equal(group.children[0].textContent, "Search full inventory");
  assert.equal(
    group.children[0].dataset.chatSuggestionAction,
    JSON.stringify({ type: "search_vehicle_inventory" }),
  );
});

test("vehicle result summaries expose the active filters", () => {
  const summary = richSummary({
      viewType: "vehicle_list",
      view: {
        items: [{ id: "veh-001" }, { id: "veh-002" }, { id: "veh-003" }],
        total: 15,
        page: 1,
        pageSize: 3,
        filterSummary: "Fuel: Petrol; Gearbox: Automatic; Body style: SUV; Order: lowest mileage first",
      },
    });
  assert.equal(
    summary,
    [
      "Showing 3 of 15 available vehicles matching your request.",
      "Current filters:",
      "- Fuel: Petrol",
      "- Gearbox: Automatic",
      "- Body style: SUV",
      "- Order: lowest mileage first",
    ].join("\n"),
  );
  const content = messageContent({ role: "assistant" }, summary);
  const list = content.children.find((child) => child.tagName === "UL");
  assert.deepEqual(
    list.children.map((item) => item.textContent),
    [
      "Fuel: Petrol",
      "Gearbox: Automatic",
      "Body style: SUV",
      "Order: lowest mileage first",
    ],
  );
});

test("four useful vehicle controls remain visible", () => {
  const group = suggestionChips([
    { label: "Show me more", text: "show me more" },
    { label: "Compare", text: "compare" },
    { label: "Change fuel", text: "change fuel" },
    { label: "Any fuel", text: "any fuel" },
  ]);

  assert.deepEqual(
    group.children.map((child) => child.textContent),
    ["Show me more", "Compare", "Change fuel", "Any fuel"],
  );
});

test("multi-topic knowledge answers render as plain bullets", () => {
  const content = messageContent(
    { role: "assistant" },
    [
      "- PCP: Personal Contract Purchase uses monthly payments.",
      "- PCH: Personal Contract Hire is a vehicle lease.",
    ].join("\n"),
  );

  const list = content.children.find((child) => child.tagName === "UL");
  assert.deepEqual(
    list.children.map((item) => item.textContent),
    [
      "PCP: Personal Contract Purchase uses monthly payments.",
      "PCH: Personal Contract Hire is a vehicle lease.",
    ],
  );
});

test("normal replies render two or four chips while service choices render all", () => {
  const three = [
    { label: "One", text: "one" },
    { label: "Two", text: "two" },
    { label: "Three", text: "three" },
  ];
  assert.deepEqual(
    suggestionChips(three).children.map((child) => child.textContent),
    ["One", "Two"],
  );

  const services = [...three, { label: "Four", text: "four" }, { label: "Five", text: "five" }];
  assert.equal(suggestionChips(services, { allowMany: true }).children.length, 5);
});

test("finite LLM clarification options render as plain reply chips", () => {
  const item = renderMessage({
    role: "assistant",
    text: "Which BMW would you like?",
    viewType: "suggestion_list",
    view: {
      completeChoiceSet: true,
      suggestions: [
        { label: "BMW 3 Series 320d M Sport", text: "BMW 3 Series 320d M Sport" },
        { label: "BMW 1 Series 118i M Sport", text: "BMW 1 Series 118i M Sport" },
        { label: "BMW X1 xDrive23i M Sport", text: "BMW X1 xDrive23i M Sport" },
      ],
    },
  });
  const group = item.children.find((child) => child.className === "webchat-suggestions");

  assert.deepEqual(
    group.children.map((child) => child.textContent),
    [
      "BMW 3 Series 320d M Sport",
      "BMW 1 Series 118i M Sport",
      "BMW X1 xDrive23i M Sport",
    ],
  );
  assert.equal(group.children[0].dataset.chatSuggestion, "BMW 3 Series 320d M Sport");
  assert.equal(group.children[0].dataset.chatSuggestionAction, undefined);
});
