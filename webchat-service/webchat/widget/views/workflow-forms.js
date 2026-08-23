import { textElement } from "../core/dom.js";
import { moneyFromPence } from "../core/format.js";
import {
  applyServerFormValues,
  contactFormFields,
  flowCloseButton,
  formField,
  formatAppointment,
  textAreaField,
} from "./appointments.js";

// Draft cards and forms collect only the fields required to prepare a protected workflow.

export function draftCard(view) {
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

function workflowDetailsCard(view, {
  title,
  description,
  fields,
  hidden = {},
  submitLabel,
  cancelAction = "",
  cancelPlacement = "actions",
}) {
  const card = document.createElement("section");
  card.className = "webchat-confirmation webchat-workflow-form-card";
  card.dataset.workflowKind = view.kind || "";
  if (cancelAction && cancelPlacement === "header") {
    const heading = document.createElement("div");
    heading.className = "webchat-workflow-form-heading";
    heading.append(textElement("strong", "", title));
    heading.append(flowCloseButton("offer-enquiry", view.draftId));
    card.append(heading);
  } else {
    card.append(textElement("strong", "", title));
  }
  card.append(textElement("p", "", description));
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
  if (cancelAction && cancelPlacement === "actions") {
    const cancel = textElement("button", "webchat-secondary-action", "Cancel");
    cancel.type = "button";
    cancel.dataset.chatAction = cancelAction;
    if (view.draftId) cancel.dataset.draftId = view.draftId;
    actions.append(cancel);
  }
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  form.append(
    grid,
    textElement("small", "webchat-privacy-note", "Your details are sent securely to Northstar Motors and are not repeated in the confirmation."),
    formError,
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

function salesEnquiryDetailsCard(view, { inline = false } = {}) {
  const summary = view.summary || {};
  const offerLabel = inline ? view.contextLabel : "";
  return workflowDetailsCard(view, {
    title: offerLabel ? `Sales enquiry for ${offerLabel}` : "Send a sales enquiry",
    description: "Choose a dealership, tell us what you need, and add your contact details.",
    fields: [
      selectField("dealershipId", "Dealership", dealershipOptions(view), summary.dealershipId),
      selectField("enquiryType", "Enquiry type", [
        { value: "general", label: "General" },
        { value: "availability", label: "Vehicle availability" },
        { value: "finance", label: "Finance" },
        { value: "part-exchange", label: "Part exchange" },
      ], summary.enquiryType, "general"),
      textAreaField("message", "How can sales help?", {
        minLength: 5,
        maxLength: 2000,
        rows: 3,
        value: summary.message || salesEnquiryMessageDefault(view),
      }),
      ...contactFormFields(),
    ],
    hidden: { vehicleId: summary.vehicleId },
    submitLabel: "Prepare sales enquiry",
    cancelAction: inline ? "cancel-inline-offer-enquiry" : "cancel-workflow-form",
    cancelPlacement: inline ? "header" : "actions",
  });
}

function salesEnquiryMessageDefault(view) {
  const vehicle = view.vehicle || {};
  const vehicleName = `${vehicle.make || ""} ${vehicle.model || ""}`.trim();
  if (vehicleName) return `Please contact me about the ${vehicleName}.`;
  return {
    availability: "Please contact me about vehicle availability.",
    finance: "Please contact me about vehicle finance.",
    "part-exchange": "Please contact me about a part exchange.",
  }[view.summary?.enquiryType] || "Please contact me about my sales enquiry.";
}

export function inlineOfferEnquiryForm(view) {
  return salesEnquiryDetailsCard(view, { inline: true });
}

export function setOfferEnquiryActionState(flow, expanded, hasEnquiry = expanded) {
  const launch = flow?.launchButton || flow?.closest(".webchat-offer-card")
    ?.querySelector('[data-chat-action="offer-enquiry"]');
  if (!launch) return;
  const offerLabel = launch.dataset.offerLabel || "this offer";
  launch.disabled = false;
  launch.textContent = expanded ? "Hide enquiry" : hasEnquiry ? "View enquiry" : "Send sales enquiry";
  launch.setAttribute("aria-expanded", String(expanded));
  launch.setAttribute(
    "aria-label",
    expanded
      ? `Hide the sales enquiry about ${offerLabel}`
      : hasEnquiry
      ? `View the sales enquiry about ${offerLabel}`
      : `Send a sales enquiry about ${offerLabel}`,
  );
}

export function interestVehicleSummary(vehicle) {
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
      textAreaField("notes", "Notes (optional)", {
        required: false,
        maxLength: 1000,
        rows: 2,
        value: summary.notes || "Please contact me if this vehicle becomes available.",
      }),
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
  const messageDefaults = dealershipMessageDefaults(summary.department);
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
      ], summary.department, "general"),
      formField("subject", "Subject", "text", "off", {
        minLength: 3,
        maxLength: 120,
        value: summary.subject || messageDefaults.subject,
      }),
      textAreaField("message", "Message", {
        minLength: 5,
        maxLength: 2000,
        rows: 3,
        value: summary.message || messageDefaults.message,
      }),
      selectField("preferredContactMethod", "Preferred reply method", [
        { value: "email", label: "Email" },
        { value: "phone", label: "Phone" },
      ], summary.preferredContactMethod, "email"),
      ...contactFormFields(),
    ],
    submitLabel: "Prepare message",
  });
}

