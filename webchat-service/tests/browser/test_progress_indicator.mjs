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

test("pending requests use an indeterminate indicator without a fabricated percentage", () => {
  assert.match(
    widgetSource,
    /<progress id="webchat-progress-bar" max="100" aria-label="Request in progress"><\/progress>/,
  );
  assert.doesNotMatch(widgetSource, /webchat-progress-value/);
  assert.doesNotMatch(controllerSource, /progressTimer|progressValue|\b88\b/);
  assert.match(controllerSource, /progressBar\.removeAttribute\("value"\)/);
  assert.match(styleSource, /progress:indeterminate/);
});
