// Stable facade and closed view dispatcher. Rendering families live in focused modules.
import { textElement } from "../core/dom.js?v=20260904.2";
import { messageContent } from "./message-content.js?v=20260905.1";
import { suggestionChips } from "./suggestions.js?v=20260905.2";
import {
  comparisonTable,
  vehicleAvailabilityCard,
  vehicleCard,
} from "./vehicle.js?v=20260904.4";
import {
  dealershipCard,
  openingHoursCard,
  offerCard,
} from "./information-cards.js?v=20260904.2";
import {
  collectionPresentation,
  structuredCollectionList,
} from "./structured-collection.js?v=20260905.3";
import {
  businessInformationCard,
  confirmationCard,
  partExchangeEstimateCard,
  receiptCard,
  workshopBookingDetailsCard,
} from "./workflow-cards.js?v=20260906.1";
import { formatAppointment } from "./appointments.js?v=20260905.1";
export { businessInformationCard, receiptCard } from "./workflow-cards.js?v=20260906.1";

function renderVehicleList(item, view) {
  if (Array.isArray(view?.items)) {
    const vehicles = view.items.slice(0, 3);
    const cards = document.createElement("div");
    cards.className = "webchat-cards";
    vehicles.forEach((vehicle, index) => {
      cards.append(vehicleCard(resultCardValue(vehicle, index, vehicles.length)));
    });
    item.append(cards);
  }
}

function resultCardValue(value, index, total) {
  return {
    ...value,
    optionNumber: total > 1 ? index + 1 : undefined,
  };
}

function renderVehicleComparison(item, view) {
  if (Array.isArray(view?.items)) item.append(comparisonTable(view.items));
}

function renderVehicleAvailability(item, view) {
  if (view) item.append(vehicleAvailabilityCard(view));
}

function renderServiceList(item, view) {
  const presentation = collectionPresentation(view);
  const collectionAlreadyRendered = (
    view?.collectionPresentationRendered === true
    && presentation.layout !== "chip_grid"
  );
  if (!collectionAlreadyRendered) {
    const list = structuredCollectionList(view);
    if (list) {
      const owner = presentation.layout === "bullet_list"
        ? item.querySelector(".webchat-assistant-bubble") || item
        : item;
      owner.append(list);
    }
  }
  if (presentation.layout === "bullet_list" && presentation.purpose === "choice") {
    const replies = suggestionChips(view?.choiceReplies || [], {
      allowMany: true,
      className: "webchat-appointment-replies",
    });
    if (replies.childElementCount) item.append(replies);
  }
}

function isBulletChoice(view = {}) {
  const presentation = collectionPresentation(view);
  return (
    presentation.layout === "bullet_list"
    && ["choice", "clarification"].includes(presentation.purpose)
  );
}

function appendChoiceReplies(item, view = {}) {
  const replies = suggestionChips(view?.choiceReplies || [], {
    allowMany: true,
    className: "webchat-appointment-replies",
  });
  if (replies.childElementCount) item.append(replies);
}

function orderNeutralChoicePrompt(message) {
  const neutral = (value) => (
    typeof value === "string" ? value.replace(/\bbelow\b/gi, "here") : value
  );
  const neutralSegments = (segments) => (
    Array.isArray(segments)
      ? segments.map((segment) => (
        segment?.type === "text" ? { ...segment, text: neutral(segment.text) } : segment
      ))
      : segments
  );
  return {
    ...message,
    text: neutral(message.text),
    segments: neutralSegments(message.segments),
    blocks: Array.isArray(message.blocks)
      ? message.blocks.map((block) => (
        block?.type === "paragraph"
          ? { ...block, segments: neutralSegments(block.segments) }
          : block?.type === "list"
            ? {
              ...block,
              items: block.items?.map((entry) => ({
                ...entry,
                segments: neutralSegments(entry.segments),
              })),
            }
            : block
      ))
      : message.blocks,
  };
}

