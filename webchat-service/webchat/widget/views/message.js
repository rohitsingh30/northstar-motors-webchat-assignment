// Stable facade and closed view dispatcher. Rendering families live in focused modules.
import { textElement } from "../core/dom.js";
import { messageContent } from "./message-content.js";
import { suggestionChips } from "./suggestions.js";
import {
  comparisonTable,
  vehicleAvailabilityCard,
  vehicleCard,
} from "./vehicle.js";
import {
  testDriveSlotPicker,
  workshopSlotPicker,
} from "./appointments.js";
import {
  dealershipCard,
  openingHoursCard,
  offerCard,
  serviceCard,
} from "./information-cards.js";
import {
  businessInformationCard,
  confirmationCard,
  draftCard,
  partExchangeEstimateCard,
  partExchangeEstimateForm,
  privateLookupForm,
  receiptCard,
  workshopBookingDetailsCard,
} from "./workflow-cards.js";

export {
  bindBookedTestDriveAction,
  inlineBookedTestDrive,
  inlineBookingReceipt,
  inlineTestDriveConfirmation,
  inlineWorkshopConfirmation,
  inlineWorkshopReceipt,
  setWorkshopFlowContent,
  setBookedTestDriveActionState,
  testDriveDetailsForm,
  testDriveSlotPicker,
  workshopDetailsForm,
  workshopSlotPicker,
} from "./appointments.js";
export {
  businessInformationCard,
  inlineOfferEnquiryForm,
  receiptCard,
  setOfferEnquiryActionState,
} from "./workflow-cards.js";

export function richSummary(message) {
  const count = message.view?.items?.length || 0;
  if (message.viewType === "vehicle_comparison") {
    if (!count) return "I couldn't find two vehicles to compare.";
    return `Side-by-side comparison of ${count} selected ${count === 1 ? "vehicle" : "vehicles"}.`;
  }
  if (message.viewType === "vehicle_availability") {
    return "I checked the vehicle’s current availability.";
  }
  if (message.viewType === "vehicle_details") {
    return "Vehicle details are shown below.";
  }
  if (message.viewType === "vehicle_list") {
    const total = message.view?.total ?? count;
    const visible = Math.min(count, total);
    const page = message.view?.page || 1;
    const pageSize = message.view?.pageSize || visible;
    const filters = String(message.view?.filterSummary || "")
      .split(";")
      .map((filter) => filter.trim())
      .filter(Boolean);
    const filterSuffix = filters.length
      ? `\nCurrent filters:\n- ${filters.join("\n- ")}`
      : "";
    if (message.view?.scope === "currentPage") {
      return (visible < total
        ? `Showing ${visible} of ${total} matching vehicles from the current page.`
        : `Showing ${visible} matching ${visible === 1 ? "vehicle" : "vehicles"} from the current page.`) + filterSuffix;
    }
    if (page > 1 && visible) {
      const start = (page - 1) * pageSize + 1;
      const end = Math.min(start + visible - 1, total);
      return `Showing more vehicles: ${start}–${end} of ${total} available.${filterSuffix}`;
    }
    if (visible < total) {
      return `Showing ${visible} of ${total} available vehicles matching your request.${filterSuffix}`;
    }
    return `Showing ${visible} available ${visible === 1 ? "vehicle" : "vehicles"} matching your request.${filterSuffix}`;
  }
  if (message.viewType === "opening_hours") {
    if (message.view?.holidayOnly) return "Published holiday opening hours are shown below.";
    return `${message.view?.day || "Current"} opening hours are shown below.`;
  }
  if (message.viewType === "dealership_list") return "Dealership details are shown below.";
  if (message.viewType === "workshop_location_list") return "Workshop locations are shown below.";
  if (message.viewType === "service_list") return "Supported workshop services are shown below.";
  if (message.viewType === "offer_list") {
    if (!message.view?.detailView && count > 3) {
      return `${count} current published offers are available. The first 3 are shown initially.`;
    }
    return "Current published offers are shown below.";
  }
  if (message.viewType === "slot_list") {
    return count
      ? "Available workshop times are shown below."
      : message.view?.emptyMessage || "No matching workshop times are currently available.";
  }
  if (message.viewType === "test_drive_slot_picker") {
    return count
      ? "Available test-drive times are shown below."
      : message.view?.emptyMessage ||
          "No online test-drive times are currently available for this vehicle.";
  }
  if (message.viewType === "draft") return "Please complete the form below.";
  if (message.viewType === "part_exchange_estimate_form") return "Please complete the three vehicle details below.";
  if (message.viewType === "confirmation") return "Please review the details below.";
  if (message.viewType === "part_exchange_estimate") return "Your indicative part-exchange range is shown below.";
  if (message.viewType === "business_information") {
    const topics = {
      finance: "finance",
      privacy: "privacy",
      part_exchange: "part-exchange",
    };
    const topic = topics[message.view?.topic];
    return topic
      ? `Current Northstar ${topic} information is shown below.`
      : "Current Northstar information is shown below.";
  }
  return message.text || "";
}

