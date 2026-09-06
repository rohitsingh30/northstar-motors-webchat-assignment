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
    this.classList = { add: (...tokens) => { this.className = [this.className, ...tokens].filter(Boolean).join(" "); } };
  }
  get childElementCount() { return this.children.length; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name); }
  addEventListener() {}
  querySelector(selector) {
    const className = selector.startsWith(".") ? selector.slice(1) : null;
    for (const child of this.children) {
      if (className && child.className?.split(" ").includes(className)) return child;
      const nested = child.querySelector?.(selector);
      if (nested) return nested;
    }
    return null;
  }
}
globalThis.document = { createElement: (tagName) => new TestElement(tagName) };

const { renderMessage } = await import("../../webchat/widget/views/message.js");

function all(element) {
  return [element, ...element.children.flatMap(all)];
}

test("vehicle results expose only the trusted modal action while other cards stay read-only", () => {
  const messages = [
    renderMessage({ role: "assistant", text: "Cars", viewType: "vehicle_list", view: { items: [{ id: "veh-001", make: "BMW", model: "1 Series", availability: "available" }] } }),
    renderMessage({ role: "assistant", text: "Offers", viewType: "offer_list", view: { items: [{ id: "offer-01", vehicleId: "veh-001", name: "BMW offer" }] } }),
    renderMessage({ role: "assistant", text: "Dealers", viewType: "dealership_list", view: { items: [{ id: "northstar-one", name: "Northstar One" }] } }),
    renderMessage({ role: "assistant", text: "Services", viewType: "service_list", view: { items: [{ id: "service-one", name: "Interim service" }] } }),
  ];
  const nodes = messages.flatMap(all);
  const buttons = nodes.filter((node) => node.tagName === "BUTTON");
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].textContent, "View vehicle");
  assert.equal(buttons[0].dataset.vehicleDetail, "veh-001");
  assert.equal(nodes.filter((node) => node.tagName === "A").length, 0);
  assert.equal(nodes.filter((node) => ["INPUT", "FORM", "SELECT", "TEXTAREA", "DETAILS"].includes(node.tagName)).length, 0);
});

test("single result cards never render a meaningless option one label", () => {
  const messages = [
    renderMessage({
      role: "assistant",
      text: "One vehicle",
      viewType: "vehicle_list",
      view: { items: [{ id: "veh-001", make: "BMW", model: "1 Series", optionNumber: 7 }] },
    }),
    renderMessage({
      role: "assistant",
      text: "One offer",
      viewType: "offer_list",
      view: { items: [{ id: "offer-01", name: "BMW offer", optionNumber: 7 }] },
    }),
    renderMessage({
      role: "assistant",
      text: "One dealership",
      viewType: "dealership_list",
      view: { items: [{ id: "dealer-01", name: "Northstar Stockport", optionNumber: 7 }] },
    }),
  ];

  const optionLabels = messages
    .flatMap(all)
    .filter((node) => node.className?.split(" ").includes("webchat-option-number"));
  assert.equal(optionLabels.length, 0);
});

test("multiple result cards retain stable choice labels across card families", () => {
  const messages = [
    renderMessage({
      role: "assistant",
      text: "Vehicles",
      viewType: "vehicle_list",
      view: { items: [{ id: "veh-1", make: "BMW" }, { id: "veh-2", make: "MINI" }] },
    }),
    renderMessage({
      role: "assistant",
      text: "Offers",
      viewType: "offer_list",
      view: { items: [{ id: "offer-1", name: "Offer A" }, { id: "offer-2", name: "Offer B" }] },
    }),
    renderMessage({
      role: "assistant",
      text: "Dealerships",
      viewType: "dealership_list",
      view: { items: [{ id: "dealer-1", name: "Bolton" }, { id: "dealer-2", name: "Stockport" }] },
    }),
  ];

  const optionLabels = messages
    .flatMap(all)
    .filter((node) => node.className?.split(" ").includes("webchat-option-number"))
    .map((node) => node.textContent);
  assert.deepEqual(optionLabels, ["Option 1", "Option 2", "Option 1", "Option 2", "Option 1", "Option 2"]);
});

