// Keep generic DOM construction within this small, safe boundary. Callers provide
// text, never markup, so model output cannot be interpreted as HTML.
export function textElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text ?? "—";
  return element;
}
