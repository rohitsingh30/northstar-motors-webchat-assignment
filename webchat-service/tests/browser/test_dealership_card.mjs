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
    this.hidden = false;
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

function childWithClass(element, className) {
  return element.children.find((child) => child.className.split(" ").includes(className));
}

test("dealership card starts a location-scoped workshop turn", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Dealership details",
    viewType: "dealership_list",
    view: {
      items: [{
        id: "northstar-stockport",
        name: "Northstar Stockport",
        town: "Stockport",
        departments: ["parts", "sales", "service"],
      }],
    },
  });
  const cards = childWithClass(message, "webchat-cards");
  const card = childWithClass(cards, "webchat-dealership-card");
  const actions = childWithClass(card, "webchat-dealership-actions");

  assert.equal(actions.children[0].textContent, "Book a service");
  assert.equal(
    actions.children[0].dataset.chatSuggestion,
    "Book a service at Northstar Stockport",
  );
  assert.deepEqual(
    JSON.parse(actions.children[0].dataset.chatSuggestionAction),
    {
      type: "start_dealership_workshop",
      dealershipId: "northstar-stockport",
    },
  );
  assert.equal(childWithClass(card, "webchat-dealership-workshop-flow"), undefined);
});

test("dealership service message retains the dealership on service actions", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Choose a service",
    viewType: "service_list",
    view: {
      dealershipId: "northstar-stockport",
      items: [{
        id: "full-service",
        name: "Full service",
        description: "A complete scheduled service.",
      }],
    },
  });
  const cards = childWithClass(message, "webchat-cards");
  const service = childWithClass(cards, "webchat-service-card");
  const footer = childWithClass(service, "webchat-service-footer");
  const findTimes = footer.children[1];

  assert.equal(findTimes.textContent, "Find times");
  assert.equal(findTimes.dataset.serviceTypeId, "full-service");
  assert.equal(findTimes.dataset.dealershipId, "northstar-stockport");
});
