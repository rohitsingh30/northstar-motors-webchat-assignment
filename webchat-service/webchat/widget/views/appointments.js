import { textElement } from "../core/dom.js?v=20260904.2";

const SLOT_TIME_ZONE = "Europe/London";

export function formatAppointment(startsAt) {
  const value = new Date(startsAt);
  if (Number.isNaN(value.getTime())) return String(startsAt || "");
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: SLOT_TIME_ZONE,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(value);
}

export function confirmedBookingDisclosure(title, view, rows, extraClass = "") {
  const card = document.createElement("article");
  card.className = `webchat-inline-receipt webchat-booking-disclosure${extraClass ? ` ${extraClass}` : ""}`;
  const summary = document.createElement("header");
  summary.className = "webchat-booking-disclosure-summary";
  const copy = document.createElement("span");
  copy.className = "webchat-booking-disclosure-copy";
  copy.append(
    textElement("strong", "", title),
    textElement("span", "webchat-booking-disclosure-reference", view.reference ? `Reference ${view.reference}` : "Booking confirmed"),
  );
  const mark = textElement("span", "webchat-receipt-mark", "✓");
  mark.setAttribute("aria-hidden", "true");
  summary.append(mark, copy);
  card.append(summary);
  const details = document.createElement("dl");
  details.className = "webchat-booking-summary";
  rows.filter(([label, value]) => label !== "Reference" && value !== undefined && value !== null && value !== "")
    .forEach(([label, value, valueClass = ""]) => {
      const row = document.createElement("div");
      row.append(textElement("dt", "", label), textElement("dd", valueClass, value));
      details.append(row);
    });
  if (details.children.length) card.append(details);
  return card;
}
