import { textElement } from "../core/dom.js";
import { suggestionChips } from "./suggestions.js";

// Appointment renderers own the complete test-drive and workshop booking UI lifecycle.

const SLOT_TIME_ZONE = "Europe/London";

export function setWorkshopFlowContent(flow, content, surfaceOwner = "flow") {
  const children = Array.isArray(content) ? content : [content];
  flow.replaceChildren(...children);
  flow.dataset.surfaceOwner = surfaceOwner;
}

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

function workshopLocationLabel(slot) {
  if (slot.dealershipTown) return slot.dealershipTown;
  return String(slot.dealershipName || "Workshop").replace(/^Northstar\s+/i, "");
}

function testDriveVehicleHeader(view, firstSlot = {}) {
  const vehicle = view.vehicle || {};
  const make = vehicle.make || firstSlot.make || "";
  const model = vehicle.model || firstSlot.model || "";
  const title = `${make} ${model}`.trim() || "Selected vehicle";
  const location =
    vehicle.dealershipTown ||
    firstSlot.dealershipTown ||
    String(firstSlot.dealershipName || "").replace(/^Northstar\s+/i, "");
  const details = [
    vehicle.year || firstSlot.year,
    vehicle.variant || firstSlot.variant,
    location,
  ].filter(Boolean);
  const header = document.createElement("header");
  header.className = "webchat-test-drive-vehicle-header";
  header.append(
    textElement("span", "webchat-flow-eyebrow", "Book a test drive"),
    textElement("strong", "webchat-test-drive-vehicle-name", title),
  );
  if (details.length) {
    header.append(
      textElement("span", "webchat-test-drive-vehicle-details", details.join(" · ")),
    );
  }
  return header;
}

