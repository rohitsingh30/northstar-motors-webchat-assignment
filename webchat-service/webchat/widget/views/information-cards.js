import { textElement } from "../core/dom.js?v=20260904.2";
import { moneyFromPence } from "../core/format.js?v=20260904.2";

// Read-only business results are rendered independently of workflow forms and receipts.

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

export function factCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-card";
  CARD_FIELDS.forEach(([key, label]) => {
    if (value?.[key] === undefined || value[key] === null) return;
    card.append(textElement("strong", "", label));
    card.append(textElement("span", "", String(value[key])));
  });
  return card;
}

export function offerCard(value, { detailed = false } = {}) {
  const card = document.createElement("article");
  card.className = "webchat-card webchat-offer-card";
  const heading = document.createElement("div");
  heading.className = "webchat-offer-heading";
  if (Number.isInteger(value.optionNumber)) {
    heading.append(textElement("span", "webchat-option-number", `Option ${value.optionNumber}`));
  }
  heading.append(textElement("strong", "", value.name || `${value.make || ""} ${value.model || ""}`.trim() || "New-car offer"));
  if (value.productType) heading.append(textElement("span", "webchat-offer-type", value.productType));
  card.append(heading);
  if (Number.isInteger(value.monthlyPricePence)) {
    const priceRow = document.createElement("div");
    priceRow.className = "webchat-offer-price-row";
    priceRow.append(textElement("strong", "webchat-card-price", `${moneyFromPence(value.monthlyPricePence)} / month`));
    card.append(priceRow);
  }
  const terms = [
    ["Upfront payment", value.upfrontPaymentPence ?? value.depositPence],
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
      textElement("dd", "", Number.isInteger(detail) && label === "Upfront payment" ? moneyFromPence(detail) : detail),
    );
  });
  if (details.childElementCount) card.append(details);
  if (detailed && value.description) {
    const description = document.createElement("div");
    description.className = "webchat-offer-description";
    description.append(
      textElement("strong", "", "Offer details"),
      textElement("p", "", value.description),
    );
    card.append(description);
  }
  return card;
}

export function dealershipCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-dealership-card";
  const heading = document.createElement("div");
  heading.className = "webchat-dealership-heading";
  if (Number.isInteger(value.optionNumber)) {
    heading.append(textElement("span", "webchat-option-number", `Option ${value.optionNumber}`));
  }
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

export function openingHoursCard(value) {
  const card = document.createElement("article");
  card.className = "webchat-opening-hours-card";
  const header = document.createElement("header");
  header.className = "webchat-opening-hours-heading";
  header.append(
    textElement("h3", "", value.name || "Northstar location"),
    textElement(
      "span",
      "webchat-opening-location",
      value.holidayOnly ? "Holiday opening hours" : `${value.day || "Current"} hours`,
    ),
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
  if (departments.childElementCount) card.append(departments);
  if (closedPeriods.length) {
    const labels = closedPeriods.map((label) => label === "Sun" ? "Sundays" : label).join(" and ");
    card.append(textElement("p", "webchat-opening-closed-days", `Closed ${labels}.`));
  }
  const holidayExceptions = Array.isArray(value.holidayExceptions) ? value.holidayExceptions : [];
  if (holidayExceptions.length) {
    const holiday = document.createElement("section");
    holiday.className = "webchat-opening-holiday";
    const label = holidayExceptions[0].label || "Holiday exception";
    if (!value.holidayOnly) {
      holiday.append(textElement("span", "webchat-opening-section-label", "Holiday hours"));
    }
    holiday.append(
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

export function titleCaseDisplay(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function dealershipName(view, dealershipId) {
  const dealership = (view.dealerships || []).find(
    (item) => String(item.id) === String(dealershipId),
  );
  return dealership?.name || dealership?.town || "Selected dealership";
}

export function vehicleDisplayName(vehicle) {
  const model = [vehicle?.year, vehicle?.make, vehicle?.model].filter(Boolean).join(" ");
  return [model, vehicle?.variant].filter(Boolean).join(" · ") || "Selected vehicle";
}
