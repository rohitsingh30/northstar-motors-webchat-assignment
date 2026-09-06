const choice = (label, value = label) => ({ label, value });

export const FIELD_DEFINITIONS = Object.freeze({
  registration: {
    label: "vehicle registration",
    reviewLabel: "Registration",
    prompt: "What’s your vehicle registration? For example, AB12 CDE.",
    private: true,
    mask: "Vehicle registration entered privately",
  },
  mileage: {
    label: "current mileage",
    reviewLabel: "Mileage",
    reviewFormat: "mileage",
    prompt: "Thanks — what’s the vehicle’s current mileage? Enter the dashboard reading, for example 24,000 miles.",
    private: true,
    mask: "Current mileage entered privately",
  },
  fullName: {
    label: "full name",
    reviewLabel: "Full name",
    prompt: "Thanks. What’s your first and last name? For example, Alex Morgan.",
    private: true,
    mask: "Name entered privately",
  },
  email: {
    label: "email address",
    reviewLabel: "Email",
    prompt: "And what email address should Northstar use? For example, name@example.com.",
    private: true,
    mask: "Email entered privately",
  },
  phone: {
    label: "UK phone number",
    reviewLabel: "Phone",
    prompt: "Finally, what’s the best UK phone number to reach you on? For example, 07700 900123.",
    private: true,
    mask: "Phone number entered privately",
  },
  condition: {
    label: "vehicle condition",
    reviewLabel: "Condition",
    prompt: "How would you describe the vehicle’s condition?",
    private: true,
    mask: "Vehicle condition entered privately",
    choices: [
      choice("Excellent", "excellent"),
      choice("Good", "good"),
      choice("Fair", "fair"),
    ],
  },
  reference: {
    label: "booking reference",
    reviewLabel: "Booking reference",
    prompt: "What’s your booking reference? It usually looks like WORK-12345 and is shown in your confirmation email.",
    private: true,
    mask: "Booking reference entered privately",
  },
  lastName: {
    label: "surname",
    reviewLabel: "Surname",
    prompt: "Thanks — what surname is the booking under?",
    private: true,
    mask: "Surname entered privately",
  },
});

export function fieldDefinition(field) {
  return FIELD_DEFINITIONS[field] || { label: field, prompt: `Please provide ${field}.` };
}

export function fieldChoices(field) {
  return fieldDefinition(field).choices || [];
}
