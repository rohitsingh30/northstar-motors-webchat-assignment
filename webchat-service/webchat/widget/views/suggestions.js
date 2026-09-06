import { textElement } from "../core/dom.js?v=20260904.2";

function validSuggestions(suggestions) {
  return (suggestions || []).filter(
    (suggestion) => suggestion?.label && suggestion?.text,
  );
}

export function suggestionChips(
  suggestions,
  { allowMany = false, balanceOptional = false, className = "" } = {},
) {
  const group = document.createElement("div");
  group.className = `webchat-suggestions ${className}`.trim();
  group.setAttribute("aria-label", "Suggested replies");
  let visibleSuggestions = allowMany
    ? validSuggestions(suggestions)
    : validSuggestions(suggestions).slice(0, 4);
  if (balanceOptional && visibleSuggestions.length === 3) {
    visibleSuggestions = visibleSuggestions.slice(0, 2);
  }
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