function dealershipMessageDefaults(department) {
  const supportedDepartment = ["sales", "service", "parts"].includes(department)
    ? department
    : "general";
  const label = supportedDepartment === "general"
    ? "General"
    : supportedDepartment[0].toUpperCase() + supportedDepartment.slice(1);
  return {
    subject: `${label} enquiry`,
    message: supportedDepartment === "general"
      ? "Please contact me about my enquiry."
      : `Please contact me about a ${supportedDepartment} enquiry.`,
  };
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
    submitLabel: "Review mileage or notes",
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
    ], summary.department, "sales"),
    ...contactFormFields(),
    formField("preferredTime", "Preferred time (optional)", "text", "off", {
      required: false,
      maxLength: 120,
      placeholder: "e.g. weekday afternoon",
    }),
    formField("reason", "What should we call about?", "text", "off", {
      minLength: 5,
      maxLength: 1000,
      value: summary.reason || callbackReasonDefault(summary),
    }),
  );
  applyServerFormValues(fields, summary);
  if (summary.vehicleId) {
    const vehicle = document.createElement("input");
    vehicle.type = "hidden";
    vehicle.name = "vehicleId";
    vehicle.value = summary.vehicleId;
    form.append(vehicle);
  }
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "Your details are sent securely to the dealership team."),
    formError,
  );
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Prepare callback request");
  submit.type = "submit";
  actions.append(submit);
  form.append(actions);
  card.append(form);
  return card;
}

function callbackReasonDefault(summary) {
  if (summary.vehicleId) return "Discuss a vehicle enquiry";
  return {
    service: "Discuss a service enquiry",
    parts: "Discuss a parts enquiry",
  }[summary.department] || "Discuss a sales enquiry";
}

function selectField(name, labelText, options, selectedValue = "", defaultValue = "") {
  const label = textElement("label", "", labelText);
  const select = document.createElement("select");
  select.name = name;
  select.required = true;
  const validValues = new Set(options.map((option) => option.value));
  const effectiveValue = validValues.has(selectedValue)
    ? selectedValue
    : validValues.has(defaultValue)
    ? defaultValue
    : "";
  const hasSelectedOption = Boolean(effectiveValue);
  if (!hasSelectedOption) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = `Choose ${labelText.toLowerCase()}`;
    placeholder.disabled = true;
    placeholder.selected = true;
    select.append(placeholder);
  }
  options.forEach((option) => {
    const choice = document.createElement("option");
    choice.value = option.value;
    choice.textContent = option.label;
    choice.selected = option.value === effectiveValue;
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
    formField("mileage", "Mileage", "number", "off", {
      min: 0,
      max: 2000000,
      inputMode: "numeric",
      placeholder: "e.g. 45000",
    }),
    selectField("condition", "Condition", [
      { value: "excellent", label: "Excellent" },
      { value: "good", label: "Good" },
      { value: "fair", label: "Fair" },
    ], summary.condition, "good"),
    ...contactFormFields(),
  );
  applyServerFormValues(fields, summary);
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "Your details are used to prepare this estimate and contact you about it."),
  );
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Get indicative estimate");
  submit.type = "submit";
  actions.append(submit);
  form.append(formError, actions);
  if (view.privacyContact) {
    form.append(textElement("small", "webchat-privacy-note", `Privacy questions: ${view.privacyContact}`));
  }
  card.append(form);
  return card;
}

export function partExchangeEstimateForm(view) {
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
      placeholder: "e.g. 45000",
    }),
    selectField("condition", "Condition", [
      { value: "excellent", label: "Excellent" },
      { value: "good", label: "Good" },
      { value: "fair", label: "Fair" },
    ], values.condition, "good"),
  );
  applyServerFormValues(fields, values);
  const actions = document.createElement("div");
  actions.className = "webchat-inline-actions";
  const submit = textElement("button", "webchat-primary-action", "Get indicative estimate");
  submit.type = "submit";
  actions.append(submit);
  const formError = textElement("small", "webchat-form-error", "");
  formError.dataset.formError = "true";
  formError.hidden = true;
  form.append(
    fields,
    textElement("small", "webchat-privacy-note", "The result is indicative and subject to vehicle inspection and market conditions."),
    formError,
    actions,
  );
  card.append(form);
  return card;
}