function renderBulletChoiceBeforePrompt(item, message, promptContent) {
  const view = message.view || {};
  let list = null;
  let leadIn = null;
  let content = promptContent;
  const blocks = Array.isArray(message.blocks) ? message.blocks : [];
  let collectionRenderedInBlocks = false;

  if (view.collectionPresentationRendered === true && blocks.length) {
    const listBlocks = blocks.filter((block) => block?.type === "list");
    if (listBlocks.length) {
      list = messageContent({ ...message, blocks: listBlocks }, "");
      collectionRenderedInBlocks = true;
    }
  }
  if (!list) list = structuredCollectionList(view);
  if (!list) {
    item.append(content);
    return;
  }

  // A choice-owning message can contain grounded context before its final question. Preserve
  // those explicit paragraph blocks ahead of the application-owned collection; only the last
  // paragraph is the trailing prompt. This is structural block ordering, not prose parsing, and
  // therefore applies equally to appointments, services, entities, and restored conversations.
  let finalParagraphIndex = -1;
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    if (blocks[index]?.type !== "paragraph") continue;
    finalParagraphIndex = index;
    break;
  }
  if (finalParagraphIndex > 0) {
    const leadInBlocks = blocks
      .slice(0, finalParagraphIndex)
      .filter((block) => block?.type !== "list");
    const promptBlocks = blocks
      .slice(finalParagraphIndex)
      .filter((block) => block?.type !== "list");
    if (leadInBlocks.length && promptBlocks.length) {
      leadIn = messageContent({ ...message, blocks: leadInBlocks }, "");
      leadIn.classList.add("webchat-assistant-bubble", "webchat-collection-lead");
      content = messageContent({ ...message, blocks: promptBlocks }, message.text);
      content.classList.add("webchat-assistant-bubble");
    }
  } else if (collectionRenderedInBlocks) {
    const promptBlocks = blocks.filter((block) => block?.type !== "list");
    if (promptBlocks.length) {
      content = messageContent({ ...message, blocks: promptBlocks }, message.text);
      content.classList.add("webchat-assistant-bubble");
    }
  }

  const details = document.createElement("div");
  details.className = "webchat-assistant-bubble webchat-collection-bubble";
  details.append(list);
  item.classList.add("webchat-message--collection-prompt");
  item.append(...[leadIn, details, content].filter(Boolean));
  appendChoiceReplies(item, view);
}

function appointmentChoiceReplies(slots) {
  const choices = slots
    .map((slot) => ({
      label: slot.displayLabel || formatAppointment(slot.startsAt),
      location: slot.dealershipTown || slot.dealershipName || "",
    }))
    .filter((choice) => choice.label);
  if (choices.length === 1) return [{ label: "Choose this time", text: choices[0].label }];
  const counts = new Map();
  const replies = choices.map((choice) => {
    const short = choice.label.replace(
      /^(\w{3}), (\d+) (\w+) \d{4}, (\d{2}:\d{2})$/,
      "$1 $2 $3 · $4",
    );
    counts.set(short, (counts.get(short) || 0) + 1);
    return { label: short, text: choice.label, location: choice.location };
  });
  return replies.map((reply) => ({
    label: counts.get(reply.label) > 1 && reply.location
      ? `${reply.label} · ${reply.location}`
      : reply.label,
    text: reply.text,
  }));
}

function trustedSlotChoiceView(view = {}) {
  const slots = Array.isArray(view.items) ? view.items.slice(0, 12) : [];
  if (!slots.length) return null;
  return {
    collectionPresentation: {
      schemaVersion: 1,
      layout: "bullet_list",
      purpose: "choice",
      items: slots.map((slot) => {
        const context = [
          slot.dealershipTown || slot.dealershipName,
          slot.serviceTypeName || slot.serviceName,
          [slot.vehicleYear || slot.year, slot.make, slot.model, slot.variant]
            .filter((value) => value !== undefined && value !== null && String(value).trim())
            .join(" "),
        ].filter((value) => typeof value === "string" && value.trim());
        return {
          label: slot.displayLabel || formatAppointment(slot.startsAt),
          description: context.join(" · "),
        };
      }),
    },
    choiceReplies: appointmentChoiceReplies(slots),
  };
}

function renderTrustedSlotContext(item, view = {}) {
  const appointmentView = trustedSlotChoiceView(view);
  if (appointmentView) renderServiceList(item, appointmentView);
  renderGroundedReplies(item, view);
}

function renderCardList(item, view, cardFactory) {
  if (Array.isArray(view?.items)) {
    const values = view.items.slice(0, 8);
    const cards = document.createElement("div");
    cards.className = "webchat-cards";
    values.forEach((value, index) => {
      cards.append(cardFactory(resultCardValue(value, index, values.length)));
    });
    item.append(cards);
  }
}

