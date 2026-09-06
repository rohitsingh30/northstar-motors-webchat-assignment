import { textElement } from "../core/dom.js?v=20260904.2";
import { moneyFromPence } from "../core/format.js?v=20260904.2";
import { formatAppointment } from "./appointments.js?v=20260904.2";
import {
  dealershipName,
  titleCaseDisplay,
  vehicleDisplayName,
} from "./information-cards.js?v=20260904.2";

function interestVehicleSummary(vehicle) {
  const selected = document.createElement("section");
  selected.className = "webchat-interest-vehicle";
  const heading = document.createElement("div");
  heading.className = "webchat-interest-vehicle-heading";
  heading.append(
    textElement("span", "webchat-flow-eyebrow", "Selected vehicle"),
    textElement("strong", "", `${vehicle.make || ""} ${vehicle.model || ""}`.trim() || "Selected vehicle"),
  );
  selected.append(heading);
  const identity = [vehicle.year, vehicle.variant, vehicle.dealershipTown].filter(Boolean).join(" · ");
  if (identity) selected.append(textElement("p", "webchat-interest-vehicle-meta", identity));
  const facts = document.createElement("dl");
  [["Mileage", Number.isFinite(Number(vehicle.mileage)) ? `${Number(vehicle.mileage).toLocaleString("en-GB")} miles` : null], ["Fuel", vehicle.fuelType], ["Gearbox", vehicle.transmission], ["Price", Number.isInteger(vehicle.pricePence) ? moneyFromPence(vehicle.pricePence) : null]]
    .filter(([, value]) => value)
    .forEach(([label, value]) => facts.append(textElement("dt", "", label), textElement("dd", "", value)));
  if (facts.childElementCount) selected.append(facts);
  return selected;
}

// Confirmation cards are application-owned, visual-only review summaries. The customer confirms,
// cancels, corrects, or changes topic through the ordinary conversation.

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
  let card;
  if (view.kind === "test_drive") card = testDriveConfirmationCard(view);
  else if (view.kind === "workshop_booking") card = workshopBookingConfirmationCard(view);
  else if (view.kind === "workshop_amend") card = workshopAmendmentConfirmationCard(view);
  else if (view.kind === "workshop_cancel") card = workshopCancellationConfirmationCard(view);
  else if (view.kind === "vehicle_interest") card = vehicleInterestConfirmationCard(view);
  else if (view.kind === "callback") card = callbackConfirmationCard(view);
  else if (view.kind === "part_exchange") card = partExchangeConfirmationCard(view);
  if (["sales_enquiry", "dealership_message"].includes(view.kind)) {
    card = dealershipEnquiryConfirmationCard(view);
  }
  if (card) {
    appendLocalReviewDetails(card, view.localDetails, view.localReviewDetails);
    return card;
  }
  card = document.createElement("section");
  card.className = "webchat-confirmation";
  card.append(textElement("strong", "", "Review before confirming"));
  const summary = document.createElement("dl");
  const hidden = new Set(["kind", "contactProvided", "slotId", "serviceTypeId"]);
  Object.entries(view.summary || {}).forEach(([key, value]) => {
    if (hidden.has(key) || /Id$/.test(key)) return;
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
      : key === "selectedStartsAt"
        ? "Appointment"
      : key === "selectedDealershipName"
        ? "Location"
      : key === "selectedServiceName"
        ? "Service"
      : key.replace(/([A-Z])/g, " $1");
    const displayValue = key === "dealershipId"
      ? "Selected dealership"
      : ["currentAppointment", "newAppointment"].includes(key)
        ? formatAppointment(value)
      : key === "selectedStartsAt"
        ? formatAppointment(value)
      : key === "mileage" && Number.isFinite(Number(value))
        ? `${Number(value).toLocaleString("en-GB")} miles`
        : typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
    summary.append(textElement("dt", "", label));
    summary.append(textElement("dd", "", displayValue));
  });
  card.append(summary);
  if (view.privacyContact) {
    card.append(textElement("small", "", `Privacy contact: ${view.privacyContact}`));
  }
  appendLocalReviewDetails(card, view.localDetails, view.localReviewDetails);
  return card;
}