const STRUCTURED_VIEW_TYPES = new Set([
  "business_information",
  "vehicle_list",
  "vehicle_details",
  "vehicle_comparison",
  "vehicle_availability",
  "opening_hours",
  "dealership_list",
  "workshop_location_list",
  "service_list",
  "offer_list",
  "slot_list",
  "test_drive_slot_picker",
  "draft",
  "confirmation",
  "part_exchange_estimate_form",
  "part_exchange_estimate",
  "workshop_booking_details",
]);

function renderVehicleList(item, view) {
  if (Array.isArray(view?.items)) {
    const cards = document.createElement("div");
    cards.className = "webchat-cards";
    view.items.slice(0, 3).forEach((vehicle) => cards.append(vehicleCard(vehicle)));
    item.append(cards);
  }
}

function renderVehicleComparison(item, view) {
  if (Array.isArray(view?.items)) item.append(comparisonTable(view.items));
}

function renderVehicleAvailability(item, view) {
  if (view) item.append(vehicleAvailabilityCard(view));
}

function renderTestDriveSlots(item, view) {
  if (!view) return;
  const first = Array.isArray(view.items) ? view.items[0] : null;
  const vehicle = view.vehicle || {};
  const flow = document.createElement("div");
  flow.className = "webchat-booking-flow webchat-test-drive-flow-standalone";
  flow.dataset.bookingFlow = "true";
  flow.testDriveOptions = view;
  flow.vehicleLabel = [
    vehicle.make || first?.make,
    vehicle.model || first?.model,
  ].filter(Boolean).join(" ") || "Selected vehicle";
  flow.append(testDriveSlotPicker(view, { inline: true }));
  item.classList.add("webchat-test-drive-flow-message");
  item.append(flow);
}

function renderServiceList(item, view) {
  if (Array.isArray(view?.items)) {
    const cards = document.createElement("div");
    cards.className = "webchat-cards webchat-service-list";
    view.items.slice(0, 12).forEach((service) => {
      cards.append(serviceCard(service, { dealershipId: view.dealershipId }));
    });
    item.append(cards);
  }
}

function renderWorkshopSlots(item, view) {
  if (Array.isArray(view?.items) && view.items.length) {
    const flow = document.createElement("div");
    flow.className = "webchat-booking-flow webchat-workshop-flow-standalone";
    flow.dataset.workshopFlow = "true";
    flow.workshopOptions = view;
    flow.append(workshopSlotPicker(view));
    item.append(flow);
  }
}

function renderCardList(item, view, cardFactory) {
  if (Array.isArray(view?.items)) {
    const cards = document.createElement("div");
    cards.className = "webchat-cards";
    view.items.slice(0, 8).forEach((value) => cards.append(cardFactory(value)));
    item.append(cards);
  }
}

