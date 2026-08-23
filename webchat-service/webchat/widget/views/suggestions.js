import { textElement } from "../core/dom.js";

function balancedFollowUpSuggestions(suggestions) {
  const valid = (suggestions || []).filter(
    (suggestion) => suggestion?.label && suggestion?.text,
  );
  if (valid.length >= 4) return valid.slice(0, 4);
  if (valid.length >= 2) return valid.slice(0, 2);
  return [];
}

export function suggestionChips(suggestions, { allowMany = false, className = "" } = {}) {
  const group = document.createElement("div");
  group.className = `webchat-suggestions ${className}`.trim();
  group.setAttribute("aria-label", "Suggested replies");
  const visibleSuggestions = allowMany
    ? (suggestions || []).filter((suggestion) => suggestion?.label && suggestion?.text).slice(0, 8)
    : balancedFollowUpSuggestions(suggestions);
  visibleSuggestions.forEach((suggestion) => {
    const button = textElement("button", "webchat-suggestion", suggestion.label);
    button.type = "button";
    button.dataset.chatSuggestion = suggestion.text;
    if (suggestion.action?.type) {
      button.dataset.chatSuggestionAction = JSON.stringify(suggestion.action);
    }
    group.append(button);
  });
  return group;
}
