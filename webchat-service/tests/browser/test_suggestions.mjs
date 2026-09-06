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
const { continuesRenderedTurn, renderMessage } = await import("../../webchat/widget/views/message.js");
const { messageContent } = await import("../../webchat/widget/views/message-content.js");

test("a single contextual suggestion remains visible", () => {
  const group = suggestionChips([
    {
      label: "Search full inventory",
      text: "search the full vehicle inventory with these filters",
      action: { type: "search_vehicle_inventory" },
    },
  ]);

  assert.equal(group.children.length, 1);
  assert.equal(group.children[0].textContent, "Search full inventory");
});

test("vehicle result narration comes from the grounded message, not the renderer", () => {
  const grounded = "I found several close matches.\n- Petrol\n- Automatic\n- Lowest mileage first";
  const rendered = renderMessage({
    role: "assistant",
    text: grounded,
    viewType: "vehicle_list",
    view: { items: [{ id: "veh-001" }] },
  });
  const content = rendered.children[0];
  const list = content.children.find((child) => child.tagName === "UL");
  assert.deepEqual(
    list.children.map((item) => item.textContent),
    ["Petrol", "Automatic", "Lowest mileage first"],
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

test("trusted structured links render inline outside visual cards", () => {
  const content = messageContent({
    role: "assistant",
    text: "Call Manchester on 0161 555 0100.",
    segments: [
      { type: "text", text: "Call Manchester on " },
      {
        type: "link",
        label: "0161 555 0100",
        href: "tel:01615550100",
        destinationKind: "telephone",
      },
      { type: "text", text: "." },
    ],
  }, "Call Manchester on 0161 555 0100.");

  assert.equal(content.tagName, "P");
  const link = content.children.find((child) => child.tagName === "A");
  assert.equal(link.textContent, "0161 555 0100");
  assert.equal(link.attributes.get("href"), "tel:01615550100");
});

test("AI choices and grounded facts use semantic bold emphasis", () => {
  const content = messageContent({
    role: "assistant",
    text: "Choose Volvo XC40 or BMW i4.",
    blocks: [{
      type: "paragraph",
      segments: [
        { type: "text", text: "Choose " },
        { type: "emphasis", text: "Volvo XC40" },
        { type: "text", text: " or " },
        { type: "fact", factId: "fact-bmw-i4", text: "BMW i4" },
        { type: "text", text: "." },
      ],
    }],
  }, "Choose Volvo XC40 or BMW i4.");

  assert.deepEqual(
    content.children.filter((child) => child.tagName === "STRONG").map((child) => child.textContent),
    ["Volvo XC40", "BMW i4"],
  );
});

test("grounded semantic bullet segments render as a real list", () => {
  const content = messageContent({
    role: "assistant",
    text: "Service details:\n- Price on request\n- About 90 minutes",
    segments: [
      { type: "text", text: "Service details:" },
      { type: "bullet" },
      { type: "text", text: "Price on request" },
      { type: "bullet" },
      { type: "text", text: "About 90 minutes" },
    ],
  }, "Service details:\n- Price on request\n- About 90 minutes");

  const list = content.children.find((child) => child.tagName === "UL");
  assert.equal(list.children.length, 2);
  assert.equal(list.children[0].children[0].textContent, "Price on request");
  assert.equal(list.children[1].children[0].textContent, "About 90 minutes");
});

test("explicit blocks keep trusted inline facts in one paragraph before a list", () => {
  const content = messageContent({
    role: "assistant",
    text: "I found an MOT tomorrow at 14:00 in Bolton.\n- Does that time work?",
    blocks: [
      {
        type: "paragraph",
        segments: [
          { type: "text", text: "I found an MOT tomorrow at " },
          { type: "fact", factId: "fact-slot-start", text: "14:00" },
          { type: "text", text: " in " },
          { type: "fact", factId: "fact-dealership-name", text: "Northstar Bolton" },
          { type: "text", text: "." },
        ],
      },
      {
        type: "list",
        items: [{ segments: [{ type: "text", text: "Does that time work?" }] }],
      },
    ],
  }, "fallback");

  assert.equal(content.children[0].tagName, "P");
  assert.equal(
    content.children[0].children.map((child) => child.textContent).join(""),
    "I found an MOT tomorrow at 14:00 in Northstar Bolton.",
  );
  assert.deepEqual(
    content.children[0].children
      .filter((child) => child.tagName === "STRONG")
      .map((child) => [child.textContent, child.dataset.factId]),
    [
      ["14:00", "fact-slot-start"],
      ["Northstar Bolton", "fact-dealership-name"],
    ],
  );
  assert.equal(content.children[1].tagName, "UL");
  assert.equal(
    content.children[1].children[0].children.map((child) => child.textContent).join(""),
    "Does that time work?",
  );
});

test("unsafe structured links fall back to plain non-link text", () => {
  const content = messageContent({
    role: "assistant",
    text: "Do not open this.",
    segments: [{
      type: "link",
      label: "Do not open this",
      href: "javascript:alert(1)",
      destinationKind: "website",
    }],
  }, "Do not open this.");

  assert.equal(content.tagName, "P");
  assert.equal(content.children.length, 0);
  assert.equal(content.textContent, "Do not open this.");
});

test("normal replies keep up to four AI choices while service choices render all", () => {
  const three = [
    { label: "One", text: "one" },
    { label: "Two", text: "two" },
    { label: "Three", text: "three" },
  ];
  assert.deepEqual(
    suggestionChips(three).children.map((child) => child.textContent),
    ["One", "Two", "Three"],
  );

  const services = [...three, { label: "Four", text: "four" }, { label: "Five", text: "five" }];
  assert.equal(suggestionChips(services, { allowMany: true }).children.length, 5);
});

test("restored grounded presentation reduces a legacy three-chip row to two", () => {
  const item = renderMessage({
    role: "assistant",
    text: "What would you like to do next?",
    viewType: "grounded_presentation",
    view: {
      cards: [],
      quickReplies: [
        { label: "One", message: "one" },
        { label: "Two", message: "two" },
        { label: "Three", message: "three" },
      ],
    },
  });
  const group = item.children.find((child) => child.className === "webchat-suggestions");

  assert.deepEqual(
    group.children.map((child) => child.textContent),
    ["One", "Two"],
  );
});

test("a trailing follow-up renders after its result cards and before its replies", () => {
  const item = renderMessage({
    role: "assistant",
    purpose: "follow_up",
    continuesTurn: true,
    text: "Which type of car are you most interested in?",
    viewType: "grounded_presentation",
    view: {
      cards: [{
        type: "vehicle_preview",
        data: {
          items: [{
            id: "veh-001",
            year: 2026,
            dealershipTown: "Stockport",
            make: "BMW",
            model: "3 Series",
            derivative: "320d M Sport",
            pricePence: 2_125_000,
            monthlyFinancePence: 31_500,
            mileage: 34_750,
            fuelType: "Diesel",
            transmission: "Automatic",
          }],
        },
      }],
      quickReplies: [
        { label: "Show me more", message: "Show me more" },
        { label: "Compare", message: "Compare these vehicles" },
      ],
    },
  });

  assert.equal(item.children[0].className, "webchat-cards");
  assert.equal(item.children[1].textContent, "Which type of car are you most interested in?");
  assert.match(item.children[1].className, /\bwebchat-assistant-bubble\b/);
  assert.equal(item.children[2].className, "webchat-suggestions");
  assert.ok(item.className.includes("webchat-message--same-turn"));
});

test("a restored workflow question renders in a bubble after interruption cards", () => {
  const item = renderMessage({
    role: "assistant",
    purpose: "workflow_prompt",
    text: "What day or date would suit you, and roughly what time works best?",
    viewType: "grounded_presentation",
    view: {
      cards: [{
        type: "dealership",
        data: { items: [{ id: "northstar-liverpool", name: "Northstar Liverpool" }] },
      }],
      quickReplies: [],
    },
  });

  assert.equal(item.children[0].className, "webchat-cards");
  assert.equal(
    item.children[1].textContent,
    "What day or date would suit you, and roughly what time works best?",
  );
  assert.match(item.children[1].className, /\bwebchat-assistant-bubble\b/);
});

test("message purpose cannot collapse spacing across different turns", () => {
  const separateTurn = renderMessage({
    role: "assistant",
    purpose: "follow_up",
    text: "What else can I help you with?",
  });
  const sameTurn = renderMessage({
    role: "assistant",
    purpose: "answer",
    continuesTurn: true,
    text: "You can still register your interest.",
  });

  assert.ok(!separateTurn.className.includes("webchat-message--same-turn"));
  assert.ok(sameTurn.className.includes("webchat-message--same-turn"));
});

test("persisted turn identity owns compact spacing for every workflow", () => {
  const previous = {
    dataset: { turnId: "turn-availability", messageRole: "assistant" },
  };

  assert.equal(continuesRenderedTurn(previous, {
    turnId: "turn-availability",
    role: "assistant",
    purpose: "answer",
  }), true);
  assert.equal(continuesRenderedTurn(previous, {
    turnId: "turn-next-request",
    role: "assistant",
    purpose: "follow_up",
  }), false);
  assert.equal(continuesRenderedTurn(previous, {
    turnId: "turn-availability",
    role: "user",
  }), false);
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
