import { textElement } from "../core/dom.js";
import { formatAppointment } from "./appointments.js";
import {
  dealershipName,
  titleCaseDisplay,
  vehicleDisplayName,
} from "./information-cards.js";
import { interestVehicleSummary } from "./workflow-forms.js";

// Confirmation cards contain only application-owned review data and explicit write controls.

function dealershipEnquiryConfirmationCard(view) {
  const summary = view.summary || {};
  const isDealershipMessage = view.kind === "dealership_message";
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-dealership-enquiry-review";

  const header = document.createElement("header");
  header.className = "webchat-enquiry-review-heading";
  const heading = textElement("strong", "", "Review before confirming");
  heading.id = `webchat-confirmation-${String(view.draftId).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  card.setAttribute("aria-labelledby", heading.id);
  header.append(
    textElement(
      "span",
      "webchat-flow-eyebrow",
      isDealershipMessage ? "Dealership message" : "Dealership enquiry",
    ),
    heading,
    textElement(
      "p",
      "",
      isDealershipMessage
        ? "Check the destination and message before sending."
        : "Check the details below before sending your enquiry.",
    ),
  );
  card.append(header);

  const details = document.createElement("dl");
  details.className = "webchat-enquiry-summary";
  const appendDetail = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const row = document.createElement("div");
    row.append(textElement("dt", "", label), textElement("dd", "", value));
    details.append(row);
  };
  appendDetail("Dealership", dealershipName(view, summary.dealershipId));
  if (summary.enquiryType) appendDetail("Enquiry type", titleCaseDisplay(summary.enquiryType));
  if (summary.department) appendDetail("Department", titleCaseDisplay(summary.department));
  if (summary.subject) appendDetail("Subject", String(summary.subject));
  if (summary.preferredContactMethod) {
    appendDetail("Preferred contact", titleCaseDisplay(summary.preferredContactMethod));
  }
  if (summary.vehicleId) appendDetail("Vehicle", vehicleDisplayName(view.vehicle));
  card.append(details);

  if (summary.message) {
    const message = document.createElement("section");
    message.className = "webchat-enquiry-message";
    message.append(
      textElement("span", "webchat-flow-eyebrow", "Message"),
      textElement("p", "", String(summary.message)),
    );
    card.append(message);
  }

  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions";
  const confirm = textElement(
    "button",
    "webchat-primary-action",
    isDealershipMessage ? "Send message" : "Confirm enquiry",
  );
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const cancel = textElement("button", "webchat-secondary-action", "Cancel");
  cancel.type = "button";
  cancel.dataset.chatAction = "cancel";
  cancel.dataset.draftId = view.draftId;
  actions.append(confirm, cancel);
  card.append(actions);

  if (view.privacyContact) {
    const privacy = document.createElement("footer");
    privacy.className = "webchat-privacy-footer";
    privacy.append(textElement(
      "small",
      "webchat-privacy-note",
      `Privacy contact · ${view.privacyContact}`,
    ));
    card.append(privacy);
  }
  return card;
}

export function confirmationCard(view) {
  if (view.kind === "workshop_amend") return workshopAmendmentConfirmationCard(view);
  if (view.kind === "workshop_cancel") return workshopCancellationConfirmationCard(view);
  if (view.kind === "vehicle_interest") return vehicleInterestConfirmationCard(view);
  if (view.kind === "callback") return callbackConfirmationCard(view);
  if (["sales_enquiry", "dealership_message"].includes(view.kind)) {
    return dealershipEnquiryConfirmationCard(view);
  }
  const card = document.createElement("section");
  card.className = "webchat-confirmation";
  card.append(textElement("strong", "", "Review before confirming"));
  const summary = document.createElement("dl");
  const hidden = new Set(["kind", "contactProvided", "slotId", "serviceTypeId"]);
  Object.entries(view.summary || {}).forEach(([key, value]) => {
    if (hidden.has(key)) return;
    const label = key === "dealershipId"
      ? "Dealership"
      : key === "bookingReference"
        ? "Booking reference"
      : key === "currentAppointment"
        ? "Current appointment"
      : key === "newAppointment"
        ? "New appointment"
      : key === "newDealership"
        ? "New location"
      : key === "vehicleId"
        ? "Vehicle"
        : key.replace(/([A-Z])/g, " $1");
    const displayValue = key === "dealershipId"
      ? "Selected dealership"
      : ["currentAppointment", "newAppointment"].includes(key)
        ? formatAppointment(value)
      : key === "vehicleId"
        ? vehicleDisplayName(view.vehicle)
      : key === "mileage" && Number.isFinite(Number(value))
        ? `${Number(value).toLocaleString("en-GB")} miles`
        : typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
    summary.append(textElement("dt", "", label));
    summary.append(textElement("dd", "", displayValue));
  });
  card.append(summary);
  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions";
  const confirm = textElement(
    "button",
    "",
    "Confirm",
  );
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const cancel = textElement(
    "button",
    "",
    "Cancel",
  );
  cancel.type = "button";
  cancel.dataset.chatAction = "cancel";
  cancel.dataset.draftId = view.draftId;
  actions.append(confirm, cancel);
  card.append(actions);
  if (view.privacyContact) {
    card.append(textElement("small", "", `Privacy contact: ${view.privacyContact}`));
  }
  return card;
}

function workshopCancellationConfirmationCard(view) {
  const summary = view.summary || {};
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workshop-cancel-review";

  const header = document.createElement("header");
  header.className = "webchat-cancel-review-heading";
  const warning = textElement("span", "webchat-cancel-review-mark", "!");
  warning.setAttribute("aria-hidden", "true");
  const headingCopy = document.createElement("div");
  headingCopy.append(
    textElement("span", "webchat-flow-eyebrow", "Workshop cancellation"),
    textElement("h3", "", "Cancel this booking?"),
    textElement(
      "p",
      "",
      "The appointment will be cancelled and its time released.",
    ),
  );
  header.append(warning, headingCopy);
  card.append(header);

  const details = document.createElement("dl");
  details.className = "webchat-cancel-review-details";
  const appendDetail = (label, value, valueClass = "") => {
    if (!value) return;
    const row = document.createElement("div");
    row.append(
      textElement("dt", "", label),
      textElement("dd", valueClass, value),
    );
    details.append(row);
  };
  appendDetail(
    "Booking reference",
    summary.bookingReference,
    "webchat-booking-reference",
  );
  appendDetail(
    "Current appointment",
    summary.currentAppointment ? formatAppointment(summary.currentAppointment) : null,
  );
  appendDetail("Service", summary.service);
  appendDetail("Dealership", summary.dealership || summary.newDealership);
  if (details.children.length) card.append(details);

  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions webchat-cancel-review-actions";
  const confirm = textElement(
    "button",
    "webchat-primary-action webchat-danger-primary-action",
    "Confirm cancellation",
  );
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const keep = textElement(
    "button",
    "webchat-secondary-action",
    "Keep current booking",
  );
  keep.type = "button";
  keep.dataset.chatAction = "cancel";
  keep.dataset.draftId = view.draftId;
  actions.append(confirm, keep);
  card.append(actions);

  if (view.privacyContact) {
    const privacy = document.createElement("footer");
    privacy.className = "webchat-privacy-footer";
    privacy.append(textElement(
      "small",
      "webchat-privacy-note",
      `Privacy contact · ${view.privacyContact}`,
    ));
    card.append(privacy);
  }
  return card;
}

function callbackConfirmationCard(view) {
  const summary = view.summary || {};
  const dealership = (view.dealerships || []).find(
    (item) => String(item.id) === String(summary.dealershipId),
  );
  const dealershipName = dealership?.name || dealership?.town || "Selected dealership";
  const department = String(summary.department || "general");
  const departmentLabel = department.replace(/^./, (letter) => letter.toUpperCase());

  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-callback-review";

  const header = document.createElement("header");
  header.className = "webchat-callback-review-heading";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Callback request"),
    textElement("strong", "", "Review before confirming"),
    textElement("p", "", "Check where the call should go and what you need help with."),
  );
  card.append(header);

  const destination = document.createElement("section");
  destination.className = "webchat-callback-destination";
  const destinationHeading = document.createElement("div");
  destinationHeading.append(
    textElement("span", "webchat-flow-eyebrow", "Dealership"),
    textElement("strong", "", dealershipName),
  );
  destination.append(
    destinationHeading,
    textElement("span", "webchat-callback-department", `${departmentLabel} team`),
  );
  if (dealership?.town && dealership.town !== dealershipName) {
    destinationHeading.append(textElement("small", "", dealership.town));
  }
  card.append(destination);

  if (summary.reason) {
    const reason = document.createElement("section");
    reason.className = "webchat-callback-reason";
    reason.append(
      textElement("span", "webchat-flow-eyebrow", "Call about"),
      textElement("p", "", String(summary.reason)),
    );
    card.append(reason);
  }
  if (summary.preferredTime) {
    card.append(textElement(
      "p",
      "webchat-callback-time",
      `Preferred time · ${summary.preferredTime}`,
    ));
  }

  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions";
  const confirm = textElement("button", "", "Confirm callback request");
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const cancel = textElement("button", "", "Cancel");
  cancel.type = "button";
  cancel.dataset.chatAction = "cancel";
  cancel.dataset.draftId = view.draftId;
  actions.append(confirm, cancel);
  card.append(actions);
  if (view.privacyContact) {
    card.append(textElement(
      "small",
      "webchat-privacy-note",
      `Privacy contact: ${view.privacyContact}`,
    ));
  }
  return card;
}

function vehicleInterestConfirmationCard(view) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-vehicle-interest-review";
  const header = document.createElement("header");
  header.className = "webchat-interest-review-heading";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Register interest"),
    textElement("strong", "", "Review before confirming"),
    textElement("p", "", "We’ll send this request for the selected vehicle."),
  );
  card.append(header);
  if (view.vehicle) card.append(interestVehicleSummary(view.vehicle));

  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions";
  const confirm = textElement("button", "", "Confirm interest request");
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const cancel = textElement("button", "", "Cancel");
  cancel.type = "button";
  cancel.dataset.chatAction = "cancel";
  cancel.dataset.draftId = view.draftId;
  actions.append(confirm, cancel);
  card.append(actions);
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy contact: ${view.privacyContact}`));
  }
  return card;
}

