import assert from "node:assert/strict";
import test from "node:test";

import { createChatApi } from "../../webchat/widget/core/api.js";
import {
  formSubmissionErrorMode,
  workflowValidationMessage,
} from "../../webchat/widget/core/form-submit.js";

test("empty workshop amendments explain that a change must be selected", () => {
  assert.equal(
    workflowValidationMessage("workshop_amend", {}),
    "Enter an updated mileage or notes, or choose a new appointment time.",
  );
  assert.equal(workflowValidationMessage("workshop_amend", { mileage: 42000 }), "");
  assert.equal(workflowValidationMessage("workshop_amend", { notes: "Check brakes" }), "");
});

test("form-level API validation is not mislabeled as a contact-field error", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      detail: [
        {
          loc: ["body"],
          msg: "Value error, Enter a new mileage, notes, or appointment time.",
        },
      ],
    }),
  });

  try {
    await assert.rejects(
      createChatApi().prepareWorkshopAmendment("conversation", {}),
      (error) => {
        assert.equal(
          error.message,
          "Enter a new mileage, notes, or appointment time.",
        );
        assert.deepEqual(error.fieldErrors, {});
        return true;
      },
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("all forms keep customer-correctable failures on the active form", () => {
  assert.equal(formSubmissionErrorMode({ status: 422, retryable: false }, true), "fields");
  assert.equal(formSubmissionErrorMode({ status: 409, retryable: false }, false), "form");
  assert.equal(formSubmissionErrorMode({ status: 500, retryable: true }, false), "request");
});