function testDriveConfirmationCard(view) {
  const summary = view.summary || {};
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workshop-booking-review";

  const header = document.createElement("header");
  header.className = "webchat-workshop-review-heading";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Test drive"),
    textElement("strong", "", "Review your test drive"),
    textElement("p", "", "Check the appointment and your details before confirming."),
  );
  card.append(header);

  const details = document.createElement("dl");
  details.className = "webchat-workshop-review-details";
  const rows = [
    ["Appointment", summary.selectedStartsAt ? formatAppointment(summary.selectedStartsAt) : null],
    ["Location", summary.selectedDealershipName],
    ["Vehicle", vehicleDisplayName(view.vehicle)],
  ];
  rows.filter(([, value]) => value).forEach(([label, value]) => {
    const row = document.createElement("div");
    row.append(textElement("dt", "", label), textElement("dd", "", value));
    details.append(row);
  });
  if (details.childElementCount) card.append(details);
  return card;
}

function partExchangeConfirmationCard(view) {
  const summary = view.summary || {};
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workshop-booking-review";

  const header = document.createElement("header");
  header.className = "webchat-workshop-review-heading";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Part exchange"),
    textElement("strong", "", "Review your part-exchange request"),
    textElement("p", "", "Check the destination and your vehicle details before confirming."),
  );
  card.append(header);

  const details = document.createElement("dl");
  details.className = "webchat-workshop-review-details";
  const row = document.createElement("div");
  row.append(
    textElement("dt", "", "Location"),
    textElement("dd", "", dealershipName(view, summary.dealershipId)),
  );
  details.append(row);
  card.append(details);
  return card;
}

function appendLocalReviewDetails(card, values = {}, declaredDetails = []) {
  if (!card || !values || typeof values !== "object") return;
  const fullName = values.fullName || [values.firstName, values.lastName].filter(Boolean).join(" ");
  const compatibilityDetails = {
    "Full name": fullName,
    Email: values.email,
    Phone: values.phone,
    Registration: values.registration,
    Mileage: Number.isFinite(Number(values.mileage)) ? `${Number(values.mileage).toLocaleString("en-GB")} miles` : null,
    "Notes": values.notes,
    "Message": values.message,
    "Reason": values.reason,
    "Preferred time": values.preferredTime,
    "Preferred contact": values.preferredContactMethod,
    "Requested changes": values.requestedChanges,
  };
  const details = Array.isArray(declaredDetails) && declaredDetails.length
    ? declaredDetails.map((item) => [item.label, item.value])
    : Object.entries(compatibilityDetails);
  const rows = document.createElement("dl");
  rows.className = "webchat-local-review-details";
  details.filter(([, value]) => value !== undefined && value !== null && value !== "")
    .forEach(([label, value]) => {
      const row = document.createElement("div");
      row.append(textElement("dt", "", label), textElement("dd", "", String(value)));
      rows.append(row);
    });
  if (!rows.childElementCount) return;
  const heading = document.createElement("div");
  heading.className = "webchat-local-review-heading";
  heading.append(
    textElement("strong", "", "Your details"),
    textElement("small", "", "Only visible in this tab"),
  );
  card.append(heading, rows);
}

function workshopBookingConfirmationCard(view) {
  const summary = view.summary || {};
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workshop-booking-review";

  const header = document.createElement("header");
  header.className = "webchat-workshop-review-heading";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Workshop booking"),
    textElement("strong", "", "Review your appointment"),
    textElement("p", "", "Check the appointment and your details before confirming."),
  );
  card.append(header);

  const details = document.createElement("dl");
  details.className = "webchat-workshop-review-details";
  const appendDetail = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const row = document.createElement("div");
    row.append(textElement("dt", "", label), textElement("dd", "", value));
    details.append(row);
  };
  appendDetail(
    "Appointment",
    summary.selectedStartsAt ? formatAppointment(summary.selectedStartsAt) : null,
  );
  appendDetail(
    "Location",
    summary.selectedDealershipName || dealershipName(view, summary.dealershipId),
  );
  appendDetail(
    "Service",
    summary.selectedServiceName
      || (summary.serviceTypeId ? titleCaseDisplay(summary.serviceTypeId) : null),
  );
  if (details.childElementCount) card.append(details);

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
    appointment(
      "New appointment",
      summary.newAppointment || summary.selectedStartsAt,
      summary.newDealership || summary.selectedDealershipName || summary.dealership,
      true,
    ),
  );
  card.append(comparison);
  if (summary.selectedServiceName || summary.service) {
    card.append(textElement(
      "p",
      "webchat-amend-service",
      `Service · ${summary.selectedServiceName || summary.service}`,
    ));
  }

  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy contact: ${view.privacyContact}`));
  }
  return card;
}
