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
    this.listeners = new Map();
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

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }
}

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

const {
  inlineOfferEnquiryForm,
  renderMessage,
  richSummary,
  setOfferEnquiryActionState,
} = await import("../../webchat/widget/views/message.js");

function childWithClass(element, className) {
  return element.children.find((child) => child.className.split(" ").includes(className));
}

function renderOffer(detailView = false) {
  return renderMessage({
    role: "assistant",
    text: "Current offer",
    viewType: "offer_list",
    view: {
      detailView,
      items: [{
        id: "offer-02",
        vehicleId: "veh-002",
        make: "BMW",
        model: "3 Series",
        productType: "PCH",
        monthlyPricePence: 39900,
        upfrontPaymentPence: 299900,
        termMonths: 48,
        annualMileage: 8000,
        expiresOn: "2026-11-18",
        description: "Published Northstar Motors new-car offer.",
      }],
    },
  });
}

test("offer card places Send sales enquiry and View car actions in its footer", () => {
  const message = renderOffer();
  const cards = childWithClass(message, "webchat-cards");
  const card = childWithClass(cards, "webchat-offer-card");
  const priceRow = childWithClass(card, "webchat-offer-price-row");
  const actions = childWithClass(card, "webchat-offer-actions");

  assert.equal(priceRow.children[0].textContent, "£399 / month");
  assert.equal(priceRow.children.length, 1);
  assert.equal(actions.children[0].textContent, "Send sales enquiry");
  assert.equal(actions.children[0].dataset.chatAction, "offer-enquiry");
  assert.equal(actions.children[0].dataset.offerId, "offer-02");
  assert.equal(actions.children[0].getAttribute("aria-expanded"), "false");
  assert.equal(actions.children[1].textContent, "View car");
  assert.equal(actions.children[1].href, "/?vehicle=veh-002");
  assert.equal(childWithClass(card, "webchat-offer-enquiry-flow"), undefined);
});

test("detailed offer retains the enquiry and vehicle actions", () => {
  const message = renderOffer(true);
  const cards = childWithClass(message, "webchat-cards");
  const card = childWithClass(cards, "webchat-offer-card");
  const priceRow = childWithClass(card, "webchat-offer-price-row");
  const description = childWithClass(card, "webchat-offer-description");
  const actions = childWithClass(card, "webchat-offer-actions");

  assert.equal(priceRow.children.length, 1);
  assert.equal(description.children[0].textContent, "Offer details");
  assert.equal(description.children[1].textContent, "Published Northstar Motors new-car offer.");
  assert.equal(actions.children.length, 2);
  assert.equal(actions.children[0].textContent, "Send sales enquiry");
  assert.equal(actions.children[1].textContent, "View car");
});

test("offer catalogue starts compact and expands without another request", () => {
  const view = {
    items: Array.from({ length: 8 }, (_, index) => ({
      id: `offer-${String(index + 1).padStart(2, "0")}`,
      vehicleId: `veh-${String(index + 1).padStart(3, "0")}`,
      make: "BMW",
      model: `${index + 1} Series`,
      productType: index % 2 ? "PCH" : "PCP",
      monthlyPricePence: 36_400 + index * 3_500,
    })),
  };
  const message = renderMessage({
    role: "assistant",
    text: "Current offers",
    viewType: "offer_list",
    view,
  });
  const cards = childWithClass(message, "webchat-cards");
  const toggle = childWithClass(message, "webchat-offer-list-toggle");

  assert.equal(
    richSummary({ viewType: "offer_list", view }),
    "8 current published offers are available. The first 3 are shown initially.",
  );
  assert.equal(cards.children.length, 3);
  assert.equal(toggle.textContent, "Show 5 more offers");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");

  toggle.listeners.get("click")();
  assert.equal(cards.children.length, 8);
  assert.equal(toggle.textContent, "Show fewer offers");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");

  toggle.listeners.get("click")();
  assert.equal(cards.children.length, 3);
});

test("inline offer enquiry form has an explicit cancel action", () => {
  const card = inlineOfferEnquiryForm({
    draftId: "draft-offer",
    kind: "sales_enquiry",
    summary: {},
    contextLabel: "BMW 3 Series PCH",
    dealerships: [],
  });
  const form = card.children.find((child) => child.tagName === "FORM");
  const heading = childWithClass(card, "webchat-workflow-form-heading");
  const actions = childWithClass(form, "webchat-inline-actions");

  assert.equal(actions.children[0].textContent, "Prepare sales enquiry");
  assert.equal(actions.children.length, 1);
  assert.equal(heading.children[0].textContent, "Sales enquiry for BMW 3 Series PCH");
  assert.equal(heading.children[1].textContent, "×");
  assert.match(heading.children[1].className, /webchat-flow-close/);
  assert.match(heading.children[1].className, /is-cancel/);
  assert.equal(heading.children[1].getAttribute("aria-label"), "Cancel sales enquiry");
  assert.equal(heading.children[1].dataset.chatAction, "cancel-inline-offer-enquiry");
  assert.equal(heading.children[1].dataset.draftId, "draft-offer");
});

test("offer enquiry action is a hide toggle while its separate message is open", () => {
  const launch = new TestElement("button");
  launch.disabled = true;
  launch.dataset.offerLabel = "BMW 3 Series PCH";
  const flow = { launchButton: launch };

  setOfferEnquiryActionState(flow, true);
  assert.equal(launch.disabled, false);
  assert.equal(launch.textContent, "Hide enquiry");
  assert.equal(launch.getAttribute("aria-expanded"), "true");
  assert.equal(launch.getAttribute("aria-label"), "Hide the sales enquiry about BMW 3 Series PCH");

  setOfferEnquiryActionState(flow, false);
  assert.equal(launch.textContent, "Send sales enquiry");
  assert.equal(launch.getAttribute("aria-expanded"), "false");
});

test("a collapsed submitted enquiry keeps its View enquiry action", () => {
  const launch = new TestElement("button");
  launch.dataset.offerLabel = "BMW 3 Series PCH";
  const flow = { launchButton: launch };

  setOfferEnquiryActionState(flow, false, true);

  assert.equal(launch.textContent, "View enquiry");
  assert.equal(launch.getAttribute("aria-expanded"), "false");
  assert.equal(launch.getAttribute("aria-label"), "View the sales enquiry about BMW 3 Series PCH");
});
