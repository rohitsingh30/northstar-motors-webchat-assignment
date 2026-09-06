// Protected-only question groups. Public capability fields are resolved by the server conversation
// before one of these sessions is activated.
const contact = ["fullName", "email", "phone"];

export const WORKFLOW_CONVERSATION_SPECS = Object.freeze({
  test_drive: {
    label: "test drive",
    groups: [{ id: "contact", fields: contact }],
  },
  workshop_booking: {
    label: "workshop booking",
    groups: [
      { id: "vehicle_details", fields: ["registration", "mileage"] },
      { id: "contact", fields: contact },
    ],
  },
  sales_enquiry: {
    label: "sales enquiry",
    groups: [{ id: "contact", fields: contact }],
  },
  vehicle_interest: {
    label: "vehicle interest request",
    groups: [{ id: "contact", fields: contact }],
  },
  callback: {
    label: "callback request",
    groups: [{ id: "contact", fields: contact }],
  },
  dealership_message: {
    label: "dealership message",
    groups: [{ id: "contact", fields: contact }],
  },
  part_exchange_estimate: {
    label: "part-exchange estimate",
    groups: [{ id: "vehicle_details", fields: ["registration", "mileage", "condition"] }],
  },
  part_exchange: {
    label: "part-exchange request",
    secureHandoffs: [
      {
        source: "part_exchange_estimate",
        transferFields: ["registration", "mileage", "condition"],
        disposition: "consume",
      },
    ],
    groups: [
      { id: "vehicle_details", fields: ["registration", "mileage", "condition"] },
      { id: "contact", fields: contact },
    ],
  },
  booking_lookup: {
    label: "booking lookup",
    groups: [
      { id: "booking_verification", fields: ["reference", "lastName", "registration", "phone"] },
    ],
  },
});

export function workflowSteps(spec) {
  return (spec?.groups || []).flatMap((group) => group.fields);
}

export function questionGroup(spec, id) {
  return (spec?.groups || []).find((group) => group.id === id) || null;
}