test("an old entity chip collection is projected to bullets even without descriptions", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which dealership should call you?",
    viewType: "choice_list",
    view: {
      choiceEntityType: "dealership",
      collectionPresentation: {
        schemaVersion: 1,
        layout: "chip_grid",
        purpose: "choice",
        items: [
          {
            label: "Northstar Bolton",
            message: "Choose Northstar Bolton",
          },
          { label: "Northstar Stockport" },
        ],
      },
    },
  });

  const list = all(message).find((node) => node.className === "webchat-collection-list");
  assert.ok(all(message.children[0]).includes(list));
  assert.deepEqual(list.children.map((item) => item.children[0].textContent), [
    "Northstar Bolton",
    "Northstar Stockport",
  ]);
  assert.equal(all(message).some((node) => node.tagName === "BUTTON"), false);
});

test("a compact workflow value uses the ordinary simple reply component", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which contact method would you prefer?",
    viewType: "choice_list",
    view: {
      choiceEntityType: "workflow_option",
      collectionPresentation: {
        schemaVersion: 1,
        layout: "chip_grid",
        purpose: "choice",
        items: [
          { label: "Phone", message: "Please contact me by phone" },
          { label: "Email" },
        ],
      },
    },
  });

  const replies = all(message).find(
    (node) => node.className === "webchat-suggestions webchat-collection-replies",
  );
  assert.deepEqual(replies.children.map((item) => item.textContent), ["Phone", "Email"]);
  assert.deepEqual(replies.children.map((item) => item.dataset.chatSuggestion), [
    "Please contact me by phone",
    "Email",
  ]);
  assert.equal(replies.children.every((item) => item.className === "webchat-suggestion"), true);
});

test("a detailed vehicle clarification renders bullets before its final question bubble", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which vehicle would you like to test drive?",
    purpose: "clarification",
    viewType: "choice_list",
    view: {
      selectionOnly: true,
      choiceEntityType: "vehicle",
      collectionPresentation: {
        schemaVersion: 1,
        layout: "bullet_list",
        purpose: "clarification",
        items: [
          { label: "Option 1 — BMW 3 Series", description: "320d M Sport · Stockport" },
          { label: "Option 2 — MINI Countryman", description: "Cooper Classic · Stockport" },
          { label: "Option 3 — BMW 1 Series", description: "118i M Sport · Manchester" },
        ],
      },
    },
  });

  const list = all(message).find((node) => node.className === "webchat-collection-list");
  assert.equal(list.tagName, "UL");
  assert.equal(list.dataset.collectionPurpose, "clarification");
  assert.deepEqual(
    list.children.map((item) => item.tagName),
    ["LI", "LI", "LI"],
  );
  assert.deepEqual(
    list.children.map((item) => item.children[0].textContent),
    ["Option 1 — BMW 3 Series", "Option 2 — MINI Countryman", "Option 3 — BMW 1 Series"],
  );
  assert.ok(all(message.children[0]).includes(list));
  assert.equal(message.children[1].className, "webchat-assistant-bubble");
  assert.ok(all(message.children[1]).some(
    (node) => node.textContent === "Which vehicle would you like to test drive?",
  ));
  assert.equal(all(message).some((item) => item.tagName === "BUTTON"), false);
});

