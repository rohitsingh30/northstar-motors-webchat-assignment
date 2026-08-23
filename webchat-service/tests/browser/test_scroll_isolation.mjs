import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const widgetSource = await readFile(
  new URL("../../webchat/widget/northstar-chat-widget.js", import.meta.url),
  "utf8",
);
const controllerSource = await readFile(
  new URL("../../webchat/widget/webchat.js", import.meta.url),
  "utf8",
);
const styleSource = await readFile(
  new URL("../../webchat/widget/webchat.css", import.meta.url),
  "utf8",
);

test("chat scrolling stays inside the transcript without reflowing the host page", () => {
  assert.doesNotMatch(widgetSource, /margin-right|data-northstar-chat-open/);
  assert.doesNotMatch(controllerSource, /scrollIntoView/);
  assert.match(
    styleSource,
    /\.webchat-transcript[^}]*overscroll-behavior:\s*contain[^}]*scrollbar-gutter:\s*stable/,
  );
});
