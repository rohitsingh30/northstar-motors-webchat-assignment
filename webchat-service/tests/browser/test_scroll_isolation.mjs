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

test("desktop chat reserves its host column while scrolling stays inside the transcript", () => {
  assert.match(
    widgetSource,
    /html\[data-northstar-chat-open\] body\s*\{\s*margin-right:\s*350px/,
  );
  assert.match(widgetSource, /onOpen: \(\) => document\.documentElement\.setAttribute\("data-northstar-chat-open"/);
  assert.match(widgetSource, /onClose: \(\) => document\.documentElement\.removeAttribute\("data-northstar-chat-open"/);
  assert.doesNotMatch(controllerSource, /scrollIntoView/);
  assert.match(
    styleSource,
    /\.webchat-transcript[^}]*overscroll-behavior:\s*contain[^}]*scrollbar-gutter:\s*stable/,
  );
});

test("desktop chat remains a full-height right rail", () => {
  assert.match(
    styleSource,
    /\.webchat-panel\s*\{[^}]*inset:\s*0 0 0 auto;[^}]*width:\s*min\(350px, 100%\);[^}]*height:\s*100dvh;[^}]*border-left:\s*1px solid #dde3e1;[^}]*border-radius:\s*0;/,
  );
  assert.doesNotMatch(styleSource, /height:\s*min\(720px|border-radius:\s*22px/);
});