test("trusted appointment context keeps details as bullets with simple reply chips", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Here are the next available times at Bolton. Which one would you like?",
    viewType: "trusted_slot_context",
    view: {
      sourceViewType: "slot_list",
      items: [
        {
          id: "ws-slot-0001",
          startsAt: "2026-09-07T09:30:00+01:00",
          displayLabel: "Mon, 7 Sept 2026, 09:30",
          dealershipTown: "Bolton",
          serviceTypeName: "Full service",
        },
        {
          id: "ws-slot-0002",
          startsAt: "2026-09-08T14:00:00+01:00",
          dealershipTown: "Bolton",
          serviceTypeName: "Full service",
        },
      ],
    },
  });

  const list = all(message).find((node) => node.className === "webchat-collection-list");
  assert.equal(list.dataset.collectionPurpose, "choice");
  assert.equal(list.children.length, 2);
  assert.deepEqual(list.children.map((item) => item.tagName), ["LI", "LI"]);
  assert.match(list.children[0].children[0].textContent, /^Mon, 7 Sept 2026, 09:30/);
  assert.equal(list.children[0].children[1].textContent, " — Bolton · Full service");
  assert.match(list.children[1].children[0].textContent, /^Tue, 8 Sept 2026, 14:00/);
  assert.ok(all(message.children[0]).includes(list));
  assert.ok(all(message.children[1]).some(
    (node) => node.textContent === "Here are the next available times at Bolton. Which one would you like?",
  ));
  assert.equal(all(list).some((node) => node.tagName === "BUTTON"), false);
  const replies = all(message).find(
    (node) => node.className === "webchat-suggestions webchat-appointment-replies",
  );
  assert.deepEqual(replies.children.map((node) => node.tagName), ["BUTTON", "BUTTON"]);
  assert.deepEqual(
    replies.children.map((node) => node.textContent),
    ["Mon 7 Sept · 09:30", "Tue 8 Sept · 14:00"],
  );
  assert.deepEqual(
    replies.children.map((node) => node.dataset.chatSuggestion),
    ["Mon, 7 Sept 2026, 09:30", "Tue, 8 Sept 2026, 14:00"],
  );
});

test("trusted appointment rendering uses the server-owned local label", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which appointment would you like?",
    viewType: "trusted_slot_context",
    view: {
      items: [{
        id: "td-slot-0241",
        startsAt: "2026-09-08T09:00:00Z",
        displayLabel: "Tue, 8 Sept 2026, 10:00",
      }],
    },
  });

  const list = all(message).find((node) => node.className === "webchat-collection-list");
  assert.match(list.children[0].children[0].textContent, /Tue, 8 Sept 2026, 10:00/);
  assert.doesNotMatch(list.children[0].children[0].textContent, /09:00/);
  const reply = all(message).find(
    (node) => node.className === "webchat-suggestions webchat-appointment-replies",
  ).children[0];
  assert.equal(reply.textContent, "Choose this time");
  assert.equal(reply.dataset.chatSuggestion, "Tue, 8 Sept 2026, 10:00");
});

