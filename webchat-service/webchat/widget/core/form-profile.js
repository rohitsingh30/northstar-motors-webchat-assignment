// Persist reusable ordinary-form fields, never private booking-lookup proof.
const PROFILE_KEY = "northstarFormProfileV1";
const INTERNAL_FIELDS = new Set([
  "draftId",
  "mode",
  "offerId",
  "serviceTypeId",
  "slotId",
  "vehicleId",
  "verifiedGrantId",
]);

export function createFormProfile(transcript, storage = localStorage) {
  function savedProfile() {
    try {
      const parsed = JSON.parse(storage.getItem(PROFILE_KEY) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  function reusable(field) {
    const name = String(field?.name || "");
    const type = String(field?.type || "").toLowerCase();
    return Boolean(
      name
      && !INTERNAL_FIELDS.has(name)
      && !field.disabled
      && !field.readOnly
      && !["button", "file", "hidden", "password", "reset", "submit"].includes(type)
      && field.closest("form")
      && !field.closest("[data-private-lookup]")
      && transcript.contains(field)
    );
  }

  function fields(container) {
    const selector = "input[name], select[name], textarea[name]";
    return [
      ...(container.matches?.(selector) ? [container] : []),
      ...container.querySelectorAll(selector),
    ];
  }

  function remember(container) {
    const profile = savedProfile();
    let changed = false;
    fields(container).forEach((field) => {
      if (!reusable(field)) return;
      const value = field.type === "checkbox"
        ? (field.checked ? field.value || "true" : "")
        : String(field.value || "").trim();
      if (value && profile[field.name] !== value) {
        profile[field.name] = value.slice(0, 2_000);
        changed = true;
      } else if (!value && Object.hasOwn(profile, field.name)) {
        delete profile[field.name];
        changed = true;
      }
    });
    if (!changed) return;
    try {
      storage.setItem(PROFILE_KEY, JSON.stringify(profile));
    } catch {
      // Browser privacy settings or storage limits may disable persistence.
    }
  }

  function apply(container) {
    const profile = savedProfile();
    fields(container).forEach((field) => {
      if (
        !reusable(field)
        || field.dataset.profileValueSource === "server"
        || !Object.hasOwn(profile, field.name)
      ) return;
      const value = String(profile[field.name]);
      if (field instanceof HTMLSelectElement) {
        if ([...field.options].some((option) => option.value === value)) field.value = value;
      } else if (field.type === "checkbox") {
        field.checked = value === (field.value || "true");
      } else if (!field.value) {
        field.value = value;
      }
    });
  }

  const observer = new MutationObserver((records) => {
    records.forEach((record) => {
      record.addedNodes.forEach((node) => {
        if (node instanceof Element) apply(node);
      });
    });
  });
  observer.observe(transcript, { childList: true, subtree: true });
  transcript.addEventListener("change", (event) => {
    if (reusable(event.target)) remember(event.target);
  });

  return { apply, remember, disconnect: () => observer.disconnect() };
}
