const SLOT_ERRORS = new Set(["SLOT_UNAVAILABLE"]);
const VEHICLE_ERRORS = new Set(["VEHICLE_RESERVED", "VEHICLE_UNAVAILABLE"]);

export function bookingRecoveryKind(error, inlineBooking) {
  if (!inlineBooking) return "none";
  if (SLOT_ERRORS.has(error?.code)) return "slot";
  if (inlineBooking === "true" && VEHICLE_ERRORS.has(error?.code)) return "vehicle";
  if (Object.keys(error?.fieldErrors || {}).length) return "fields";
  return "none";
}

export function retryTurnState({ clientMessageId, text, action }, definitiveFailure) {
  return {
    clientMessageId: definitiveFailure ? null : clientMessageId,
    text,
    action,
    appendUser: definitiveFailure,
  };
}
