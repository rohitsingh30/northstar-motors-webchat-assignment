import { textElement } from "../core/dom.js";

export function messageContent(message, text) {
  if (message.role !== "assistant" || !text) return textElement("p", "", text);
  const normalized = String(text).replace(/\s+[-•]\s+/g, "\n- ").trim();
  const lines = normalized.split(/\r?\n/);
  const hasExplicitList = lines.some((line) => /^\s*[-•]\s+/.test(line));
  const sentences = String(text)
    .replace(/\s+/g, " ")
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-Z0-9£])/u)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (!hasExplicitList && String(text).length >= 300 && sentences.length >= 4) {
    const content = document.createElement("div");
    content.className = "webchat-assistant-content webchat-long-answer";
    content.append(textElement("p", "webchat-long-answer-intro", sentences[0]));
    const list = document.createElement("ul");
    list.className = "webchat-message-list webchat-long-answer-list";
    sentences.slice(1).forEach((sentence) => list.append(textElement("li", "", sentence)));
    content.append(list);
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