function renderOfferList(item, view) {
  if (!Array.isArray(view?.items)) return;
  const values = view.items.slice(0, 8);
  const cards = document.createElement("div");
  cards.className = "webchat-cards";
  const collapsedCount = view.detailView === true ? values.length : Math.min(3, values.length);
  const offerCards = values.map((value, index) => (
    offerCard(
      resultCardValue(value, index, values.length),
      { detailed: view.detailView === true },
    )
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

function renderGroundedCards(item, view = {}) {
  const cards = Array.isArray(view.cards) ? view.cards : [];
  cards.forEach((card) => {
    const data = card?.data || {};
    const rendererByType = {
      vehicle_preview: () => {
        if (Array.isArray(data.items)) renderVehicleList(item, data);
        else item.append(vehicleCard(data.vehicle || data));
      },
      vehicle_comparison: () => renderVehicleComparison(item, data),
      offer: () => renderOfferList(item, data),
      dealership: () => renderCardList(item, data, dealershipCard),
      opening_hours: () => renderCardList(item, data, openingHoursCard),
      service: () => renderServiceList(item, data),
      valuation: () => item.append(partExchangeEstimateCard(data)),
      confirmation: () => {
        if (data.status === "awaiting_confirmation") item.append(confirmationCard(data));
      },
      receipt: () => item.append(receiptCard(data)),
      booking: () => item.append(workshopBookingDetailsCard(data)),
    };
    rendererByType[card?.type]?.();
  });
}

function renderGroundedReplies(item, view = {}) {
  const replies = Array.isArray(view.quickReplies)
    ? view.quickReplies.map((reply) => ({ ...reply, text: reply.message || reply.text }))
    : [];
  if (replies.length) {
    const suggestions = suggestionChips(replies, { balanceOptional: true });
    if (suggestions.childElementCount) item.append(suggestions);
  }
}

function renderGroundedPresentation(item, view = {}) {
  renderGroundedCards(item, view);
  renderGroundedReplies(item, view);
}

function renderConfirmationReplies(item) {
  const replies = document.createElement("div");
  replies.className = "webchat-suggestions webchat-confirmation-replies";
  replies.setAttribute("aria-label", "Confirmation choices");

  const confirm = textElement("button", "webchat-suggestion", "Confirm details");
  confirm.type = "button";
  confirm.dataset.confirmIntent = "confirm";

  const edit = textElement("button", "webchat-suggestion", "Edit details");
  edit.type = "button";
  edit.dataset.confirmEdit = "true";

  replies.append(confirm, edit);
  item.append(replies);
}

const MESSAGE_VIEW_RENDERERS = {
  grounded_presentation: renderGroundedPresentation,
  trusted_slot_context: renderTrustedSlotContext,
  choice_list: renderServiceList,
  business_information(item, view) {
    if (view) item.append(businessInformationCard(view));
  },
  vehicle_list: renderVehicleList,
  vehicle_details(item, view) {
    if (view?.vehicle) item.append(vehicleCard(view.vehicle));
  },
  vehicle_availability: renderVehicleAvailability,
  vehicle_comparison: renderVehicleComparison,
  service_list: renderServiceList,
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
    if (view?.draftId && view?.status === "awaiting_confirmation") {
      item.append(confirmationCard(view));
      renderConfirmationReplies(item);
    }
  },
  superseded_confirmation(item) {
    item.append(textElement("p", "webchat-superseded", "Superseded — details changed"));
  },
  workshop_booking_details(item, view) {
    if (view) item.append(workshopBookingDetailsCard(view));
  },
  receipt(item, view) {
    if (view) item.append(receiptCard(view));
  },
  part_exchange_estimate(item, view) {
    if (view) item.append(partExchangeEstimateCard(view));
  },
};

export function continuesRenderedTurn(previous, message) {
  return Boolean(
    message?.turnId
    && previous?.dataset?.turnId === String(message.turnId)
    && previous?.dataset?.messageRole === message.role,
  );
}

export function renderMessage(message) {
  const item = document.createElement("li");
  item.className = `webchat-message webchat-message--${message.role}`;
  if (message.viewType) item.classList.add("webchat-message--rich");
  if (message.continuesTurn === true) item.classList.add("webchat-message--same-turn");

  const choiceView = message.viewType === "trusted_slot_context"
    ? trustedSlotChoiceView(message.view)
    : message.view;
  const bulletChoiceBeforePrompt = message.role === "assistant" && isBulletChoice(choiceView);
  const displayMessage = bulletChoiceBeforePrompt ? orderNeutralChoicePrompt(message) : message;
  const content = messageContent(displayMessage, displayMessage.text);
  if (message.role === "assistant") content.classList.add("webchat-assistant-bubble");
  const cardsBeforeTrailingQuestion = (
    message.viewType === "grounded_presentation"
    && ["clarification", "follow_up", "next_step", "workflow_prompt"].includes(message.purpose)
    && Array.isArray(message.view?.cards)
    && message.view.cards.length > 0
  );
  if (bulletChoiceBeforePrompt) {
    renderBulletChoiceBeforePrompt(item, { ...displayMessage, view: choiceView }, content);
    if (message.viewType === "trusted_slot_context") {
      renderGroundedReplies(item, message.view);
    }
  } else if (cardsBeforeTrailingQuestion) {
    renderGroundedCards(item, message.view);
    item.append(content);
    renderGroundedReplies(item, message.view);
  } else {
    item.append(content);
  }

  const renderer = Object.prototype.hasOwnProperty.call(
    MESSAGE_VIEW_RENDERERS,
    message.viewType,
  )
    ? MESSAGE_VIEW_RENDERERS[message.viewType]
    : null;
  if (!cardsBeforeTrailingQuestion && !bulletChoiceBeforePrompt) renderer?.(item, message.view);
  if (
    message.view?.selectionOnly !== true
    && !message.view?.collectionPresentation
    && Array.isArray(message.view?.suggestions)
    && message.view.suggestions.length
  ) {
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
