import { textElement } from "../core/dom.js?v=20260904.2";
import { moneyFromPence } from "../core/format.js?v=20260904.2";

const WIDGET_SERVICE_ORIGIN = new URL(import.meta.url).origin;

function vehicleSpec(label, value, divider = false) {
  const wrapper = document.createElement("div");
  if (divider) wrapper.className = "webchat-vehicle-spec-divider";
  wrapper.append(textElement("dt", "", label), textElement("dd", "", value));
  return wrapper;
}

export function vehicleCard(item) {
  const card = document.createElement("article");
  card.className = "webchat-vehicle-card";
  card.dataset.vehicleId = item.id || "";

  const media = document.createElement("div");
  media.className = "webchat-vehicle-media";
  if (item.image) {
    const image = document.createElement("img");
    image.src = new URL(item.image, WIDGET_SERVICE_ORIGIN).href;
    image.alt = `${item.make || ""} ${item.model || ""}`.trim();
    image.loading = "lazy";
    media.append(image);
  }
  if (item.availability && item.availability !== "available") {
    media.append(textElement("span", `webchat-availability ${item.availability}`, item.availability));
  }
  if (/^veh-[0-9]{3}$/.test(item.id || "")) {
    const viewButton = textElement("button", "webchat-vehicle-view", "View vehicle");
    viewButton.type = "button";
    viewButton.dataset.vehicleDetail = item.id;
    viewButton.setAttribute(
      "aria-label",
      `View ${[item.make, item.model].filter(Boolean).join(" ") || "vehicle"} details`,
    );
    media.append(viewButton);
  }

  const content = document.createElement("div");
  content.className = "webchat-vehicle-content";
  const identity = document.createElement("div");
  identity.className = "webchat-vehicle-identity";
  if (Number.isInteger(item.optionNumber)) {
    identity.append(textElement("span", "webchat-option-number", `Option ${item.optionNumber}`));
  }
  identity.append(
    textElement(
      "p",
      "webchat-vehicle-meta",
      [item.year, item.dealershipTown || item.dealershipName].filter(Boolean).join(" · "),
    ),
    textElement("h3", "", `${item.make || ""} ${item.model || ""}`.trim()),
    textElement("p", "webchat-vehicle-variant", item.variant),
  );
  const price = document.createElement("div");
  price.className = "webchat-vehicle-price";
  price.append(
    textElement("strong", "", item.price),
    textElement(
      "span",
      "",
      moneyFromPence(item.monthlyPricePence)
        ? `${moneyFromPence(item.monthlyPricePence)} / month`
        : "Speak to our team",
    ),
  );
  const summary = document.createElement("div");
  summary.className = "webchat-vehicle-summary";
  summary.append(identity, price);

  const specs = document.createElement("dl");
  specs.className = "webchat-vehicle-specs";
  specs.append(
    vehicleSpec("Mileage", Number.isInteger(item.mileage) ? `${item.mileage.toLocaleString("en-GB")} mi` : "—"),
    vehicleSpec("Fuel", item.fuelType, true),
    vehicleSpec("Gearbox", item.transmission, true),
  );
  content.append(summary, specs);

  card.append(media, content);
  return card;
}

export function vehicleAvailabilityCard(view) {
  const vehicle = view.vehicle || {};
  const status = String(view.availability || "unknown").toLowerCase();
  const heading = [vehicle.year, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Vehicle";
  const details = [vehicle.variant, vehicle.dealershipTown].filter(Boolean).join(" · ");
  const card = document.createElement("article");
  card.className = "webchat-vehicle-status-card";
  card.append(
    textElement("p", "webchat-flow-eyebrow", "Current vehicle status"),
    textElement("strong", "", heading),
  );
  if (details) card.append(textElement("span", "webchat-vehicle-status-detail", details));
  card.append(
    textElement("span", `webchat-status-pill ${status}`, status === "unknown" ? "Status unavailable" : status),
  );
  const explanation = {
    available: "This vehicle is currently available.",
    reserved: "This vehicle is reserved. You can still register your interest.",
    sold: "This vehicle has been sold and has no published return date. The sales team can help with alternatives.",
  }[status] || "The current vehicle status could not be confirmed.";
  card.append(textElement("p", "", explanation));
  return card;
}

export function comparisonTable(items) {
  const wrap = document.createElement("div");
  wrap.className = "webchat-comparison-table";
  if (!items.length) {
    wrap.append(textElement("p", "webchat-comparison-empty", "I couldn't find two vehicles to compare. Try selecting two vehicles from the current results."));
    return wrap;
  }
  const rows = [
    ["Model", (v) => `${v.make || ""} ${v.model || ""}`.trim()],
    ["Variant", (v) => v.variant || "—"],
    ["Year", (v) => v.year || "—"],
    ["Price", (v) => v.price || "—"],
    ["Monthly", (v) => moneyFromPence(v.monthlyPricePence) ? `${moneyFromPence(v.monthlyPricePence)} / month` : "—"],
    ["Fuel", (v) => v.fuelType || "—"],
    ["Gearbox", (v) => v.transmission || "—"],
    ["Mileage", (v) => v.mileage ? `${v.mileage.toLocaleString()} mi` : "—"],
    ["Body style", (v) => v.bodyStyle || "—"],
    ["Colour", (v) => v.colour || "—"],
    ["Availability", (v) => v.availability || "—"],
    ["Location", (v) => v.dealershipTown || v.dealershipName || "—"],
  ];
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.append(textElement("th", "", " "));
  items.slice(0, 3).forEach((vehicle) => {
    headRow.append(
      textElement("th", "", [vehicle.year, vehicle.model].filter(Boolean).join(" ") || "Vehicle"),
    );
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  rows.forEach(([label, value]) => {
    const row = document.createElement("tr");
    row.append(textElement("th", "", label));
    items.slice(0, 3).forEach((vehicle) => row.append(textElement("td", "", value(vehicle))));
    body.append(row);
  });
  table.append(head, body);
  wrap.append(table);
  return wrap;
}
