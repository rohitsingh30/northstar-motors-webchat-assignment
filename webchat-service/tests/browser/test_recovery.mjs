import assert from "node:assert/strict";
import test from "node:test";

import {
  bookingRecoveryKind,
  retryTurnState,
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
