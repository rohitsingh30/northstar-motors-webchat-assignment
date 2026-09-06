import { textElement } from "../core/dom.js?v=20260904.2";

function trustedHref(segment) {
  const href = typeof segment?.href === "string" ? segment.href.trim() : "";
  const kind = segment?.destinationKind;
  if (kind === "telephone" && /^tel:[^\s]+$/i.test(href)) return href;
  if (kind === "email" && /^mailto:[^\s]+$/i.test(href)) return href;
  if (kind !== "directions" && kind !== "website") return null;
  if (/^\/(?!\/)/.test(href)) return href;
  try {
    const parsed = new URL(href);
    if (parsed.protocol === "https:") return href;
    if (
      parsed.protocol === "http:"
      && ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)
    ) return href;
  } catch {
    return null;
  }
  return null;
}

function validatedInlineSegments(segments) {
  if (!Array.isArray(segments) || !segments.length) return null;
  const validated = segments.map((segment) => {
    if (segment?.type === "text" && typeof segment.text === "string" && segment.text) return segment;
    if (
      segment?.type === "emphasis"
      && typeof segment.text === "string"
      && segment.text
    ) return segment;
    if (
      segment?.type === "fact"
      && typeof segment.factId === "string"
      && /^fact-[A-Za-z0-9_.:-]{1,160}$/.test(segment.factId)
      && typeof segment.text === "string"
      && segment.text
    ) return segment;
    if (segment?.type === "link" && typeof segment.label === "string" && segment.label) {
      const href = trustedHref(segment);
      return href ? { ...segment, href } : null;
    }
    return null;
  });
  if (validated.some((segment) => !segment)) return null;
  return validated;
}

function legacyBlocks(segments) {
  if (!Array.isArray(segments) || !segments.length) return null;
  const blocks = [];
  let paragraph = [];
  let items = [];
  let currentItem = null;
  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", segments: paragraph });
    paragraph = [];
  };
  const flushList = () => {
    if (currentItem?.length) items.push({ segments: currentItem });
    currentItem = null;
    if (items.length) blocks.push({ type: "list", items });
    items = [];
  };
  segments.forEach((segment) => {
    if (segment?.type === "bullet") {
      flushParagraph();
      if (currentItem?.length) items.push({ segments: currentItem });
      currentItem = [];
    } else if (currentItem !== null) currentItem.push(segment);
    else paragraph.push(segment);
  });
  flushParagraph();
  flushList();
  return blocks;
}

function validatedBlocks(message) {
  const source = Array.isArray(message.blocks) && message.blocks.length
    ? message.blocks
    : legacyBlocks(message.segments);
  if (!Array.isArray(source) || !source.length) return null;
  const blocks = source.map((block) => {
    if (block?.type === "paragraph") {
      const segments = validatedInlineSegments(block.segments);
      return segments ? { type: "paragraph", segments } : null;
    }
    if (block?.type !== "list" || !Array.isArray(block.items) || !block.items.length) return null;
    const items = block.items.map((item) => {
      const segments = validatedInlineSegments(item?.segments);
      return segments ? { segments } : null;
    });
    return items.some((item) => !item) ? null : { type: "list", items };
  });
  return blocks.some((block) => !block) ? null : blocks;
}

function appendInline(target, segments) {
  segments.forEach((segment) => {
    if (segment.type === "text") {
      target.append(textElement("span", "", segment.text));
      return;
    }
    if (segment.type === "emphasis") {
      target.append(textElement("strong", "webchat-inline-emphasis", segment.text));
      return;
    }
    if (segment.type === "fact") {
      const fact = textElement("strong", "webchat-inline-fact", segment.text);
      fact.dataset.factId = segment.factId;
      target.append(fact);
      return;
    }
    const link = textElement("a", "webchat-inline-link", segment.label);
    link.href = segment.href;
    link.setAttribute("href", segment.href);
    if (/^https?:/i.test(segment.href)) link.setAttribute("rel", "noopener noreferrer");
    target.append(link);
  });
}

function structuredContent(message) {
  if (message.role !== "assistant") return null;
  const blocks = validatedBlocks(message);
  if (!blocks) return null;
  if (blocks.length === 1 && blocks[0].type === "paragraph") {
    const paragraph = document.createElement("p");
    paragraph.className = "webchat-message-segments";
    appendInline(paragraph, blocks[0].segments);
    return paragraph;
  }
  const root = document.createElement("div");
  root.className = "webchat-assistant-content";
  blocks.forEach((block) => {
    if (block.type === "paragraph") {
      const paragraph = document.createElement("p");
      appendInline(paragraph, block.segments);
      root.append(paragraph);
      return;
    }
    const list = document.createElement("ul");
    list.className = "webchat-message-list";
    block.items.forEach((item) => {
      const listItem = document.createElement("li");
      appendInline(listItem, item.segments);
      list.append(listItem);
    });
    root.append(list);
  });
  return root;
}

export function messageContent(message, text) {
  const structured = structuredContent(message);
  if (structured) return structured;
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