function renderOfferList(item, view) {
  if (!Array.isArray(view?.items)) return;
  const values = view.items.slice(0, 8);
  const cards = document.createElement("div");
  cards.className = "webchat-cards";
  const collapsedCount = view.detailView === true ? values.length : Math.min(3, values.length);
  const offerCards = values.map((value) => (
    offerCard(value, { detailed: view.detailView === true })
  ));
  const showCards = (expanded) => {
    cards.replaceChildren(
      ...offerCards.slice(0, expanded ? offerCards.length : collapsedCount),
    );
  };
  showCards(false);
  item.append(cards);
  if (values.length > collapsedCount) {
    const remaining = values.length - collapsedCount;
    const collapsedLabel = `Show ${remaining} more ${remaining === 1 ? "offer" : "offers"}`;
    const toggle = textElement("button", "webchat-offer-list-toggle", collapsedLabel);
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") !== "true";
      showCards(expanded);
      toggle.textContent = expanded ? "Show fewer offers" : collapsedLabel;
      toggle.setAttribute("aria-expanded", String(expanded));
    });
    item.append(toggle);
  }
  if (view.financeNotice) item.append(textElement("small", "", view.financeNotice));
}

const MESSAGE_VIEW_RENDERERS = {
  business_information(item, view) {
    if (view) item.append(businessInformationCard(view));
  },
  vehicle_list: renderVehicleList,
  vehicle_details(item, view) {
    if (view?.vehicle) item.append(vehicleCard(view.vehicle));
  },
  vehicle_availability: renderVehicleAvailability,
  vehicle_comparison: renderVehicleComparison,
  test_drive_slot_picker: renderTestDriveSlots,
  service_list: renderServiceList,
  slot_list: renderWorkshopSlots,
  offer_list: renderOfferList,
  dealership_list(item, view) {
    renderCardList(item, view, dealershipCard);
  },
  workshop_location_list(item, view) {
    renderCardList(item, view, dealershipCard);
  },
  opening_hours(item, view) {
    renderCardList(item, view, openingHoursCard);
  },
  confirmation(item, view) {
    if (view?.draftId) item.append(confirmationCard(view));
  },
  draft(item, view) {
    if (view?.draftId) item.append(draftCard(view));
  },
  private_booking_lookup(item, view) {
    item.append(privateLookupForm(view || {}));
  },
  workshop_booking_details(item, view) {
    if (view) item.append(workshopBookingDetailsCard(view));
  },
  part_exchange_estimate_form(item, view) {
    item.append(partExchangeEstimateForm(view || {}));
  },
  receipt(item, view) {
    if (view) item.append(receiptCard(view));
  },
  part_exchange_estimate(item, view) {
    if (view) item.append(partExchangeEstimateCard(view));
  },
};

export function renderMessage(message) {
  const item = document.createElement("li");
  item.className = `webchat-message webchat-message--${message.role}`;
  if (message.viewType) item.classList.add("webchat-message--rich");

  // Rich results already contain the facts in cards. Keep only a short
  // introduction so the narrow widget does not repeat an entire text list.
  const text = STRUCTURED_VIEW_TYPES.has(message.viewType)
    ? richSummary(message)
    : message.text;
  item.append(messageContent(message, text));

  const renderer = Object.prototype.hasOwnProperty.call(
    MESSAGE_VIEW_RENDERERS,
    message.viewType,
  )
    ? MESSAGE_VIEW_RENDERERS[message.viewType]
    : null;
  renderer?.(item, message.view);
  if (Array.isArray(message.view?.suggestions) && message.view.suggestions.length) {
    const serviceChoices = message.viewType === "service_list";
    const completeChoiceSet = serviceChoices || message.view.completeChoiceSet === true;
    const suggestions = suggestionChips(
      message.view.suggestions,
      {
        allowMany: completeChoiceSet,
        className: serviceChoices ? "webchat-service-suggestions" : "",
      },
    );
    if (suggestions.childElementCount) item.append(suggestions);
  }
  return item;
}

export function renderWorkflowCard(view, viewType = null) {
  if (viewType === "part_exchange_estimate") return partExchangeEstimateCard(view);
  if (viewType === "private_booking_lookup") return privateLookupForm(view || {});
  return view?.status === "awaiting_confirmation" ? confirmationCard(view) : draftCard(view);
}
