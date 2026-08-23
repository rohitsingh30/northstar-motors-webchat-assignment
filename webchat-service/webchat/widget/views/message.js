import { textElement } from "../core/dom.js";
import { moneyFromPence } from "../core/format.js";
import { messageContent } from "./message-content.js";
import { suggestionChips } from "./suggestions.js";
import {
  comparisonTable,
  vehicleAvailabilityCard,
  vehicleCard,
} from "./vehicle.js";

// Model text is rendered only through safe DOM properties and closed view types.

const SLOT_TIME_ZONE = "Europe/London";

function slotDateKey(startsAt) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: SLOT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(startsAt));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function formatAppointment(startsAt) {
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

function workshopLocationLabel(slot) {
  if (slot.dealershipTown) return slot.dealershipTown;
  return String(slot.dealershipName || "Workshop").replace(/^Northstar\s+/i, "");
}

function appointmentSlotPicker(view, { inline = false, kind = "test-drive" } = {}) {
  const workshop = kind === "workshop";
  const slotPattern = workshop ? /^ws-slot-[0-9]{4}$/ : /^td-slot-[0-9]{4}$/;
  const slots = (view.items || []).filter(
    (slot) => slotPattern.test(slot.id || "") && !Number.isNaN(Date.parse(slot.startsAt)),
  );
  const picker = document.createElement("section");
  picker.className = `webchat-slot-picker${inline ? " is-inline" : ""}${workshop ? " is-workshop" : ""}`;
  if (!slots.length) {
    picker.append(
      textElement(
        "p",
        "",
        workshop
          ? "No matching workshop times are currently available."
          : "No matching test-drive times are currently available.",
      ),
    );
    return picker;
  }

  const first = slots[0];
  if (!inline) {
    picker.append(
      textElement(
        "strong",
        "webchat-slot-title",
        workshop
          ? first.serviceName || "Workshop appointment"
          : `${first.make || "Vehicle"} ${first.model || ""}`.trim(),
      ),
      textElement(
        "span",
        "webchat-slot-location",
        workshop ? "Choose a location, date and time" : first.dealershipName,
      ),
    );
  }

  const locations = new Map();
  slots.forEach((slot) => {
    const key = slot.dealershipId || slot.dealershipName || "workshop";
    if (!locations.has(key)) locations.set(key, workshopLocationLabel(slot));
  });
  let selectedLocation = workshop ? locations.keys().next().value : null;
  const locationSummary = workshop
    ? textElement("p", "webchat-workshop-location", `At ${locations.get(selectedLocation)}`)
    : null;
  function groupedSlots() {
    const groups = new Map();
    slots
      .filter((slot) => !workshop || (slot.dealershipId || slot.dealershipName || "workshop") === selectedLocation)
      .forEach((slot) => {
        const key = slotDateKey(slot.startsAt);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(slot);
      });
    return groups;
  }
  let groups = groupedSlots();
  let dates = [...groups.keys()].sort();
  const controls = document.createElement("div");
  controls.className = "webchat-date-controls";
  const previous = textElement("button", "webchat-date-arrow", "‹");
  previous.type = "button";
  previous.setAttribute("aria-label", "Show earlier dates");
  const dateStrip = document.createElement("div");
  dateStrip.className = "webchat-date-strip";
  const next = textElement("button", "webchat-date-arrow", "›");
  next.type = "button";
  next.setAttribute("aria-label", "Show later dates");
  controls.append(previous, dateStrip, next);

  const heading = textElement("p", "webchat-slot-day-heading", "");
  const times = document.createElement("div");
  times.className = "webchat-time-grid";
  const datesPerPage = 3;
  let pageStart = 0;
  let selectedDate = dates[0];

  function selectDate(key, selectedButton) {
    selectedDate = key;
    dateStrip.querySelectorAll("button").forEach((button) => {
      const selected = button === selectedButton;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    const day = new Date(groups.get(key)[0].startsAt);
    heading.textContent = `Available ${new Intl.DateTimeFormat("en-GB", {
      timeZone: SLOT_TIME_ZONE,
      weekday: "long",
      day: "numeric",
      month: "long",
    }).format(day)}`;
    times.replaceChildren();
    groups.get(key).forEach((slot) => {
      const label = new Intl.DateTimeFormat("en-GB", {
        timeZone: SLOT_TIME_ZONE,
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      }).format(new Date(slot.startsAt));
      const button = textElement("button", "webchat-time-button", label);
      button.type = "button";
      button.dataset.chatAction = workshop
        ? (view.mode === "amendment" ? "select-workshop-amendment-slot" : "select-workshop-slot")
        : "select-test-drive-slot";
      button.dataset.slotId = slot.id;
      button.dataset.slotLabel = `${heading.textContent.replace("Available ", "")} at ${label}`;
      button.dataset.vehicleId = slot.vehicleId || view.vehicleId || "";
      button.dataset.dealershipId = slot.dealershipId || "";
      button.dataset.serviceTypeId = slot.serviceTypeId || "";
      button.dataset.dealershipName = slot.dealershipName || "";
      button.dataset.serviceName = slot.serviceName || "";
      times.append(button);
    });
  }

  function renderDatePage() {
    dateStrip.replaceChildren();
    const pageDates = dates.slice(pageStart, pageStart + datesPerPage);
    if (!pageDates.includes(selectedDate)) selectedDate = pageDates[0];
    pageDates.forEach((key) => {
      const date = new Date(groups.get(key)[0].startsAt);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "webchat-date-button";
      button.setAttribute("aria-pressed", "false");
      button.append(
        textElement("span", "", new Intl.DateTimeFormat("en-GB", { timeZone: SLOT_TIME_ZONE, weekday: "short" }).format(date)),
        textElement("strong", "", new Intl.DateTimeFormat("en-GB", { timeZone: SLOT_TIME_ZONE, day: "numeric" }).format(date)),
        textElement("span", "", new Intl.DateTimeFormat("en-GB", { timeZone: SLOT_TIME_ZONE, month: "short" }).format(date)),
      );
      button.addEventListener("click", () => selectDate(key, button));
      dateStrip.append(button);
      if (key === selectedDate) selectDate(key, button);
    });
    previous.disabled = pageStart === 0;
    next.disabled = pageStart + datesPerPage >= dates.length;
  }

  previous.addEventListener("click", () => {
    pageStart = Math.max(0, pageStart - datesPerPage);
    selectedDate = dates[pageStart];
    renderDatePage();
  });
  next.addEventListener("click", () => {
    pageStart = Math.min(dates.length - 1, pageStart + datesPerPage);
    selectedDate = dates[pageStart];
    renderDatePage();
  });
  renderDatePage();

  if (workshop && locations.size > 1) {
    const locationPicker = document.createElement("div");
    locationPicker.className = "webchat-location-picker";
    locationPicker.setAttribute("aria-label", "Workshop location");
    locations.forEach((label, key) => {
      const button = textElement("button", "webchat-location-button", label.replace(/^Northstar\s+/i, ""));
      button.type = "button";
      button.setAttribute("aria-pressed", String(key === selectedLocation));
      button.classList.toggle("is-selected", key === selectedLocation);
      button.addEventListener("click", () => {
        selectedLocation = key;
        locationSummary.textContent = `At ${label}`;
        groups = groupedSlots();
        dates = [...groups.keys()].sort();
        pageStart = 0;
        selectedDate = dates[0];
        locationPicker.querySelectorAll("button").forEach((choice) => {
          const selected = choice === button;
          choice.classList.toggle("is-selected", selected);
          choice.setAttribute("aria-pressed", String(selected));
        });
        renderDatePage();
      });
      locationPicker.append(button);
    });
    picker.append(locationSummary, locationPicker);
  } else if (locationSummary) {
    picker.append(locationSummary);
  }
  picker.append(controls, heading, times);
  if (inline) picker.append(flowCloseButton(kind));
  return picker;
}

export function testDriveSlotPicker(view, { inline = false } = {}) {
  return appointmentSlotPicker(view, { inline, kind: "test-drive" });
}

export function workshopSlotPicker(view, { inline = false } = {}) {
  return appointmentSlotPicker(view, { inline, kind: "workshop" });
}

function flowCloseButton(kind, draftId = "") {
  const cancel = textElement("button", "webchat-flow-close", "×");
  cancel.type = "button";
  cancel.classList.add("is-cancel");
  cancel.setAttribute("aria-label", `Close ${kind === "workshop" ? "workshop" : "test-drive"} booking`);
  cancel.dataset.chatAction = kind === "workshop" ? "cancel-inline-workshop" : "cancel-inline-test-drive";
  if (draftId) cancel.dataset.draftId = draftId;
  return cancel;
}

function formField(name, labelText, type, autocomplete, constraints = {}) {
  const label = textElement("label", "", labelText);
  label.dataset.fieldName = name;
  const input = document.createElement("input");
  input.name = name;
  input.type = type;
  input.autocomplete = autocomplete;
  input.required = true;
  Object.entries(constraints).forEach(([property, value]) => { input[property] = value; });
  const error = textElement("small", "webchat-field-error", "");
  error.dataset.fieldError = name;
  error.hidden = true;
  label.append(input, error);
  return label;
}

function textAreaField(name, labelText, constraints = {}) {
  const label = textElement("label", "", labelText);
  const input = document.createElement("textarea");
  input.name = name;
  input.required = constraints.required !== false;
  Object.entries(constraints).forEach(([property, value]) => {
    if (property !== "required") input[property] = value;
  });
  const error = textElement("small", "webchat-field-error", "");
  error.dataset.fieldError = name;
  error.hidden = true;
  label.append(input, error);
  return label;
}

function contactFormFields() {
  return [
    formField("firstName", "First name", "text", "given-name", { minLength: 2, maxLength: 100 }),
    formField("lastName", "Last name", "text", "family-name", { minLength: 2, maxLength: 100 }),
    formField("email", "Email", "email", "email"),
    formField("phone", "Phone", "tel", "tel", {
      inputMode: "tel",
      maxLength: 30,
      placeholder: "e.g. 07123 456789",
    }),
  ];
}

function applyServerFormValues(container, values) {
  Object.entries(values || {}).forEach(([name, value]) => {
    const field = container.querySelector(`[name="${name}"]`);
    if (!field || value === undefined || value === null) return;
    field.value = value;
    field.dataset.profileValueSource = "server";
  });
}

export function testDriveDetailsForm({ slotId, slotLabel, dealershipName }) {
  const form = document.createElement("form");
  form.className = "webchat-test-drive-details";
  form.dataset.testDriveDetails = "true";
  form.dataset.slotId = slotId;
  form.dataset.slotLabel = slotLabel;
  form.append(
    textElement("p", "webchat-flow-eyebrow", "Your selected time"),
    textElement("strong", "webchat-selected-slot", slotLabel),
    textElement("span", "webchat-slot-location", dealershipName),
  );
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid";
  fields.append(
    formField("firstName", "First name", "text", "given-name", { minLength: 2, maxLength: 100 }),
    formField("lastName", "Last name", "text", "family-name", { minLength: 2, maxLength: 100 }),
    formField("email", "Email", "email", "email"),
    formField("phone", "Phone", "tel", "tel", {
      inputMode: "tel",
      maxLength: 30,
      placeholder: "e.g. 07123 456789",
    }),
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const back = textElement("button", "webchat-secondary-action", "Back");
  back.type = "button";
  back.dataset.chatAction = "back-to-test-drive-slots";
  const review = textElement("button", "webchat-primary-action", "Review booking");
  review.type = "submit";
  actions.append(back, review);
  form.append(
    fields,
    textElement(
      "small",
      "webchat-privacy-note",
      "Your details are used only to arrange this test drive.",
    ),
    actions,
    flowCloseButton("test-drive"),
  );
  return form;
}

export function inlineTestDriveConfirmation(view, slotLabel) {
  const card = document.createElement("section");
  card.className = "webchat-inline-confirmation";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Ready to book"),
    textElement("strong", "webchat-selected-slot", slotLabel),
    textElement("p", "", "Your contact details have been added securely."),
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const edit = textElement("button", "webchat-secondary-action", "Edit details");
  edit.type = "button";
  edit.dataset.chatAction = "edit-test-drive-details";
  const confirm = textElement("button", "webchat-primary-action", "Confirm booking");
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.inlineBooking = "true";
  actions.append(edit, confirm);
  card.append(actions, flowCloseButton("test-drive", view.draftId));
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy: ${view.privacyContact}`));
  }
  return card;
}

export function inlineBookingReceipt(view) {
  const card = document.createElement("section");
  card.className = "webchat-inline-receipt";
  card.append(
    textElement("span", "webchat-receipt-mark", "✓"),
    textElement("strong", "", "Test drive booked"),
    textElement("p", "", view.reference ? `Reference ${view.reference}` : "Your booking is confirmed."),
  );
  return card;
}

export function workshopDetailsForm({ slotId, slotLabel, dealershipName, serviceName }) {
  const form = document.createElement("form");
  form.className = "webchat-test-drive-details webchat-workshop-details";
  form.dataset.workshopDetails = "true";
  form.dataset.slotId = slotId;
  form.append(
    textElement("p", "webchat-flow-eyebrow", serviceName || "Workshop appointment"),
    textElement("strong", "webchat-selected-slot", slotLabel),
    textElement("span", "webchat-slot-location", dealershipName),
  );
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-workshop-fields";
  fields.append(
    formField("firstName", "First name", "text", "given-name", { minLength: 2, maxLength: 100 }),
    formField("lastName", "Last name", "text", "family-name", { minLength: 2, maxLength: 100 }),
    formField("email", "Email", "email", "email"),
    formField("phone", "Phone", "tel", "tel", { inputMode: "tel", maxLength: 30 }),
    formField("registration", "Registration", "text", "off", { minLength: 2, maxLength: 20 }),
    formField("mileage", "Current mileage", "number", "off", { min: 0, max: 2000000 }),
    formField("notes", "Notes (optional)", "text", "off", { required: false, maxLength: 1000 }),
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const back = textElement("button", "webchat-secondary-action", "Back");
  back.type = "button";
  back.dataset.chatAction = "back-to-workshop-slots";
  const review = textElement("button", "webchat-primary-action", "Review booking");
  review.type = "submit";
  actions.append(back, review);
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "Your details are used only to arrange this workshop appointment."),
    actions,
    flowCloseButton("workshop"),
  );
  return form;
}

export function inlineWorkshopConfirmation(view, selection, details) {
  const card = document.createElement("section");
  card.className = "webchat-inline-confirmation";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Ready to book"),
    textElement("strong", "webchat-selected-slot", selection.serviceName || "Workshop appointment"),
    textElement("p", "", `${selection.slotLabel} · ${selection.dealershipName}`),
    textElement("p", "webchat-workshop-vehicle", `${details.registration.toUpperCase()} · ${Number(details.mileage).toLocaleString("en-GB")} miles`),
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const edit = textElement("button", "webchat-secondary-action", "Edit details");
  edit.type = "button";
  edit.dataset.chatAction = "edit-workshop-details";
  const confirm = textElement("button", "webchat-primary-action", "Confirm booking");
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  confirm.dataset.inlineBooking = "workshop";
  actions.append(edit, confirm);
  card.append(actions, flowCloseButton("workshop", view.draftId));
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy: ${view.privacyContact}`));
  }
  return card;
}

export function inlineWorkshopReceipt(view) {
  const card = document.createElement("section");
  card.className = "webchat-inline-receipt";
  card.append(
    textElement("span", "webchat-receipt-mark", "✓"),
    textElement("strong", "", "Workshop booked"),
    textElement("p", "", view.reference ? `Reference ${view.reference}` : "Your appointment is confirmed."),
    flowCloseButton("workshop"),
  );
  return card;
}

export function inlineBookedTestDrive(booking) {
  const card = document.createElement("section");
  card.className = "webchat-booked-test-drive";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Test drive booked"),
    textElement("strong", "", booking.slotLabel || "Your selected appointment"),
  );
  const details = document.createElement("p");
  details.className = "webchat-booked-test-drive-details";
  const reference = booking.reference ? `Reference ${booking.reference}` : "Booking confirmed";
  details.textContent = booking.dealershipName
    ? `${reference} · ${booking.dealershipName}`
    : reference;
  const close = textElement("button", "webchat-flow-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Close booked test drive");
  close.dataset.chatAction = "hide-booked-test-drive";
  card.append(details, close);
  return card;
}

