// Persist customer identity/contact details and the vehicle registration used in ordinary forms.
// Workflow content, choices, and all private booking-lookup proof remain request-specific.
const PROFILE_KEY = "northstarFormProfileV1";
const REUSABLE_PROFILE_FIELDS = new Set([
  "firstName",
  "lastName",
  "email",
  "phone",
  "registration",
]);

export function createFormProfile(transcript, storage = localStorage) {
  function savedProfile() {
    try {
      const parsed = JSON.parse(storage.getItem(PROFILE_KEY) || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
      const profile = Object.fromEntries(
        Object.entries(parsed).filter(
          ([name, value]) => REUSABLE_PROFILE_FIELDS.has(name) && typeof value === "string",
        ),
      );
      if (Object.keys(profile).length !== Object.keys(parsed).length) {
        storage.setItem(PROFILE_KEY, JSON.stringify(profile));
      }
      return profile;
    } catch {
      return {};
    }
  }

  function reusable(field) {
    const name = String(field?.name || "");
    const type = String(field?.type || "").toLowerCase();
    return Boolean(
      name
      && REUSABLE_PROFILE_FIELDS.has(name)
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