test("alternative provenance reaches the customer as ordinary conversation, not a card", () => {
  const message = renderMessage({
    role: "assistant",
    text: [
      "No test-drive appointments matched the requested schedule. "
        + "Here are the next available times for the same selected vehicle.",
      "Would this time suit you?",
    ].join("\n\n"),
    blocks: [
      {
        type: "paragraph",
        segments: [{
          type: "text",
          text: "No test-drive appointments matched the requested schedule. "
            + "Here are the next available times for the same selected vehicle.",
        }],
      },
      {
        type: "paragraph",
        segments: [{ type: "text", text: "Would this time suit you?" }],
      },
    ],
    viewType: "choice_list",
    view: {
      collectionPresentation: {
        schemaVersion: 1,
        layout: "bullet_list",
        purpose: "choice",
        items: [{
          label: "Mon, 7 Sept 2026, 10:00",
          description: "Stockport · 2020 BMW 3 Series 320d M Sport",
        }],
      },
      choiceReplies: [{
        label: "Choose this time",
        text: "Mon, 7 Sept 2026, 10:00",
      }],
    },
  });

  const notices = all(message).filter((node) => node.className === "webchat-alternative-offer");
  const choices = all(message).filter((node) => node.className === "webchat-collection-list");
  assert.equal(notices.length, 0);
  assert.equal(choices.length, 1);
  const prose = all(message).map((node) => node.textContent).join(" ");
  const choiceText = all(choices[0]).map((node) => node.textContent).join(" ");
  assert.equal(all(choices[0]).some((node) => node.tagName === "BUTTON"), false);
  assert.doesNotMatch(prose, /Alternative offered/);
  assert.match(prose, /same selected vehicle/);
  assert.match(prose, /2020 BMW 3 Series/);
  assert.match(choiceText, /Mon, 7 Sept 2026, 10:00/);
  const reply = all(message).find(
    (node) => node.className === "webchat-suggestions webchat-appointment-replies",
  ).children[0];
  assert.equal(reply.textContent, "Choose this time");
  assert.equal(reply.dataset.chatSuggestion, "Mon, 7 Sept 2026, 10:00");
  assert.equal(all(message).some((node) => node.className === "webchat-collection-replies"), false);
  assert.equal(message.children[0].className.includes("webchat-collection-lead"), true);
  assert.match(all(message.children[0]).map((node) => node.textContent).join(" "), /did not match|matched the requested schedule/);
  assert.equal(message.children[1].className, "webchat-assistant-bubble webchat-collection-bubble");
  assert.ok(all(message.children[1]).includes(choices[0]));
  assert.equal(message.children[2].className, "webchat-message-segments webchat-assistant-bubble");
  assert.match(all(message.children[2]).map((node) => node.textContent).join(" "), /Would this time suit you/);
  assert.equal(message.children[3].className, "webchat-suggestions webchat-appointment-replies");
});

test("every detailed choice namespace keeps grounded lead-in before choices and its prompt after", () => {
  const cases = [
    ["vehicle", "choice_list", "clarification"],
    ["offer", "choice_list", "clarification"],
    ["dealership", "choice_list", "choice"],
    ["service", "service_list", "choice"],
    ["recovery", "choice_list", "choice"],
    ["reference", "choice_list", "clarification"],
    ["tool_result", "choice_list", "choice"],
  ];

  cases.forEach(([entityType, viewType, purpose]) => {
    const message = renderMessage({
      role: "assistant",
      text: "The requested option is unavailable.\n\nWhich option would you like?",
      blocks: [
        {
          type: "paragraph",
          segments: [{ type: "text", text: "The requested option is unavailable." }],
        },
        {
          type: "paragraph",
          segments: [{ type: "text", text: "Which option would you like?" }],
        },
      ],
      viewType,
      view: {
        choiceEntityType: entityType,
        collectionPresentation: {
          schemaVersion: 1,
          layout: "bullet_list",
          purpose,
          items: [{ label: "Available option", description: "Trusted detail" }],
        },
      },
    });

    assert.equal(message.children[0].className.includes("webchat-collection-lead"), true);
    assert.equal(message.children[1].className.includes("webchat-collection-bubble"), true);
    assert.equal(message.children[2].className.includes("webchat-assistant-bubble"), true);
    assert.match(all(message.children[0]).map((node) => node.textContent).join(" "), /unavailable/);
    assert.match(all(message.children[1]).map((node) => node.textContent).join(" "), /Available option/);
    assert.match(all(message.children[2]).map((node) => node.textContent).join(" "), /Which option/);
  });
});

test("restored detailed choices preserve lead-in, trusted list, and final prompt order", () => {
  const message = renderMessage({
    role: "assistant",
    text: "The requested location is unavailable.\n\nNorthstar Stockport\n\nWould Stockport work?",
    blocks: [
      {
        type: "paragraph",
        segments: [{ type: "text", text: "The requested location is unavailable." }],
      },
      {
        type: "list",
        items: [{ segments: [{ type: "text", text: "Northstar Stockport" }] }],
      },
      {
        type: "paragraph",
        segments: [{ type: "text", text: "Would Stockport work?" }],
      },
    ],
    viewType: "choice_list",
    view: {
      collectionPresentationRendered: true,
      choiceEntityType: "dealership",
      collectionPresentation: {
        schemaVersion: 1,
        layout: "bullet_list",
        purpose: "choice",
        items: [{ label: "Northstar Stockport" }],
      },
    },
  });

  assert.equal(message.children[0].className.includes("webchat-collection-lead"), true);
  assert.match(all(message.children[0]).map((node) => node.textContent).join(" "), /unavailable/);
  assert.equal(message.children[1].className.includes("webchat-collection-bubble"), true);
  assert.match(all(message.children[1]).map((node) => node.textContent).join(" "), /Northstar Stockport/);
  assert.match(all(message.children[2]).map((node) => node.textContent).join(" "), /Would Stockport work/);
});