const CARD_FIELDS = [
  ["name", "Name"],
  ["make", "Make"],
  ["model", "Model"],
  ["productType", "Product"],
  ["monthlyPricePence", "Monthly price (pence)"],
  ["dealershipName", "Dealership"],
  ["departments", "Departments"],
  ["town", "Town"],
  ["addressLine", "Address"],
  ["postcode", "Postcode"],
  ["phone", "Phone"],
  ["email", "Email"],
  ["description", "Details"],
  ["serviceName", "Service"],
  ["startsAt", "Time"],
  ["endsAt", "Ends"],
  ["status", "Status"],
];

function serviceCard(item) {
  const card = document.createElement("article");
  card.className = "webchat-service-card";
  const title = document.createElement("div");
  title.className = "webchat-service-heading";
  title.append(
    textElement("h3", "", item.name),
    textElement(
      "span",
      "webchat-service-duration",
      Number.isInteger(item.durationMinutes) ? `${item.durationMinutes} min` : "Workshop service",
    ),
  );
  card.append(title, textElement("p", "webchat-service-description", item.description));
  const footer = document.createElement("div");
  footer.className = "webchat-service-footer";
  footer.append(
    textElement(
      "strong",
      "webchat-service-price",
      Number.isInteger(item.priceFromPence) ? `From ${moneyFromPence(item.priceFromPence)}` : "Price on request",
    ),
  );
  if (item.id) {
    const action = textElement("button", "webchat-primary-action", "Find times");
    action.type = "button";
    action.dataset.chatAction = "workshop-service";
    action.dataset.serviceTypeId = item.id;
    action.dataset.serviceName = item.name || "Workshop service";
    footer.append(action);
  }
  const flow = document.createElement("div");
  flow.className = "webchat-booking-flow";
  flow.dataset.workshopFlow = "true";
  flow.hidden = true;
  card.append(footer, flow);
  return card;
}

function factCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-card";
  CARD_FIELDS.forEach(([key, label]) => {
    if (value?.[key] === undefined || value[key] === null) return;
    card.append(textElement("strong", "", label));
    card.append(textElement("span", "", String(value[key])));
  });
  return card;
}

function offerCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-card webchat-offer-card";
  const heading = document.createElement("div");
  heading.className = "webchat-offer-heading";
  heading.append(textElement("strong", "", value.name || `${value.make || ""} ${value.model || ""}`.trim() || "New-car offer"));
  if (value.productType) heading.append(textElement("span", "webchat-offer-type", value.productType));
  card.append(heading);
  if (Number.isInteger(value.monthlyPricePence)) {
    card.append(textElement("strong", "webchat-card-price", `${moneyFromPence(value.monthlyPricePence)} / month`));
  }
  const terms = [
    ["Deposit", value.depositPence],
    ["APR", value.aprPercent != null ? `${value.aprPercent}%` : null],
    ["Term", value.termMonths ? `${value.termMonths} months` : null],
    ["Mileage", value.annualMileage ? `${Number(value.annualMileage).toLocaleString("en-GB")} miles/year` : null],
    ["Expires", value.expiresOn],
  ];
  const details = document.createElement("dl");
  details.className = "webchat-offer-terms";
  terms.filter(([, detail]) => detail !== undefined && detail !== null).forEach(([label, detail]) => {
    details.append(
      textElement("dt", "", label),
      textElement("dd", "", Number.isInteger(detail) && label === "Deposit" ? moneyFromPence(detail) : detail),
    );
  });
  if (details.childElementCount) card.append(details);
  return card;
}

function dealershipCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-dealership-card";
  const heading = document.createElement("div");
  heading.className = "webchat-dealership-heading";
  heading.append(
    textElement("h3", "", value.name || "Northstar dealership"),
    textElement("span", "webchat-dealership-town", value.town || ""),
  );
  card.append(heading);
  const details = document.createElement("dl");
  details.className = "webchat-dealership-details";
  [["Address", value.address || value.addressLine], ["Postcode", value.postcode], ["Phone", value.phone], ["Email", value.email]]
    .filter(([, detail]) => detail)
    .forEach(([label, detail]) => {
      const value = textElement("dd", label === "Email" ? "webchat-dealership-email" : "", detail);
      details.append(textElement("dt", "", label), value);
    });
  if (details.childElementCount) card.append(details);
  if (Array.isArray(value.brands) && value.brands.length) {
    card.append(textElement("p", "webchat-dealership-brands", value.brands.join(" · ")));
  }
  if (Array.isArray(value.departments) && value.departments.length) {
    const departments = document.createElement("div");
    departments.className = "webchat-dealership-departments";
    departments.append(textElement("strong", "", "Departments"));
    const chips = document.createElement("div");
    chips.className = "webchat-department-chips";
    value.departments.forEach((department) => chips.append(textElement("span", "", String(department).replace(/^./, (letter) => letter.toUpperCase()))));
    departments.append(chips);
    card.append(departments);
  }
  return card;
}