function appointmentSlotPicker(
  view,
  { inline = false, kind = "test-drive", showContext = false, serviceName = "" } = {},
) {
  const workshop = kind === "workshop";
  const slotPattern = workshop ? /^ws-slot-[0-9]{4}$/ : /^td-slot-[0-9]{4}$/;
  const slots = (view.items || []).filter(
    (slot) => slotPattern.test(slot.id || "") && !Number.isNaN(Date.parse(slot.startsAt)),
  );
  const picker = document.createElement("section");
  picker.className = `webchat-slot-picker${inline ? " is-inline" : ""}${workshop ? " is-workshop" : ""}`;
  if (!workshop) picker.append(testDriveVehicleHeader(view, slots[0]));
  const first = slots[0];
  if (workshop && (!inline || showContext)) {
    picker.append(
      textElement("span", "webchat-flow-eyebrow", "Book a workshop appointment"),
      textElement(
        "strong",
        "webchat-slot-title",
        serviceName || first?.serviceName || "Workshop appointment",
      ),
      textElement(
        "span",
        "webchat-slot-location",
        "Choose a location, date and time",
      ),
    );
  }
  if (!slots.length) {
    picker.append(
      textElement(
        "p",
        "",
        workshop
          ? "No matching workshop times are currently available."
          : view.emptyMessage ||
              "No online test-drive times are currently available for this vehicle.",
      ),
    );
    if (inline && Array.isArray(view.suggestions) && view.suggestions.length) {
      const suggestions = suggestionChips(view.suggestions);
      if (suggestions.childElementCount) picker.append(suggestions);
    }
    if (inline) picker.append(flowCloseButton(kind));
    return picker;
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

export function workshopSlotPicker(
  view,
  { inline = false, showContext = false, serviceName = "" } = {},
) {
  return appointmentSlotPicker(view, {
    inline,
    kind: "workshop",
    showContext,
    serviceName,
  });
}

export function flowCloseButton(kind, draftId = "") {
  const configuration = {
    workshop: {
      label: "Close workshop booking",
      action: "cancel-inline-workshop",
    },
    "test-drive": {
      label: "Close test-drive booking",
      action: "cancel-inline-test-drive",
    },
    "offer-enquiry": {
      label: "Cancel sales enquiry",
      action: "cancel-inline-offer-enquiry",
    },
  }[kind];
  const cancel = textElement("button", "webchat-flow-close", "×");
  cancel.type = "button";
  cancel.classList.add("is-cancel");
  cancel.setAttribute("aria-label", configuration.label);
  cancel.dataset.chatAction = configuration.action;
  if (draftId) cancel.dataset.draftId = draftId;
  return cancel;
}

export function formField(name, labelText, type, autocomplete, constraints = {}) {
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

export function textAreaField(name, labelText, constraints = {}) {
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

export function contactFormFields() {
  return [
    formField("firstName", "First name", "text", "given-name", {
      minLength: 2,
      maxLength: 100,
      placeholder: "e.g. Jamie",
    }),
    formField("lastName", "Last name", "text", "family-name", {
      minLength: 2,
      maxLength: 100,
      placeholder: "e.g. Taylor",
    }),
    formField("email", "Email", "email", "email", {
      placeholder: "e.g. jamie@example.com",
    }),
    formField("phone", "Phone", "tel", "tel", {
      inputMode: "tel",
      maxLength: 30,
      placeholder: "e.g. 07123 456789",
    }),
  ];
}

export function applyServerFormValues(container, values) {
  Object.entries(values || {}).forEach(([name, value]) => {
    const field = container.querySelector(`[name="${name}"]`);
    if (!field || value === undefined || value === null) return;
    if (
      field.tagName === "SELECT"
      && ![...field.children].some((option) => option.value === String(value))
    ) return;
    field.value = value;
    field.dataset.profileValueSource = "server";
  });
}

export function testDriveDetailsForm({ slotId, slotLabel, dealershipName, vehicleLabel }) {
  const form = document.createElement("form");
  form.className = "webchat-test-drive-details";
  form.dataset.testDriveDetails = "true";
  form.dataset.slotId = slotId;
  form.dataset.slotLabel = slotLabel;
  form.append(
    textElement("p", "webchat-flow-eyebrow", "Book a test drive"),
    textElement("strong", "webchat-selected-slot", vehicleLabel || "Selected vehicle"),
    textElement("span", "webchat-slot-location", `${slotLabel} · ${dealershipName}`),
  );
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid";
  fields.append(...contactFormFields());
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const back = textElement("button", "webchat-secondary-action", "Back");
  back.type = "button";
  back.dataset.chatAction = "back-to-test-drive-slots";
  const review = textElement("button", "webchat-primary-action", "Review booking");
  review.type = "submit";
  actions.append(back, review);
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  form.append(
    fields,
    textElement(
      "small",
      "webchat-privacy-note",
      "Your details are used only to arrange this test drive.",
    ),
    formError,
    actions,
    flowCloseButton("test-drive"),
  );
  return form;
}

export function inlineTestDriveConfirmation(view, slotLabel, vehicleLabel = "") {
  const card = document.createElement("section");
  card.className = "webchat-inline-confirmation";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Ready to book"),
    textElement("strong", "webchat-selected-slot", vehicleLabel || "Selected vehicle"),
    textElement("p", "", slotLabel),
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
  confirm.dataset.expectedKind = "test_drive";
  confirm.dataset.inlineBooking = "true";
  actions.append(edit, confirm);
  card.append(actions, flowCloseButton("test-drive", view.draftId));
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy: ${view.privacyContact}`));
  }
  return card;
}

export function confirmedBookingDisclosure(title, view, rows, extraClass = "") {
  const card = document.createElement("details");
  card.className = `webchat-inline-receipt webchat-booking-disclosure${extraClass ? ` ${extraClass}` : ""}`;

  const summary = document.createElement("summary");
  summary.className = "webchat-booking-disclosure-summary";
  const copy = document.createElement("span");
  copy.className = "webchat-booking-disclosure-copy";
  copy.append(
    textElement("strong", "", title),
    textElement(
      "span",
      "webchat-booking-disclosure-reference",
      view.reference ? `Reference ${view.reference}` : "Booking confirmed",
    ),
  );
  const mark = textElement("span", "webchat-receipt-mark", "✓");
  mark.setAttribute("aria-hidden", "true");
  const toggle = document.createElement("span");
  toggle.className = "webchat-booking-disclosure-toggle";
  toggle.append(
    textElement("span", "webchat-booking-disclosure-toggle-label", "Details"),
    textElement("span", "webchat-booking-disclosure-chevron", ""),
  );
  toggle.setAttribute("aria-hidden", "true");
  summary.append(mark, copy, toggle);
  card.append(summary);

  const details = document.createElement("dl");
  details.className = "webchat-booking-summary";
  rows.filter(([label, value]) => (
    label !== "Reference" && value !== undefined && value !== null && value !== ""
  ))
    .forEach(([label, value, valueClass = ""]) => {
      const row = document.createElement("div");
      row.append(
        textElement("dt", "", label),
        textElement("dd", valueClass, value),
      );
      details.append(row);
    });
  if (details.children.length) {
    card.append(details);
  }
  return card;
}

export function workshopBookingActions(reference = "") {
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions webchat-booking-actions";
  const reschedule = textElement("button", "webchat-primary-action", "Reschedule");
  reschedule.type = "button";
  reschedule.dataset.chatAction = "start-workshop-amendment";
  if (reference) reschedule.dataset.bookingReference = reference;
  const cancel = textElement(
    "button",
    "webchat-secondary-action webchat-danger-action",
    "Cancel booking",
  );
  cancel.type = "button";
  cancel.dataset.chatAction = "start-workshop-cancellation";
  if (reference) cancel.dataset.bookingReference = reference;
  actions.append(reschedule, cancel);
  return actions;
}

export function inlineBookingReceipt(view) {
  return confirmedBookingDisclosure("Test drive booked", view, [
    ["Vehicle", view.vehicleLabel],
    ["Appointment", view.slotLabel || (view.startsAt ? formatAppointment(view.startsAt) : "")],
    ["Location", view.dealershipName],
  ]);
}

export function workshopDetailsForm({ slotId, slotLabel, dealershipName, serviceName }) {
  const form = document.createElement("form");
  form.className = "webchat-test-drive-details webchat-workshop-details";
  form.dataset.workshopDetails = "true";
  form.dataset.slotId = slotId;
  form.append(
    textElement("p", "webchat-flow-eyebrow", "Book a workshop appointment"),
    textElement("strong", "webchat-selected-slot", serviceName || "Workshop service"),
    textElement("span", "webchat-slot-location", `${slotLabel} · ${dealershipName}`),
  );
  const fields = document.createElement("div");
  fields.className = "webchat-details-grid webchat-workshop-fields";
  fields.append(
    ...contactFormFields(),
    formField("registration", "Registration", "text", "off", {
      minLength: 2,
      maxLength: 20,
      placeholder: "e.g. AB19 XYZ",
    }),
    formField("mileage", "Current mileage", "number", "off", {
      min: 0,
      max: 2000000,
      placeholder: "e.g. 45000",
    }),
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
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "Your details are used only to arrange this workshop appointment."),
    formError,
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
  confirm.dataset.expectedKind = "workshop_booking";
  confirm.dataset.inlineBooking = "workshop";
  actions.append(edit, confirm);
  card.append(actions, flowCloseButton("workshop", view.draftId));
  if (view.privacyContact) {
    card.append(textElement("small", "webchat-privacy-note", `Privacy: ${view.privacyContact}`));
  }
  return card;
}

export function inlineWorkshopReceipt(view) {
  const card = confirmedBookingDisclosure("Workshop booked", view, [
    ["Service", view.serviceName],
    ["Appointment", view.slotLabel || (view.startsAt ? formatAppointment(view.startsAt) : "")],
    ["Location", view.dealershipName],
    ["Vehicle", view.registration ? String(view.registration).toUpperCase() : ""],
  ]);
  card.append(workshopBookingActions(view.reference));
  return card;
}

export function inlineBookedTestDrive(booking) {
  const card = confirmedBookingDisclosure("Test drive booked", booking, [
    ["Vehicle", booking.vehicleLabel],
    [
      "Appointment",
      booking.slotLabel || (booking.startsAt ? formatAppointment(booking.startsAt) : ""),
    ],
    ["Location", booking.dealershipName],
  ], "webchat-booked-test-drive");
  const close = textElement("button", "webchat-flow-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Close booked test drive");
  close.dataset.chatAction = "hide-booked-test-drive";
  card.append(close);
  return card;
}

export function bindBookedTestDriveAction(flow, action, booking) {
  if (!flow || !action || !booking) return false;
  flow.booking = { ...booking };
  flow.replaceChildren();
  flow.hidden = true;
  action.disabled = false;
  action.dataset.chatAction = "view-booked-test-drive";
  action.textContent = "View booked test drive";
  action.setAttribute("aria-expanded", "false");
  if (flow.id) action.setAttribute("aria-controls", flow.id);
  return true;
}

export function setBookedTestDriveActionState(flow, expanded) {
  const action = flow?.closest(".webchat-vehicle-card")
    ?.querySelector('[data-chat-action="view-booked-test-drive"]');
  if (!action) return;
  action.disabled = false;
  action.textContent = expanded ? "Hide booked test drive" : "View booked test drive";
  action.setAttribute("aria-expanded", String(expanded));
}