test("restored detailed choices keep their existing bullets without adding controls", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which dealership?\n- Northstar Bolton\n- Northstar Stockport",
    blocks: [
      {
        type: "paragraph",
        segments: [{ type: "text", text: "Which dealership?" }],
      },
      {
        type: "list",
        items: [
          { segments: [{ type: "text", text: "Northstar Bolton" }] },
          { segments: [{ type: "text", text: "Northstar Stockport" }] },
        ],
      },
    ],
    viewType: "choice_list",
    view: {
      collectionPresentationRendered: true,
      choiceEntityType: "dealership",
      collectionPresentation: {
        schemaVersion: 1,
        layout: "chip_grid",
        purpose: "choice",
        items: [
          { label: "Northstar Bolton", description: "Bolton" },
          { label: "Northstar Stockport", description: "Stockport" },
        ],
      },
    },
  });

  assert.equal(all(message).filter((node) => node.className === "webchat-message-list").length, 1);
  assert.equal(all(message).filter((node) => node.className === "webchat-collection-list").length, 0);
  assert.equal(all(message).filter((node) => node.className === "webchat-suggestion").length, 0);
  assert.equal(
    all(message).filter((node) => node.className.includes("webchat-collection-chip")).length,
    0,
  );
  assert.equal(message.children[0].className, "webchat-assistant-bubble webchat-collection-bubble");
  assert.ok(all(message.children[1]).some((node) => node.textContent === "Which dealership?"));
});

test("confirmation shows local private details with protected decision chips", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Review",
    viewType: "confirmation",
    view: {
      draftId: "draft-1",
      kind: "test_drive",
      status: "awaiting_confirmation",
      summary: { vehicleId: "veh-001" },
      localDetails: { fullName: "Rohit Singh", email: "rohit@example.com", phone: "07123456789" },
    },
  });
  const nodes = all(message);
  assert.ok(nodes.some((node) => node.textContent === "Rohit Singh"));
  assert.ok(nodes.some((node) => node.textContent === "rohit@example.com"));
  const controls = nodes.filter((node) => node.tagName === "BUTTON");
  assert.deepEqual(controls.map((node) => node.textContent), ["Confirm details", "Edit details"]);
  assert.equal(controls[0].dataset.confirmIntent, "confirm");
  assert.equal(controls[1].dataset.confirmEdit, "true");
});

test("part-exchange confirmation renders every schema-derived protected review field", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Review",
    viewType: "confirmation",
    view: {
      draftId: "draft-part-exchange",
      kind: "part_exchange",
      status: "awaiting_confirmation",
      summary: { dealershipId: "northstar-bolton" },
      dealerships: [{ id: "northstar-bolton", name: "Northstar Bolton" }],
      localReviewDetails: [
        { field: "registration", label: "Registration", value: "AB12 CDE" },
        { field: "mileage", label: "Mileage", value: "25,000 miles" },
        { field: "condition", label: "Condition", value: "Good" },
        { field: "fullName", label: "Full name", value: "Alex Morgan" },
      ],
    },
  });
  const nodes = all(message);

  for (const value of ["AB12 CDE", "25,000 miles", "Good", "Alex Morgan"]) {
    assert.ok(nodes.some((node) => node.textContent === value), value);
  }
});

