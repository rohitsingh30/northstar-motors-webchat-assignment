import { textElement } from "../core/dom.js?v=20260904.2";
import { suggestionChips } from "./suggestions.js?v=20260905.2";

function fallbackPresentation(items, purpose) {
  const displayItems = Array.isArray(items)
    ? items
      .map((item) => ({
        label: typeof item?.name === "string" ? item.name.trim() : "",
        description: typeof item?.description === "string" ? item.description.trim() : "",
      }))
      .filter((item) => item.label)
    : [];
  return {
    schemaVersion: 1,
    layout: "bullet_list",
    purpose,
    items: displayItems,
  };
}

export function collectionPresentation(view = {}) {
  const declared = view?.collectionPresentation;
  if (
    declared?.schemaVersion === 1
    && ["bullet_list", "chip_grid"].includes(declared.layout)
    && ["information", "choice", "clarification"].includes(declared.purpose)
    && Array.isArray(declared.items)
  ) {
    // Entity records are information first. Old persisted rich-chip payloads are projected to
    // the same semantic list as new producers; only closed workflow scalar values remain replies.
    const compactWorkflowChoice = view?.choiceEntityType === "workflow_option";
    if (
      declared.layout === "chip_grid"
      && (
        !compactWorkflowChoice
        || declared.items.some(
          (item) => typeof item?.description === "string" && item.description.trim(),
        )
      )
    ) return { ...declared, layout: "bullet_list" };
    return declared;
  }
  return fallbackPresentation(
    view?.items,
    view?.selectionOnly === true ? "choice" : "information",
  );
}

export function structuredCollectionList(view = {}) {
  const presentation = collectionPresentation(view);
  const items = presentation.items
    .filter((item) => typeof item?.label === "string" && item.label.trim())
    .slice(0, 20);
  if (!items.length) return null;

  if (presentation.layout === "chip_grid") {
    const group = suggestionChips(
      items.map((item) => ({
        label: item.label.trim(),
        text: typeof item.message === "string" && item.message.trim()
          ? item.message.trim()
          : item.label.trim(),
      })),
      { allowMany: true, className: "webchat-collection-replies" },
    );
    group.dataset.collectionPurpose = presentation.purpose;
    group.setAttribute("aria-label", "Available choices");
    return group;
  }

  const list = document.createElement("ul");
  list.className = "webchat-collection-list";
  list.dataset.collectionPurpose = presentation.purpose;
  items.forEach((item) => {
    const row = document.createElement("li");
    row.append(textElement("strong", "webchat-collection-label", item.label.trim()));
    const description = typeof item.description === "string" ? item.description.trim() : "";
    if (description) {
      row.append(textElement("span", "webchat-collection-description", ` — ${description}`));
    }
    list.append(row);
  });
  return list;
}
