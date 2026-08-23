import { textElement } from "../core/dom.js";
import { moneyFromPence } from "../core/format.js";
import {
  confirmedBookingDisclosure,
  formatAppointment,
  workshopBookingActions,
} from "./appointments.js";
import { factCard } from "./information-cards.js";

// Receipts and private lookup views restore only public, application-approved workflow data.

export function receiptCard(view) {
  const titles = {
    sales_enquiry: "Sales enquiry submitted",
    test_drive: "Test drive booked",
    vehicle_interest: "Interest registered",
    callback: "Callback requested",
    workshop_booking: "Workshop booked",
    workshop_amend: "Workshop booking updated",
    workshop_cancel: "Workshop booking cancelled",
    workshop_change_abandoned: "Current booking kept",
    request_cancelled: "Request cancelled",
    dealership_message: "Message submitted",
    part_exchange: "Part-exchange request submitted",
  };
  const requestCancellationTitles = {
    sales_enquiry: "Sales enquiry cancelled",
  };
  const title = view.kind === "request_cancelled"
    ? requestCancellationTitles[view.requestKind] || titles.request_cancelled
    : titles[view.kind];
  const confirmedBookingTitles = {
    test_drive: "Test drive booked",
    workshop_booking: "Workshop booked",
    workshop_amend: "Workshop booking updated",
  };
  if (confirmedBookingTitles[view.kind]) {
    const card = confirmedBookingDisclosure(confirmedBookingTitles[view.kind], view, [
      ["Appointment", view.slotLabel || (view.startsAt ? formatAppointment(view.startsAt) : "")],
      ["Location", view.dealershipName],
      ["Vehicle", view.vehicleLabel || view.registration],
      ["Service", view.serviceName || view.serviceTypeName],
    ], "webchat-receipt-card");
    if (["workshop_booking", "workshop_amend"].includes(view.kind)) {
      card.append(workshopBookingActions(view.reference));
    }
    card.receiptView = { ...view };
    return card;
  }
  if (title) {
    const card = document.createElement("article");
    card.className = "webchat-inline-receipt webchat-receipt-card";
    card.append(
      textElement("span", "webchat-receipt-mark", "✓"),
      textElement("strong", "", title),
      textElement(
        "p",
        "",
        view.reference
          ? `Reference ${view.reference}`
          : view.kind === "workshop_change_abandoned"
            ? "No changes were made to your booking."
          : `Status: ${view.status || "completed"}`,
      ),
    );
    if (Number.isInteger(view.estimateLowPence) && Number.isInteger(view.estimateHighPence)) {
      card.append(textElement(
        "strong",
        "webchat-card-price",
        `${moneyFromPence(view.estimateLowPence)}–${moneyFromPence(view.estimateHighPence)}`,
      ));
    }
    return card;
  }
  const card = factCard(view);
  if (Number.isInteger(view.estimateLowPence) && Number.isInteger(view.estimateHighPence)) {
    const currency = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });
    card.append(
      textElement(
        "span",
        "webchat-card-price",
        `${currency.format(view.estimateLowPence / 100)}–${currency.format(view.estimateHighPence / 100)}`,
      ),
    );
  }
  if (view.estimateNotice) card.append(textElement("small", "", view.estimateNotice));
  return card;
}

export function partExchangeEstimateCard(view) {
  const card = document.createElement("article");
  card.className = "webchat-part-exchange-estimate";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Indicative estimate"),
    textElement(
      "strong",
      "webchat-estimate-range",
      Number.isInteger(view.estimateLowPence) && Number.isInteger(view.estimateHighPence)
        ? `${moneyFromPence(view.estimateLowPence)}–${moneyFromPence(view.estimateHighPence)}`
        : "Estimate unavailable",
    ),
  );
  const details = [
    view.registration,
    Number.isInteger(view.mileage) ? `${view.mileage.toLocaleString("en-GB")} miles` : null,
    view.condition ? String(view.condition).replace(/^./, (letter) => letter.toUpperCase()) : null,
  ].filter(Boolean);
  if (details.length) card.append(textElement("p", "webchat-estimate-details", details.join(" · ")));
  if (view.estimateNotice) card.append(textElement("small", "webchat-privacy-note", view.estimateNotice));
  return card;
}

