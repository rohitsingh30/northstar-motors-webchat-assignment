import { textElement } from "../core/dom.js";

export function messageContent(message, text) {
  if (message.role !== "assistant" || !text) return textElement("p", "", text);
  const normalized = String(text).replace(/\s+[-•]\s+/g, "\n- ").trim();
  const lines = normalized.split(/\r?\n/);
  const hasExplicitList = lines.some((line) => /^\s*[-•]\s+/.test(line));
  if (!hasExplicitList && lines.length > 1) {
    const content = document.createElement("div");
    content.className = "webchat-assistant-content";
    lines.filter((line) => line.trim()).forEach((line) => {
      content.append(textElement("p", "", line.trim()));
    });
    return content;
  }
  if (!hasExplicitList) return textElement("p", "", text);

  const content = document.createElement("div");
  content.className = "webchat-assistant-content";
  let list = null;
  lines.forEach((line) => {
    const match = line.match(/^\s*[-•]\s+(.+)$/);
    if (match) {
      if (!list) {
        list = document.createElement("ul");
        list.className = "webchat-message-list";
        content.append(list);
      }
      list.append(textElement("li", "", match[1].trim()));
      return;
    }
    list = null;
    if (line.trim()) content.append(textElement("p", "", line.trim()));
  });
  return content;
}
