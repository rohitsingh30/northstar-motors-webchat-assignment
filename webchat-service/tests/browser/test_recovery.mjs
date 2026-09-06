import assert from "node:assert/strict";
import test from "node:test";

import { createChatApi } from "../../webchat/widget/core/api.js";
import {
  bookingRecoveryKind,
  requestFailureKey,
  retryTurnState,
  turnFailureRecovery,
} from "../../webchat/widget/core/recovery.js";

test("booking recovery distinguishes fresh slots, unavailable vehicles, and fields", () => {
  assert.equal(
    bookingRecoveryKind({ code: "SLOT_UNAVAILABLE" }, "workshop"),
    "slot",
  );
  assert.equal(
    bookingRecoveryKind({ code: "VEHICLE_RESERVED" }, "true"),
    "vehicle",
  );
  assert.equal(
    bookingRecoveryKind({ fieldErrors: { phone: "Check phone" } }, "workshop"),
    "fields",
  );
  assert.equal(
    bookingRecoveryKind({ code: "VEHICLE_UNAVAILABLE" }, "workshop"),
    "none",
  );
});

test("turn retry keeps text and reuses only an indeterminate request id", () => {
  const turn = {
    clientMessageId: "message-1",
    text: "Find an electric SUV",
    action: { type: "next_vehicle_page" },
  };

  assert.deepEqual(retryTurnState(turn, false), {
    ...turn,
    appendUser: false,
  });
  assert.deepEqual(retryTurnState(turn, true), {
    ...turn,
    clientMessageId: null,
    appendUser: true,
  });
});

test("invalid grounded output asks for rephrasing instead of repeating a permanent failure", () => {
  assert.equal(turnFailureRecovery({ retryable: false }), "rephrase");
  assert.equal(turnFailureRecovery({ retryable: true }), "retry");
  assert.equal(turnFailureRecovery(new Error("network")), "retry");
});

test("request failures have a stable key so concurrent errors render once", () => {
  assert.equal(
    requestFailureKey({ code: "RATE_LIMITED", message: "Please wait one minute." }),
    "RATE_LIMITED:Please wait one minute.",
  );
  assert.equal(
    requestFailureKey(new Error("network")),
    "CHAT_REQUEST_FAILED:network",
  );
});

test("the chat API aborts every pending request at the new-conversation boundary", async () => {
  const originalFetch = globalThis.fetch;
  let aborted = false;
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      aborted = true;
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  });

  try {
    const api = createChatApi("/api/chat/v1");
    const pending = api.sendTurn("conversation-1", "message-1", "Hello", {});
    api.cancelPendingRequests();
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(aborted, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