test("confirmation reviews hide selectors and format timestamps across workflow kinds", () => {
  const appointment = "2026-09-12T10:00:00+01:00";
  const dealership = {
    id: "northstar-manchester",
    name: "Northstar Manchester",
    town: "Manchester",
  };
  const vehicle = { id: "veh-001", year: 2025, make: "BMW", model: "3 Series" };
  const views = [
    {
      kind: "test_drive",
      summary: {
        vehicleId: vehicle.id,
        slotId: "test-slot-001",
        selectedDealershipId: dealership.id,
        selectedDealershipName: dealership.name,
        selectedStartsAt: appointment,
      },
      vehicle,
    },
    {
      kind: "workshop_booking",
      summary: {
        dealershipId: dealership.id,
        serviceTypeId: "mot",
        slotId: "workshop-slot-001",
        selectedDealershipId: dealership.id,
        selectedDealershipName: dealership.name,
        selectedServiceTypeId: "mot",
        selectedServiceName: "MOT",
        selectedStartsAt: appointment,
      },
    },
    {
      kind: "workshop_amend",
      summary: {
        bookingReference: "WORK-12345",
        currentAppointment: "2026-09-10T09:00:00+01:00",
        selectedDealershipId: dealership.id,
        selectedDealershipName: dealership.name,
        selectedServiceTypeId: "mot",
        selectedServiceName: "MOT",
        selectedStartsAt: appointment,
      },
    },
    { kind: "workshop_cancel", summary: { bookingReference: "WORK-12345", currentAppointment: appointment, dealership: dealership.name, service: "MOT" } },
    { kind: "sales_enquiry", summary: { vehicleId: vehicle.id, dealershipId: dealership.id, enquiryType: "vehicle", message: "Please contact me." }, vehicle, dealerships: [dealership] },
    { kind: "vehicle_interest", summary: { vehicleId: vehicle.id }, vehicle },
    { kind: "callback", summary: { dealershipId: dealership.id, department: "sales", reason: "New car", vehicleId: vehicle.id }, dealerships: [dealership] },
    { kind: "dealership_message", summary: { dealershipId: dealership.id, department: "service", subject: "Booking", message: "Please call me." }, dealerships: [dealership] },
    { kind: "part_exchange", summary: { dealershipId: dealership.id }, dealerships: [dealership] },
  ];

  views.forEach((view, index) => {
    const message = renderMessage({
      role: "assistant",
      text: "Please review and confirm these details.",
      viewType: "confirmation",
      view: { version: 1, draftId: `draft-${index}`, status: "awaiting_confirmation", ...view },
    });
    const text = all(message).map((node) => node.textContent).filter(Boolean).join(" ");
    assert.ok(!text.includes("Dealership Id"), view.kind);
    assert.ok(!text.includes("Vehicle Id"), view.kind);
    assert.ok(!text.includes("Slot Id"), view.kind);
    assert.ok(!text.includes(dealership.id), view.kind);
    assert.ok(!text.includes(vehicle.id), view.kind);
    assert.ok(!text.includes(appointment), view.kind);
  });
});

test("grounded confirmation and receipt cards remain visual-only", () => {
  const confirmation = renderMessage({
    role: "assistant",
    text: "Please review this request.",
    viewType: "grounded_presentation",
    view: {
      cards: [{
        reference: "card:result-one:confirmation",
        type: "confirmation",
        data: {
          draftId: "draft-1",
          kind: "test_drive",
          status: "awaiting_confirmation",
          summary: { vehicleId: "veh-001" },
          localDetails: { fullName: "Rohit Singh", email: "rohit@example.com" },
        },
      }],
      quickReplies: [],
    },
  });
  const receipt = renderMessage({
    role: "assistant",
    text: "Your request is complete.",
    viewType: "grounded_presentation",
    view: {
      cards: [{
        reference: "card:result-two:receipt",
        type: "receipt",
        data: { kind: "test_drive", reference: "TEST-1", status: "confirmed" },
      }],
      quickReplies: [],
    },
  });
  const nodes = [...all(confirmation), ...all(receipt)];
  assert.ok(nodes.some((node) => node.textContent === "Rohit Singh"));
  assert.equal(nodes.filter((node) => ["BUTTON", "A", "INPUT", "FORM", "SELECT", "TEXTAREA", "DETAILS"].includes(node.tagName)).length, 0);
});

