// Shared form validation presentation; no network or conversation state.

export function showFieldErrors(detailsForm, fieldErrors = {}) {
  const formError = detailsForm.querySelector("[data-form-error]");
  if (formError) {
    formError.hidden = true;
    formError.textContent = "";
  }
  detailsForm.querySelectorAll("[data-field-error]").forEach((message) => {
    const field = detailsForm.elements.namedItem(message.dataset.fieldError);
    field?.removeAttribute("aria-invalid");
    message.hidden = true;
    message.textContent = "";
  });
  let firstInvalid = null;
  Object.entries(fieldErrors).forEach(([name, message]) => {
    const field = detailsForm.elements.namedItem(name);
    const error = detailsForm.querySelector(`[data-field-error="${name}"]`);
    if (!field || !error) return;
    field.setAttribute("aria-invalid", "true");
    error.textContent = message;
    error.hidden = false;
    firstInvalid ||= field;
  });
  firstInvalid?.focus();
  return firstInvalid !== null;
}

export function showFormError(form, message) {
  const error = form.querySelector("[data-form-error]");
  if (!error) return false;
  error.textContent = message || "Check the booking details and try again.";
  error.hidden = false;
  return true;
}

export function applyRetainedDetails(form, details = {}) {
  Object.entries(details).forEach(([name, value]) => {
    const field = form.querySelector(`[name="${name}"]`);
    if (field && value !== undefined && value !== null) field.value = value;
  });
}
