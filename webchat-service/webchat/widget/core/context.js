// Derive context from a small allow-list, never arbitrary page content.
const SECTION = /^[a-z][a-z0-9-]{0,39}$/;
const VEHICLE_ID = /^veh-[0-9]{3}$/;
const ENTITY_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/;
const ATTRIBUTE = /^[A-Za-z][A-Za-z0-9_.:-]{0,79}$/;

let selectedVehicleId = null;
let integrationSection = null;
let integrationControls = null;
let integrationEntities = null;

export function setPageContext(context = {}) {
  selectedVehicleId = VEHICLE_ID.test(context.vehicleId || "") ? context.vehicleId : null;
  integrationSection = SECTION.test(context.section || "") ? context.section : null;
  const controls = Array.isArray(context.controls) ? cleanControls(context.controls) : [];
  const entities = Array.isArray(context.entities) ? cleanEntities(context.entities) : [];
  // Empty integration snapshots must not mask richer, currently rendered page state.
  integrationControls = controls.length ? controls : null;
  integrationEntities = entities.length ? entities : null;
}

window.addEventListener("northstar:vehicle-context", (event) => {
  setPageContext(event.detail || {});
});

function cleanText(value, maxLength) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  return normalized ? normalized.slice(0, maxLength) : null;
}

function scalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function cleanControls(controls) {
  return controls.slice(0, 30).flatMap((control) => {
    const name = String(control?.name || "").slice(0, 80);
    const label = cleanText(control?.label, 120);
    const value = cleanText(control?.value, 200);
    return ATTRIBUTE.test(name) && label && value ? [{ name, label, value }] : [];
  });
}

function cleanEntities(entities) {
  const seen = new Set();
  return entities.slice(0, 50).flatMap((entity) => {
    const type = String(entity?.type || "").slice(0, 40);
    const id = String(entity?.id || "").slice(0, 80);
    const key = `${type}:${id}`;
    if (!SECTION.test(type) || !ENTITY_ID.test(id) || seen.has(key)) return [];
    seen.add(key);
    const attributes = {};
    Object.entries(entity?.attributes || {}).slice(0, 24).forEach(([name, value]) => {
      if (ATTRIBUTE.test(name) && scalar(value)) {
        attributes[name] = typeof value === "string" ? value.slice(0, 240) : value;
      }
    });
    return [{ type, id, label: cleanText(entity?.label, 200), attributes }];
  });
}

function contextRoot(section) {
  if (section !== "home") {
    const byId = document.getElementById(section);
    if (byId) return byId;
    const byLandmark = document.querySelector(`[data-page-section="${section}"]`);
    if (byLandmark) return byLandmark;
  }
  return document.querySelector("main") || document.body;
}

function visibleSection() {
  let best = null;
  document.querySelectorAll("main section[id], main [data-page-section], footer[id]")
    .forEach((element) => {
      const name = element.dataset.pageSection || element.id;
      if (!SECTION.test(name || "")) return;
      const rectangle = element.getBoundingClientRect();
      const visibleHeight = Math.max(
        0,
        Math.min(rectangle.bottom, window.innerHeight) - Math.max(rectangle.top, 0),
      );
      const score = visibleHeight / Math.max(1, Math.min(rectangle.height, window.innerHeight));
      if (!best || score > best.score) best = { name, score };
    });
  return best?.score > 0 ? best.name : null;
}

function visibleControlState(root) {
  const values = [];
  root.querySelectorAll("select, input[type='search'], input[type='radio']:checked, input[type='checkbox']:checked")
    .forEach((control) => {
      if (values.length >= 20 || control.disabled || control.closest("northstar-chat")) return;
      const value = control.type === "checkbox"
        ? "selected"
        : String(control.value || "").trim();
      if (!value) return;
      const label = control.labels?.[0]?.innerText
        || control.getAttribute("aria-label")
        || control.name
        || control.id;
      const safeLabel = cleanText(label, 80);
      const safeValue = cleanText(value, 120);
      const name = String(control.name || control.id || `control${values.length + 1}`).slice(0, 80);
      if (ATTRIBUTE.test(name) && safeLabel && safeValue) {
        values.push({ name, label: safeLabel, value: safeValue });
      }
    });
  return values;
}

function visibleEntities(root) {
  const entities = [];
  root.querySelectorAll("[data-chat-entity][data-chat-entity-id]").forEach((element) => {
    if (entities.length >= 50 || element.closest("northstar-chat")) return;
    let attributes = {};
    try {
      attributes = JSON.parse(element.dataset.chatEntityData || "{}");
    } catch {
      attributes = {};
    }
    entities.push({
      type: element.dataset.chatEntity,
      id: element.dataset.chatEntityId,
      label: element.dataset.chatEntityLabel
        || element.querySelector("h1, h2, h3")?.innerText,
      attributes,
    });
  });
  return cleanEntities(entities);
}

function openPageDialogText() {
  const openDialog = [...document.querySelectorAll("dialog[open]")]
    .find((dialog) => !dialog.closest("northstar-chat"));
  return cleanText(openDialog?.innerText, 3000);
}

export function pageContext() {
  const url = new URL(window.location.href);
  const hashSection = url.hash.replace(/^#/, "");
  const urlVehicle = url.searchParams.get("vehicle");
  const vehicleId = selectedVehicleId || (VEHICLE_ID.test(urlVehicle || "") ? urlVehicle : null);
  const section = integrationSection
    || visibleSection()
    || (SECTION.test(hashSection) ? hashSection : "home");
  const path = `${url.pathname}${vehicleId ? `?vehicle=${encodeURIComponent(vehicleId)}` : ""}${
    section !== "home" ? `#${section}` : ""
  }`;
  const root = contextRoot(section);
  const main = document.querySelector("main") || document.body;
  const visibleText = cleanText(root.innerText, 4000);
  const pageOverview = root === main ? null : cleanText(main.innerText, 1600);
  // Structured context covers the loaded host page, not only whichever section
  // happens to occupy the largest part of the viewport at send time.
  const controls = integrationControls?.length ? integrationControls : visibleControlState(main);
  const entities = integrationEntities?.length ? integrationEntities : visibleEntities(main);
  const pageText = cleanText(
    [
      visibleText ? `Current section: ${visibleText}` : null,
      controls.length
        ? `Current page choices: ${controls.map(({ label, value }) => `${label}: ${value}`).join("; ")}`
        : null,
      pageOverview ? `Page overview: ${pageOverview}` : null,
    ]
      .filter(Boolean)
      .join(" "),
    6000,
  );
  return {
    path,
    section,
    vehicleId,
    title: cleanText(document.title, 120) || "Northstar Motors",
    heading: cleanText(root.querySelector("h1, h2")?.innerText, 200),
    description: cleanText(document.querySelector('meta[name="description"]')?.content, 500),
    pageText,
    dialogText: openPageDialogText(),
    controls,
    entities,
  };
}