test("workshop receipt is read-only and contains no amend or cancellation buttons", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Booked",
    viewType: "receipt",
    view: { kind: "workshop_booking", reference: "WORK-1", startsAt: "2026-08-27T16:00:00Z" },
  });
  assert.equal(all(message).filter((node) => node.tagName === "BUTTON").length, 0);
  assert.equal(all(message).filter((node) => node.tagName === "DETAILS").length, 0);
});

test("verified and cancelled bookings render status-specific replies outside the card", () => {
  const cases = [
    {
      status: "confirmed",
      labels: ["Edit booking", "Cancel booking"],
      messages: ["edit this workshop booking", "cancel this workshop booking"],
    },
    {
      status: "cancelled",
      labels: ["Book an appointment", "Find another booking"],
      messages: ["book a new workshop appointment", "find another existing workshop booking"],
    },
  ];

  cases.forEach(({ status, labels, messages }) => {
    const message = renderMessage({
      role: "assistant",
      text: "What would you like to do next?",
      viewType: "grounded_presentation",
      view: {
        cards: [{
          type: "booking",
          data: {
            status,
            reference: "WORK-1",
            startsAt: "2026-09-07T11:00:00Z",
            dealershipName: "Northstar Manchester",
            serviceTypeName: "MOT",
          },
        }],
        quickReplies: labels.map((label, index) => ({ label, message: messages[index] })),
      },
    });
    const nodes = all(message);
    const card = nodes.find((node) => node.className === "webchat-card webchat-workshop-booking-card");
    const replies = nodes.filter((node) => node.className === "webchat-suggestion");

    assert.ok(card);
    assert.equal(all(card).filter((node) => node.tagName === "BUTTON").length, 0);
    assert.deepEqual(replies.map((node) => node.textContent), labels);
    assert.deepEqual(replies.map((node) => node.dataset.chatSuggestion), messages);
    assert.equal(replies.every((node) => node.dataset.chatSuggestionAction === undefined), true);
  });
});

test("an intermediate selected workshop appointment card is not rendered", () => {
  const message = renderMessage({
    role: "assistant",
    text: "I’ve selected this workshop appointment.",
    viewType: "selected_workshop_appointment",
    view: {
      id: "ws-slot-0001",
      serviceName: "MOT",
      dealershipName: "Northstar Manchester",
      startsAt: "2026-09-12T10:00:00+01:00",
    },
  });
  assert.equal(all(message).some((node) => node.className === "webchat-selected-appointment"), false);
  assert.ok(all(message).some((node) => node.textContent === "I’ve selected this workshop appointment."));
});

test("secure collection does not render an intermediate appointment card", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Please share your vehicle and contact details securely.",
    viewType: "secure_input",
    view: {
      kind: "workshop_booking",
      secureInputReady: true,
      summary: {
        selectedStartsAt: "2026-09-12T10:00:00+01:00",
        selectedDealershipName: "Northstar Manchester",
        selectedServiceName: "MOT",
      },
    },
  });
  const nodes = all(message);
  assert.equal(nodes.some((node) => node.textContent === "Selected appointment"), false);
  assert.equal(nodes.some((node) => node.textContent === "Northstar Manchester"), false);
  assert.equal(nodes.some((node) => node.className === "webchat-selected-appointment"), false);
  assert.equal(nodes.some((node) => /Reply in the chat box below/.test(node.textContent)), false);
});

