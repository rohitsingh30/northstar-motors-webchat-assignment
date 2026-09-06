import assert from "node:assert/strict";
import test from "node:test";

import {
  initialWidgetState,
  reduceWidgetState,
} from "../../webchat/widget/core/widget-state.js";

test("widget lifecycle is explicit across busy, unread, and host modal restoration", () => {
  let state = reduceWidgetState(initialWidgetState(), { type: "OPEN" });
  state = reduceWidgetState(state, { type: "REQUEST_STARTED" });
  assert.equal(state.request, "busy");
  state = reduceWidgetState(state, { type: "REQUEST_SUCCEEDED" });
  state = reduceWidgetState(state, { type: "HOST_MODAL_OPENED", interactionId: "interaction-1" });
  assert.equal(state.visibility, "hidden_for_host");
  state = reduceWidgetState(state, { type: "MESSAGE_RECEIVED", count: 2 });
  assert.equal(state.unread, 2);
  assert.equal(
    reduceWidgetState(state, { type: "HOST_MODAL_FINISHED", interactionId: "stale" }).visibility,
    "hidden_for_host",
  );
  state = reduceWidgetState(state, { type: "HOST_MODAL_FINISHED", interactionId: "interaction-1" });
  assert.equal(state.visibility, "restored");
  state = reduceWidgetState(state, { type: "RESTORE_COMPLETED" });
  assert.equal(state.visibility, "open");
});

test("unavailable and error states fail closed", () => {
  let state = reduceWidgetState(initialWidgetState(), { type: "UNAVAILABLE", code: "offline" });
  state = reduceWidgetState(state, { type: "OPEN" });
  assert.equal(state.visibility, "closed");
  assert.equal(state.availability, "unavailable");
  state = reduceWidgetState(state, { type: "AVAILABLE" });
  state = reduceWidgetState(state, { type: "REQUEST_STARTED" });
  state = reduceWidgetState(state, { type: "REQUEST_FAILED", code: "timeout" });
  assert.equal(state.request, "idle");
  assert.equal(state.lastError, "timeout");
});