function openingTime(value) {
  if (!value) return "Closed";
  const [hours, minutes] = String(value).split(":").map(Number);
  const suffix = hours >= 12 ? "pm" : "am";
  const displayHour = hours % 12 || 12;
  return `${displayHour}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

function openingSchedule(entry) {
  return entry?.closed ? "Closed" : `${openingTime(entry?.opensAt)} – ${openingTime(entry?.closesAt)}`;
}

function openingDepartmentSchedule(department, hiddenPeriods = []) {
  const details = document.createElement("dd");
  if (!Array.isArray(department.periods)) {
    details.textContent = department.display || openingSchedule(department);
    return details;
  }
  const schedules = document.createElement("div");
  schedules.className = "webchat-opening-schedules";
  department.periods
    .filter((period) => !hiddenPeriods.includes(period.label))
    .forEach((period) => {
    const row = document.createElement("div");
    row.append(
      textElement("span", "webchat-opening-period", period.label),
      textElement("span", "", period.schedule),
    );
    schedules.append(row);
  });
  if (!schedules.childElementCount) details.textContent = "Closed";
  else details.append(schedules);
  return details;
}

function openingHolidayDate(value) {
  if (!value) return "Holiday exception";
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(date);
}

function sharedClosedPeriods(departments) {
  if (!departments.length || !departments.every((department) => Array.isArray(department.periods))) {
    return [];
  }
  return departments[0].periods
    .filter((period) => period.schedule === "Closed")
    .map((period) => period.label)
    .filter((label) => departments.every((department) => department.periods.some(
      (period) => period.label === label && period.schedule === "Closed",
    )));
}

function openingHoursCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-opening-hours-card";
  const header = document.createElement("header");
  header.className = "webchat-opening-hours-heading";
  header.append(
    textElement("h3", "", value.name || "Northstar location"),
    textElement("span", "webchat-opening-location", `${value.day || "Current"} hours`),
  );
  card.append(header);
  const departments = document.createElement("dl");
  departments.className = "webchat-opening-departments";
  const entries = value.day === "Weekly" ? summarizeWeeklyDepartments(value.departments || []) : value.departments || [];
  const closedPeriods = sharedClosedPeriods(entries);
  entries.forEach((department) => {
    departments.append(
      textElement("dt", "", department.name || "Department"),
      openingDepartmentSchedule(department, closedPeriods),
    );
  });
  card.append(departments);
  if (closedPeriods.length) {
    const labels = closedPeriods.map((label) => label === "Sun" ? "Sundays" : label).join(" and ");
    card.append(textElement("p", "webchat-opening-closed-days", `Closed ${labels}.`));
  }
  const holidayExceptions = Array.isArray(value.holidayExceptions) ? value.holidayExceptions : [];
  if (holidayExceptions.length) {
    const holiday = document.createElement("section");
    holiday.className = "webchat-opening-holiday";
    const label = holidayExceptions[0].label || "Holiday exception";
    holiday.append(
      textElement("span", "webchat-opening-section-label", "Holiday hours"),
      textElement("strong", "", `${label} · ${openingHolidayDate(holidayExceptions[0].date)}`),
    );
    const holidayHours = document.createElement("dl");
    holidayHours.className = "webchat-opening-departments webchat-opening-holiday-hours";
    holidayExceptions.forEach((exception) => {
      holidayHours.append(
        textElement("dt", "", String(exception.department || "Department").replace(/^./, (letter) => letter.toUpperCase())),
        textElement("dd", "", openingSchedule(exception)),
      );
    });
    holiday.append(holidayHours);
    card.append(holiday);
  }
  return card;
}

function summarizeWeeklyDepartments(items) {
  const grouped = new Map();
  items.forEach((item) => {
    const name = item.name || "Department";
    if (!grouped.has(name)) grouped.set(name, []);
    grouped.get(name).push(item);
  });
  const dayOrder = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const label = (day) => day === "Monday" ? "Mon–Fri" : day.slice(0, 3);
  const output = [];
  grouped.forEach((rows, name) => {
    const bySchedule = new Map();
    rows.forEach((row) => {
      const schedule = openingSchedule(row);
      if (!bySchedule.has(schedule)) bySchedule.set(schedule, { days: [], row });
      bySchedule.get(schedule).days.push(row.day);
    });
    const periods = [];
    bySchedule.forEach(({ days, row }, schedule) => {
      const ordered = days.filter(Boolean).sort((a, b) => dayOrder.indexOf(a) - dayOrder.indexOf(b));
      const weekday = ordered.length === 5 && ordered.slice(0, 5).every((day, index) => dayOrder[index] === day);
      const period = weekday ? "Mon–Fri" : ordered.map(label).join(" · ");
      periods.push({ label: period, schedule });
    });
    output.push({ name, periods });
  });
  return output;
}

function titleCaseDisplay(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function dealershipName(view, dealershipId) {
  const dealership = (view.dealerships || []).find(
    (item) => String(item.id) === String(dealershipId),
  );
  return dealership?.name || dealership?.town || "Selected dealership";
}

function vehicleDisplayName(vehicle) {
  const model = [vehicle?.year, vehicle?.make, vehicle?.model].filter(Boolean).join(" ");
  return [model, vehicle?.variant].filter(Boolean).join(" · ") || "Selected vehicle";
}

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

function confirmationCard(view) {
  if (view.kind === "workshop_amend") return workshopAmendmentConfirmationCard(view);
  if (view.kind === "vehicle_interest") return vehicleInterestConfirmationCard(view);
  if (view.kind === "callback") return callbackConfirmationCard(view);
  if (["sales_enquiry", "dealership_message"].includes(view.kind)) {
    return dealershipEnquiryConfirmationCard(view);
  }
  const card = document.createElement("section");
  card.className = "webchat-confirmation";
  const isCancellation = view.kind === "workshop_cancel";
  const title = isCancellation
    ? "Cancel this workshop booking?"
    : "Review before confirming";
  card.append(textElement("strong", "", title));
  if (isCancellation) {
    card.append(textElement("p", "", "The appointment will be cancelled and its time released."));
  }
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
    isCancellation ? "Confirm cancellation" : "Confirm",
  );
  confirm.type = "button";
  confirm.dataset.chatAction = "confirm";
  confirm.dataset.draftId = view.draftId;
  const cancel = textElement(
    "button",
    "",
    isCancellation ? "Keep current booking" : "Cancel",
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

function draftCard(view) {
  if (view.kind === "part_exchange" && Array.isArray(view.dealerships)) {
    return partExchangeDetailsCard(view);
  }
  if (view.kind === "callback" && Array.isArray(view.dealerships)) {
    return callbackDetailsCard(view);
  }
  if (view.kind === "sales_enquiry" && Array.isArray(view.dealerships)) {
    return salesEnquiryDetailsCard(view);
  }
  if (view.kind === "vehicle_interest" && view.summary?.vehicleId) {
    return vehicleInterestDetailsCard(view);
  }
  if (view.kind === "dealership_message" && Array.isArray(view.dealerships)) {
    return dealershipMessageDetailsCard(view);
  }
  if (view.kind === "workshop_amend") {
    return workshopAmendmentDetailsCard(view);
  }
  const card = document.createElement("section");
  card.className = "webchat-confirmation";
  card.append(
    textElement("strong", "", "A few details are needed"),
    textElement("p", "", "Please reply with the remaining details so I can prepare this request."),
  );
  const labels = {
    dealershipId: "preferred dealership",
    email: "email address",
    firstName: "first name",
    lastName: "last name",
    phone: "phone number",
  };
  const missing = (view.missingFields || []).map((field) => labels[field] || field).join(", ");
  if (missing) card.append(textElement("p", "", `Still needed: ${missing}.`));
  if (view.privacyContact) {
    card.append(textElement("small", "", `Privacy questions: ${view.privacyContact}`));
  }
  return card;
}

function workflowDetailsCard(view, { title, description, fields, hidden = {}, submitLabel }) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workflow-form-card";
  card.dataset.workflowKind = view.kind || "";
  card.append(textElement("strong", "", title), textElement("p", "", description));
  const form = document.createElement("form");
  form.dataset.workflowDetails = view.kind;
  const grid = document.createElement("div");
  grid.className = "webchat-details-grid webchat-workflow-fields";
  grid.append(...fields);
  const summary = view.summary || {};
  applyServerFormValues(grid, summary);
  Object.entries(hidden).forEach(([name, value]) => {
    if (value === undefined || value === null || value === "") return;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.append(input);
  });
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", submitLabel);
  submit.type = "submit";
  actions.append(submit);
  form.append(
    grid,
    textElement("small", "webchat-privacy-note", "Your details are sent securely to Northstar Motors and are not repeated in the confirmation."),
    actions,
  );
  if (view.privacyContact) {
    form.append(textElement("small", "webchat-privacy-note", `Privacy questions: ${view.privacyContact}`));
  }
  card.append(form);
  return card;
}

function dealershipOptions(view) {
  return (view.dealerships || []).map((dealership) => ({
    value: dealership.id,
    label: [dealership.name, dealership.town].filter(Boolean).join(" · "),
  }));
}

function salesEnquiryDetailsCard(view) {
  const summary = view.summary || {};
  return workflowDetailsCard(view, {
    title: "Send a sales enquiry",
    description: "Choose a dealership, tell us what you need, and add your contact details.",
    fields: [
      selectField("dealershipId", "Dealership", dealershipOptions(view), summary.dealershipId),
      selectField("enquiryType", "Enquiry type", [
        { value: "general", label: "General" },
        { value: "availability", label: "Vehicle availability" },
        { value: "finance", label: "Finance" },
        { value: "part-exchange", label: "Part exchange" },
      ], summary.enquiryType || "general"),
      textAreaField("message", "How can sales help?", {
        minLength: 5,
        maxLength: 2000,
        rows: 3,
        value: summary.message || "",
      }),
      ...contactFormFields(),
    ],
    hidden: { vehicleId: summary.vehicleId },
    submitLabel: "Prepare sales enquiry",
  });
}

function interestVehicleSummary(vehicle) {
  const selected = document.createElement("section");
  selected.className = "webchat-interest-vehicle";
  if (vehicle) {
    const heading = document.createElement("div");
    heading.className = "webchat-interest-vehicle-heading";
    heading.append(
      textElement("span", "webchat-flow-eyebrow", "Selected vehicle"),
      textElement("strong", "", `${vehicle.make || ""} ${vehicle.model || ""}`.trim() || "Selected vehicle"),
    );
    if (vehicle.availability) {
      heading.append(textElement("span", `webchat-status-pill ${vehicle.availability}`, vehicle.availability));
    }
    selected.append(heading);
    const identity = [vehicle.year, vehicle.variant, vehicle.dealershipTown].filter(Boolean).join(" · ");
    if (identity) selected.append(textElement("p", "webchat-interest-vehicle-meta", identity));
    const facts = document.createElement("dl");
    [
      ["Mileage", Number.isFinite(Number(vehicle.mileage)) ? `${Number(vehicle.mileage).toLocaleString("en-GB")} miles` : null],
      ["Fuel", vehicle.fuelType],
      ["Gearbox", vehicle.transmission],
      ["Price", Number.isInteger(vehicle.pricePence) ? moneyFromPence(vehicle.pricePence) : "Price on request"],
    ].filter(([, value]) => value).forEach(([label, value]) => {
      facts.append(textElement("dt", "", label), textElement("dd", "", value));
    });
    if (facts.childElementCount) selected.append(facts);
  }
  return selected;
}

function vehicleInterestDetailsCard(view) {
  const summary = view.summary || {};
  const vehicle = view.vehicle || {};
  const vehicleName = `${vehicle.make || ""} ${vehicle.model || ""}`.trim();
  const vehicleContext = [vehicle.year, vehicle.variant, vehicle.dealershipTown].filter(Boolean).join(" · ");
  const card = workflowDetailsCard(view, {
    title: vehicleName ? `Register interest in ${vehicleName}` : "Register interest",
    description: vehicleContext
      ? `${vehicleContext}. Add your contact details below.`
      : "Add your contact details below.",
    fields: [
      ...contactFormFields(),
      textAreaField("notes", "Notes (optional)", { required: false, maxLength: 1000, rows: 2 }),
    ],
    hidden: { vehicleId: summary.vehicleId },
    submitLabel: "Prepare interest request",
  });
  const fields = card.querySelector(".webchat-workflow-fields");
  if (fields) fields.before(textElement("span", "webchat-flow-eyebrow webchat-interest-details-label", "Your details"));
  return card;
}

function dealershipMessageDetailsCard(view) {
  const summary = view.summary || {};
  return workflowDetailsCard(view, {
    title: "Leave a dealership message",
    description: "Choose the right team and add the message you would like them to receive.",
    fields: [
      selectField("dealershipId", "Dealership", dealershipOptions(view), summary.dealershipId),
      selectField("department", "Department", [
        { value: "general", label: "General" },
        { value: "sales", label: "Sales" },
        { value: "service", label: "Service" },
        { value: "parts", label: "Parts" },
      ], summary.department || "general"),
      formField("subject", "Subject", "text", "off", { minLength: 3, maxLength: 120 }),
      textAreaField("message", "Message", { minLength: 5, maxLength: 2000, rows: 3 }),
      selectField("preferredContactMethod", "Preferred reply method", [
        { value: "email", label: "Email" },
        { value: "phone", label: "Phone" },
      ], summary.preferredContactMethod || "email"),
      ...contactFormFields(),
    ],
    submitLabel: "Prepare message",
  });
}

function workshopAmendmentDetailsCard(view) {
  const card = workflowDetailsCard(view, {
    title: "Change your workshop booking",
    description: "Enter only the booking details you want to update.",
    fields: [
      formField("mileage", "Updated mileage (optional)", "number", "off", {
        required: false,
        min: 0,
        max: 2000000,
        inputMode: "numeric",
      }),
      textAreaField("notes", "Updated notes (optional)", { required: false, maxLength: 1000, rows: 3 }),
    ],
    submitLabel: "Review booking changes",
  });
  const chooseTime = textElement("button", "webchat-secondary-action", "Choose a new appointment time");
  chooseTime.type = "button";
  chooseTime.dataset.chatAction = "workshop-amendment-times";
  card.querySelector(".webchat-inline-actions")?.prepend(chooseTime);
  const summary = view.summary || {};
  const current = [
    summary.bookingReference ? `Reference ${summary.bookingReference}` : null,
    summary.service,
    summary.dealership,
    summary.currentAppointment ? formatAppointment(summary.currentAppointment) : null,
  ].filter(Boolean);
  if (current.length) {
    const booking = document.createElement("section");
    booking.className = "webchat-current-booking";
    booking.append(
      textElement("span", "webchat-flow-eyebrow", "Current booking"),
      textElement("strong", "", summary.service || "Workshop appointment"),
      textElement(
        "span",
        "",
        [summary.dealership, summary.currentAppointment ? formatAppointment(summary.currentAppointment) : null]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    if (summary.bookingReference) {
      booking.append(textElement("span", "webchat-booking-reference", summary.bookingReference));
    }
    card.insertBefore(booking, card.querySelector("form"));
  }
  return card;
}

function callbackDetailsCard(view) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-callback-card";
  card.append(
    textElement("strong", "", "Request a dealership callback"),
    textElement("p", "", "Choose where to call you and tell us what you need help with."),
  );
  const form = document.createElement("form");
  form.dataset.callbackDetails = "true";
  const summary = view.summary || {};
  const dealershipOptions = view.dealerships.map((dealership) => ({
    value: dealership.id,
    label: [dealership.name, dealership.town].filter(Boolean).join(" · "),
  }));
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-callback-fields";
  fields.append(
    selectField("dealershipId", "Dealership", dealershipOptions, summary.dealershipId),
    selectField("department", "Department", [
      { value: "sales", label: "Sales" },
      { value: "service", label: "Service" },
      { value: "parts", label: "Parts" },
    ], summary.department || "sales"),
    formField("firstName", "First name", "text", "given-name", { minLength: 2, maxLength: 100 }),
    formField("lastName", "Last name", "text", "family-name", { minLength: 2, maxLength: 100 }),
    formField("phone", "Phone", "tel", "tel", { inputMode: "tel", maxLength: 30 }),
    formField("email", "Email", "email", "email"),
    formField("preferredTime", "Preferred time (optional)", "text", "off", {
      required: false,
      maxLength: 120,
      placeholder: "e.g. weekday afternoon",
    }),
    formField("reason", "What should we call about?", "text", "off", { minLength: 5, maxLength: 1000 }),
  );
  applyServerFormValues(fields, summary);
  if (summary.vehicleId) {
    const vehicle = document.createElement("input");
    vehicle.type = "hidden";
    vehicle.name = "vehicleId";
    vehicle.value = summary.vehicleId;
    form.append(vehicle);
  }
  form.append(fields, textElement("small", "webchat-privacy-note", "Your details are sent securely to the dealership team."));
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Prepare callback request");
  submit.type = "submit";
  actions.append(submit);
  form.append(actions);
  card.append(form);
  return card;
}

function selectField(name, labelText, options, selectedValue = "") {
  const label = textElement("label", "", labelText);
  const select = document.createElement("select");
  select.name = name;
  select.required = true;
  options.forEach((option) => {
    const choice = document.createElement("option");
    choice.value = option.value;
    choice.textContent = option.label;
    choice.selected = option.value === selectedValue;
    select.append(choice);
  });
  const error = textElement("small", "webchat-field-error", "");
  error.dataset.fieldError = name;
  error.hidden = true;
  label.append(select, error);
  return label;
}

function partExchangeDetailsCard(view) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-part-exchange-card";
  card.append(
    textElement("strong", "", "Tell us about your part exchange"),
    textElement("p", "", "Choose a dealership and add your contact details. We’ll return an indicative range with the applicable qualification.")
  );
  const form = document.createElement("form");
  form.dataset.partExchangeDetails = "true";
  const summary = view.summary || {};
  const dealershipOptions = view.dealerships.map((dealership) => ({
    value: dealership.id,
    label: [dealership.name, dealership.town].filter(Boolean).join(" · "),
  }));
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-part-exchange-fields";
  fields.append(
    selectField("dealershipId", "Dealership", dealershipOptions, summary.dealershipId),
    formField("registration", "Registration", "text", "off", { minLength: 2, maxLength: 12, placeholder: "e.g. AB19 XYZ" }),
    formField("mileage", "Mileage", "number", "off", { min: 0, max: 2000000, inputMode: "numeric" }),
    selectField("condition", "Condition", [
      { value: "excellent", label: "Excellent" },
      { value: "good", label: "Good" },
      { value: "fair", label: "Fair" },
    ], summary.condition || "good"),
    formField("firstName", "First name", "text", "given-name", { minLength: 2, maxLength: 100 }),
    formField("lastName", "Last name", "text", "family-name", { minLength: 2, maxLength: 100 }),
    formField("email", "Email", "email", "email"),
    formField("phone", "Phone", "tel", "tel", { inputMode: "tel", maxLength: 30, placeholder: "e.g. 07123 456789" }),
  );
  applyServerFormValues(fields, summary);
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "Your details are used to prepare this estimate and contact you about it."),
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Get indicative estimate");
  submit.type = "submit";
  actions.append(submit);
  form.append(actions);
  if (view.privacyContact) {
    form.append(textElement("small", "webchat-privacy-note", `Privacy questions: ${view.privacyContact}`));
  }
  card.append(form);
  return card;
}

function partExchangeEstimateForm(view) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-part-exchange-card";
  card.append(
    textElement("strong", "", "Get an indicative part-exchange estimate"),
    textElement("p", "", "Add three vehicle details. No dealership or contact details are needed."),
  );
  const form = document.createElement("form");
  form.dataset.partExchangeEstimate = "true";
  const values = view.values || {};
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-part-exchange-fields";
  fields.append(
    formField("registration", "Registration", "text", "off", {
      minLength: 2,
      maxLength: 12,
      placeholder: "e.g. AB19 XYZ",
    }),
    formField("mileage", "Mileage", "number", "off", {
      min: 0,
      max: 1000000,
      inputMode: "numeric",
    }),
    selectField("condition", "Condition", [
      { value: "excellent", label: "Excellent" },
      { value: "good", label: "Good" },
      { value: "fair", label: "Fair" },
    ], values.condition || "good"),
  );
  applyServerFormValues(fields, values);
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Get indicative estimate");
  submit.type = "submit";
  actions.append(submit);
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "The result is indicative and subject to vehicle inspection and market conditions."),
    actions,
  );
  card.append(form);
  return card;
}

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
  if (titles[view.kind]) {
    const card = document.createElement("article");
    card.className = "webchat-inline-receipt webchat-receipt-card";
    card.append(
      textElement("span", "webchat-receipt-mark", "✓"),
      textElement("strong", "", titles[view.kind]),
      textElement(
        "p",
        "",
        view.reference
          ? `Reference ${view.reference}`
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

function partExchangeEstimateCard(view) {
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

function privateLookupForm(view = {}) {
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

function workshopBookingDetailsCard(view) {
  const card = document.createElement("article");
  card.className = "webchat-card webchat-workshop-booking-card";
  card.append(
    textElement("p", "webchat-flow-eyebrow", `Booking ${view.status || "verified"}`),
    textElement("strong", "", view.serviceTypeName || "Workshop appointment"),
  );
  const details = [
    view.reference ? `Reference ${view.reference}` : null,
    view.startsAt ? formatAppointment(view.startsAt) : null,
    view.dealershipName || view.dealershipTown,
  ].filter(Boolean);
  if (details.length) card.append(textElement("p", "", details.join(" · ")));
  if (view.status === "confirmed") {
    const actions = document.createElement("div");
    actions.className = "webchat-inline-actions";
    const amend = textElement("button", "webchat-secondary-action", "Change booking");
    amend.type = "button";
    amend.dataset.chatAction = "start-workshop-amendment";
    const cancel = textElement("button", "webchat-primary-action", "Cancel booking");
    cancel.type = "button";
    cancel.dataset.chatAction = "start-workshop-cancellation";
    actions.append(amend, cancel);
    card.append(actions);
  }
  return card;
}

function richSummary(message) {
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
    if (page > 1 && visible) {
      const start = (page - 1) * pageSize + 1;
      const end = Math.min(start + visible - 1, total);
      return `Showing more vehicles: ${start}–${end} of ${total} available.`;
    }
    if (visible < total) {
      return `Showing ${visible} of ${total} available vehicles matching your request.`;
    }
    return `Showing ${visible} available ${visible === 1 ? "vehicle" : "vehicles"} matching your request.`;
  }
  if (message.viewType === "opening_hours") {
    return `${message.view?.day || "Current"} opening hours are shown below.`;
  }
  if (message.viewType === "dealership_list") return "Dealership details are shown below.";
  if (message.viewType === "workshop_location_list") return "Workshop locations are shown below.";
  if (message.viewType === "service_list") return "Supported workshop services are shown below.";
  if (message.viewType === "offer_list") return "Current published offers are shown below.";
  if (message.viewType === "slot_list") {
    return count
      ? "Available workshop times are shown below."
      : message.view?.emptyMessage || "No matching workshop times are currently available.";
  }
  if (message.viewType === "test_drive_slot_picker") return "Available test-drive times are shown below.";
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
  if (view) item.append(testDriveSlotPicker(view));
}

function renderServiceList(item, view) {
  if (Array.isArray(view?.items)) {
    const cards = document.createElement("div");
    cards.className = "webchat-cards webchat-service-list";
    view.items.slice(0, 12).forEach((service) => cards.append(serviceCard(service)));
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
  offer_list(item, view) {
    if (Array.isArray(view?.items)) {
      renderCardList(item, view, offerCard);
      if (view.financeNotice) item.append(textElement("small", "", view.financeNotice));
    }
  },
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
    const suggestions = suggestionChips(
      message.view.suggestions,
      {
        allowMany: serviceChoices,
        className: serviceChoices ? "webchat-service-suggestions" : "",
      },
    );
    if (suggestions.childElementCount) item.append(suggestions);
  }
  return item;
}

export function renderWorkflowCard(view, viewType = null) {
  if (viewType === "part_exchange_estimate") return partExchangeEstimateCard(view);
  return view?.status === "awaiting_confirmation" ? confirmationCard(view) : draftCard(view);
}
