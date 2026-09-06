import { textElement } from "../core/dom.js?v=20260904.2";
import { moneyFromPence } from "../core/format.js?v=20260904.2";
import {
  confirmedBookingDisclosure,
  formatAppointment,
} from "./appointments.js?v=20260904.2";
import { factCard } from "./information-cards.js?v=20260904.2";

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

export function workshopBookingDetailsCard(view) {
  const card = document.createElement("article");
  card.className = "webchat-card webchat-workshop-booking-card";
  const heading = document.createElement("header");
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
  heading.append(mark, headingCopy);
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
  card.receiptView = {
    ...view,
    kind: "workshop_booking",
    serviceName: view.serviceName || view.serviceTypeName,
    dealershipName: view.dealershipName || view.dealershipTown,
  };
  return card;
}