export function businessInformationCard(view) {
  const card = document.createElement("article");
  card.className = "webchat-card";
  card.append(textElement("strong", "", view.organisation || "Northstar Motors"));
  if (Array.isArray(view.facts)) {
    view.facts.forEach((fact) => {
      if (!fact?.value) return;
      card.append(
        textElement("strong", "", fact.label || "Information"),
        textElement("p", "", fact.value),
      );
    });
    return card;
  }
  // Version 1 conversations stored the complete platform payload. Keep those
  // messages renderable while all new responses use the scoped fact list above.
  if (view.finance?.notice) card.append(textElement("p", "", view.finance.notice));
  if (view.partExchange?.estimateNotice) {
    card.append(textElement("p", "", view.partExchange.estimateNotice));
  }
  if (view.privacyContact) {
    card.append(textElement("span", "", `Privacy: ${view.privacyContact}`));
  }
  return card;
}

export function privateLookupForm(view = {}) {
  const mode = ["amend", "cancel"].includes(view.mode) ? view.mode : "lookup";
  const form = document.createElement("form");
  form.className = "webchat-private-lookup";
  form.dataset.privateLookup = "true";
  const title = mode === "amend"
    ? "Verify the booking you want to change"
    : mode === "cancel"
      ? "Verify the booking you want to cancel"
      : "Find an existing workshop booking";
  const description = mode === "amend"
    ? "After verification, you can update the appointment time, mileage, or notes."
    : mode === "cancel"
      ? "After verification, you can review and confirm the cancellation."
      : "After verification, the appointment details and current status will be shown.";
  form.append(
    textElement("strong", "", title),
    textElement("p", "", description),
    textElement("small", "webchat-privacy-note", "These details go directly to the secure lookup and are not added to the chat transcript."),
  );
  const modeInput = document.createElement("input");
  modeInput.type = "hidden";
  modeInput.name = "mode";
  modeInput.value = mode;
  form.append(modeInput);
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-private-lookup-fields";
  [
    ["reference", "Booking reference", "e.g. WORK-10001"],
    ["lastName", "Surname", "The surname used when booking"],
    ["registration", "Vehicle registration", "e.g. AB19 XYZ"],
    ["phone", "Phone number", "The number used when booking"],
  ].forEach(([name, labelText, placeholder]) => {
    const label = textElement("label", "", labelText);
    const input = document.createElement("input");
    input.name = name;
    input.required = true;
    input.autocomplete = name === "phone" ? "tel" : "off";
    input.placeholder = placeholder;
    const error = textElement("small", "webchat-field-error", "");
    error.dataset.fieldError = name;
    error.hidden = true;
    label.append(input, error);
    fields.append(label);
  });
  form.append(fields);
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  const submit = textElement("button", "", "Look up booking");
  submit.type = "submit";
  form.append(formError, submit);
  return form;
}

export function workshopBookingDetailsCard(view) {
  const card = document.createElement("details");
  card.className = "webchat-card webchat-workshop-booking-card";
  const heading = document.createElement("summary");
  heading.className = "webchat-booking-confirmed-heading";
  const headingCopy = document.createElement("div");
  headingCopy.append(
    textElement("p", "webchat-flow-eyebrow", `Booking ${view.status || "verified"}`),
    textElement("h3", "", view.serviceTypeName || "Workshop appointment"),
  );
  if (view.reference) {
    headingCopy.append(textElement(
      "span",
      "webchat-booking-disclosure-reference",
      `Reference ${view.reference}`,
    ));
  }
  const mark = textElement("span", "webchat-receipt-mark", "✓");
  mark.setAttribute("aria-hidden", "true");
  const toggle = document.createElement("span");
  toggle.className = "webchat-booking-disclosure-toggle";
  toggle.append(
    textElement("span", "webchat-booking-disclosure-toggle-label", "Details"),
    textElement("span", "webchat-booking-disclosure-chevron", ""),
  );
  toggle.setAttribute("aria-hidden", "true");
  heading.append(mark, headingCopy, toggle);
  card.append(heading);

  const details = document.createElement("dl");
  details.className = "webchat-booking-confirmed-details";
  const appendDetail = (label, value, valueClass = "") => {
    if (!value) return;
    const row = document.createElement("div");
    const detailValue = textElement("dd", valueClass, value);
    row.append(textElement("dt", "", label), detailValue);
    details.append(row);
  };
  appendDetail("Appointment", view.startsAt ? formatAppointment(view.startsAt) : null);
  appendDetail("Location", view.dealershipName || view.dealershipTown);
  if (details.children.length) card.append(details);
  if (view.status === "confirmed") {
    card.append(workshopBookingActions(view.reference));
  }
  card.receiptView = {
    ...view,
    kind: "workshop_booking",
    serviceName: view.serviceName || view.serviceTypeName,
    dealershipName: view.dealershipName || view.dealershipTown,
  };
  return card;
}