test("no workflow renders a card while it is collecting details", () => {
  const kinds = [
    "test_drive",
    "workshop_booking",
    "sales_enquiry",
    "vehicle_interest",
    "callback",
    "dealership_message",
    "part_exchange_estimate",
    "part_exchange",
    "booking_lookup",
  ];
  kinds.forEach((kind) => {
    const message = renderMessage({
      role: "assistant",
      text: "What detail should I collect next?",
      viewType: "secure_input",
      view: {
        kind,
        status: "collecting",
        secureInputReady: true,
        summary: {
          selectedStartsAt: "2026-09-12T10:00:00+01:00",
          selectedDealershipName: "Northstar Manchester",
          selectedServiceName: "MOT",
          vehicleId: "veh-001",
        },
        vehicle: { id: "veh-001", make: "BMW", model: "3 Series" },
      },
    });
    const nodes = all(message);
    assert.equal(nodes.some((node) => ["ARTICLE", "SECTION"].includes(node.tagName)), false);
    assert.equal(nodes.some((node) => node.className === "webchat-collector-marker"), false);
  });
});

test("a confirmation card is rendered only for the final review state", () => {
  const collecting = renderMessage({
    role: "assistant",
    text: "I still need one detail.",
    viewType: "confirmation",
    view: { draftId: "draft-1", kind: "callback", status: "collecting" },
  });
  const reviewing = renderMessage({
    role: "assistant",
    text: "Please review the request.",
    viewType: "confirmation",
    view: { draftId: "draft-2", kind: "callback", status: "awaiting_confirmation" },
  });
  assert.equal(all(collecting).some((node) => node.className.includes("webchat-confirmation")), false);
  assert.equal(all(reviewing).some((node) => node.className.includes("webchat-confirmation")), true);
});

test("a detailed service choice renders bullets before a separate final question bubble", () => {
  const message = renderMessage({
    role: "assistant",
    text: "Which workshop service would you like to book? Choose one below.",
    viewType: "service_list",
    view: {
      selectionOnly: true,
      items: [{ id: "mot", name: "MOT" }, { id: "full-service", name: "Full service" }],
      collectionPresentation: {
        schemaVersion: 1,
        layout: "bullet_list",
        purpose: "choice",
        items: [
          { label: "MOT", description: "Annual MOT inspection." },
          { label: "Full service", description: "Comprehensive annual vehicle service." },
        ],
      },
      suggestions: [{ label: "MOT", text: "Book service: MOT" }],
    },
  });
  const nodes = all(message);
  const list = nodes.find((node) => node.className === "webchat-collection-list");
  assert.ok(list);
  assert.equal(list.children.length, 2);
  assert.ok(nodes.some((node) => node.textContent === "MOT"));
  assert.ok(nodes.some((node) => node.textContent.includes("Annual MOT inspection")));
  assert.equal(nodes.filter((node) => node.tagName === "BUTTON").length, 0);
  assert.ok(all(message.children[0]).includes(list));
  assert.ok(all(message.children[1]).some(
    (node) => node.textContent === "Which workshop service would you like to book? Choose one here.",
  ));
});

test("an informational collection uses the same semantic bullet contract", () => {
  const message = renderMessage({
    role: "assistant",
    text: "We currently provide these workshop services:",
    viewType: "service_list",
    view: {
      collectionPresentation: {
        schemaVersion: 1,
        layout: "bullet_list",
        purpose: "information",
        items: [
          { label: "Brake inspection", description: "Brake condition and performance inspection." },
          { label: "Diagnostic inspection", description: "Investigation of a warning light or fault." },
        ],
      },
    },
  });
  const nodes = all(message);
  assert.equal(nodes.filter((node) => node.tagName === "UL").length, 1);
  assert.ok(all(message.children[0]).some((node) => node.tagName === "UL"));
  assert.deepEqual(
    nodes.filter((node) => node.tagName === "STRONG").map((node) => node.textContent),
    ["Brake inspection", "Diagnostic inspection"],
  );
  assert.equal(nodes.filter((node) => ["BUTTON", "A", "INPUT", "FORM"].includes(node.tagName)).length, 0);
});