function workshopAmendmentConfirmationCard(view) {
  const summary = view.summary || {};
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workshop-amend-review";

  const header = document.createElement("header");
  header.className = "webchat-amend-review-heading";
  header.append(textElement("strong", "", "Review your booking changes"));
  if (summary.bookingReference) {
    header.append(textElement("span", "webchat-booking-reference", summary.bookingReference));
  }
  card.append(header);

  const comparison = document.createElement("div");
  comparison.className = "webchat-amend-comparison";
  const appointment = (label, time, location, isNew = false) => {
    const section = document.createElement("section");
    section.className = `webchat-amend-appointment${isNew ? " is-new" : ""}`;
    section.append(
      textElement("span", "webchat-flow-eyebrow", label),
      textElement("strong", "", time ? formatAppointment(time) : "Appointment unchanged"),
    );
    if (location) section.append(textElement("span", "webchat-amend-location", location));
    return section;
  };
  comparison.append(
    appointment("Current appointment", summary.currentAppointment, summary.dealership),
    appointment("New appointment", summary.newAppointment, summary.newDealership || summary.dealership, true),
  );
  card.append(comparison);
  if (summary.service) {
    card.append(textElement("p", "webchat-amend-service", `Service · ${summary.service}`));
  }

  const actions = document.createElement("div");
  actions.className = "webchat-confirmation-actions";
  const confirm = textElement("button", "", "Confirm changes");
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.expectedKind = view.kind;
  const cancel = textElement("button", "", "Keep current booking");
  cancel.type = "button";
  cancel.dataset.chatAction = "cancel";
  cancel.dataset.draftId = view.draftId;
  actions.append(confirm, cancel);
  card.append(actions);
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy contact: ${view.privacyContact}`));
  }
  return card;
}
