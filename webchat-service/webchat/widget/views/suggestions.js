import { textElement } from "../core/dom.js";

function validSuggestions(suggestions) {
  return (suggestions || []).filter(
    (suggestion) => suggestion?.label && suggestion?.text,
  );
}

function balancedSuggestions(suggestions) {
  const valid = validSuggestions(suggestions);
  return valid.length >= 4 ? valid.slice(0, 4) : valid.slice(0, 2);
}

export function suggestionChips(suggestions, { allowMany = false, className = "" } = {}) {
  const group = document.createElement("div");
  group.className = `webchat-suggestions ${className}`.trim();
  group.setAttribute("aria-label", "Suggested replies");
  const visibleSuggestions = allowMany
    ? validSuggestions(suggestions)
    : balancedSuggestions(suggestions);
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
