import { showFieldErrors, showFormError } from "./form-state.js";
import {
  inlineTestDriveConfirmation,
  inlineWorkshopConfirmation,
  renderWorkflowCard,
  setOfferEnquiryActionState,
  setWorkshopFlowContent,
} from "../views/message.js";

// Form submission is deterministic: each closed form maps to one API preparation operation.
export function workflowValidationMessage(kind, details) {
  if (
    kind === "workshop_amend"
    && details.mileage === undefined
    && !String(details.notes || "").trim()
  ) {
    return "Enter an updated mileage or notes, or choose a new appointment time.";
  }
  return "";
}

export function formSubmissionErrorMode(error, displayedFieldError = false) {
  if (displayedFieldError) return "fields";
  if (error?.status >= 400 && error.status < 500 && !error.retryable) return "form";
  return "request";
}

export function createFormSubmitHandler({
  api,
  getConversationId,
  formProfile,
  setPending,
  clearPending,
  showRequestError,
  appendMessage,
}) {
  function presentSubmissionError(form, submit, error) {
    const displayedFieldError = showFieldErrors(form, error.fieldErrors);
    const mode = formSubmissionErrorMode(error, displayedFieldError);
    if (mode === "fields") {
      clearPending();
    } else if (mode === "form") {
      clearPending();
      if (!showFormError(form, error.message)) showRequestError(error);
    } else {
      showRequestError(error);
    }
    submit.disabled = false;
  }

  return async function handleFormSubmit(event) {
    formProfile.remember(event.target);
    const testDriveForm = event.target.closest("[data-test-drive-details]");
    if (testDriveForm) {
      event.preventDefault();
      const submit = testDriveForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your booking…");
      const flow = testDriveForm.closest("[data-booking-flow]");
      const details = Object.fromEntries(new FormData(testDriveForm));
      flow.contactDetails = details;
      showFieldErrors(testDriveForm);
      try {
        const result = await api.prepareTestDrive(getConversationId(), {
          ...details,
          slotId: flow.selectedSlot.slotId,
          vehicleId: flow.selectedSlot.vehicleId,
        });
        flow.replaceChildren(
          inlineTestDriveConfirmation(
            result.view,
            flow.selectedSlot.slotLabel,
            flow.vehicleLabel,
          ),
        );
        flow.draftId = result.view.draftId;
        clearPending();
      } catch (error) {
        presentSubmissionError(testDriveForm, submit, error);
      }
      return;
    }
    const workshopForm = event.target.closest("[data-workshop-details]");
    if (workshopForm) {
      event.preventDefault();
      const submit = workshopForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your workshop booking…");
      const flow = workshopForm.closest("[data-workshop-flow]");
      const details = Object.fromEntries(new FormData(workshopForm));
      details.mileage = Number(details.mileage);
      if (!details.notes) delete details.notes;
      flow.contactDetails = details;
      showFieldErrors(workshopForm);
      try {
        const result = await api.prepareWorkshopBooking(getConversationId(), {
          ...details,
          slotId: flow.selectedSlot.slotId,
          serviceTypeId: flow.selectedSlot.serviceTypeId,
          dealershipId: flow.selectedSlot.dealershipId,
        });
        setWorkshopFlowContent(
          flow,
          inlineWorkshopConfirmation(result.view, flow.selectedSlot, details),
        );
        flow.draftId = result.view.draftId;
        clearPending();
      } catch (error) {
        presentSubmissionError(workshopForm, submit, error);
      }
      return;
    }
    const partExchangeForm = event.target.closest("[data-part-exchange-details]");
    if (partExchangeForm) {
      event.preventDefault();
      const submit = partExchangeForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your indicative estimate…");
      const details = Object.fromEntries(new FormData(partExchangeForm));
      details.mileage = Number(details.mileage);
      showFieldErrors(partExchangeForm);
      try {
        const result = await api.preparePartExchange(getConversationId(), details);
        partExchangeForm.closest(".webchat-part-exchange-card")?.replaceWith(
          renderWorkflowCard(result.view),
        );
        clearPending();
      } catch (error) {
        presentSubmissionError(partExchangeForm, submit, error);
      }
      return;
    }
    const partExchangeEstimateForm = event.target.closest("[data-part-exchange-estimate]");
    if (partExchangeEstimateForm) {
      event.preventDefault();
      const submit = partExchangeEstimateForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Calculating your indicative estimate…");
      const details = Object.fromEntries(new FormData(partExchangeEstimateForm));
      details.mileage = Number(details.mileage);
      showFieldErrors(partExchangeEstimateForm);
      try {
        const result = await api.estimatePartExchange(getConversationId(), details);
        partExchangeEstimateForm.closest(".webchat-part-exchange-card")?.replaceWith(
          renderWorkflowCard(result.view, result.viewType),
        );
        clearPending();
      } catch (error) {
        presentSubmissionError(partExchangeEstimateForm, submit, error);
      }
      return;
    }
    const callbackForm = event.target.closest("[data-callback-details]");
    if (callbackForm) {
      event.preventDefault();
      const submit = callbackForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your callback request…");
      const details = Object.fromEntries(new FormData(callbackForm));
      showFieldErrors(callbackForm);
      try {
        const result = await api.prepareCallback(getConversationId(), details);
        callbackForm.closest(".webchat-callback-card")?.replaceWith(renderWorkflowCard(result.view));
        clearPending();
      } catch (error) {
        presentSubmissionError(callbackForm, submit, error);
      }
      return;
    }
    const workflowForm = event.target.closest("[data-workflow-details]");
    if (workflowForm) {
      event.preventDefault();
      const submit = workflowForm.querySelector("button[type='submit']");
      submit.disabled = true;
      setPending("Preparing your request…");
      const details = Object.fromEntries(new FormData(workflowForm));
      Object.keys(details).forEach((key) => {
        if (details[key] === "") delete details[key];
      });
      if (details.mileage !== undefined) details.mileage = Number(details.mileage);
      const workflowKind = workflowForm.dataset.workflowDetails;
      const prepare = {
        sales_enquiry: api.prepareSalesEnquiry,
        vehicle_interest: api.prepareVehicleInterest,
        dealership_message: api.prepareDealershipMessage,
        workshop_amend: api.prepareWorkshopAmendment,
      }[workflowKind];
      showFieldErrors(workflowForm);
      const validationMessage = workflowValidationMessage(workflowKind, details);
      if (validationMessage) {
        clearPending();
        showFormError(workflowForm, validationMessage);
        submit.disabled = false;
        return;
      }
      try {
        if (!prepare) throw new Error("This request form is unavailable.");
        const result = await prepare(getConversationId(), details);
        const card = workflowForm.closest(".webchat-workflow-form-card");
        const offerFlow = workflowForm.closest("[data-offer-enquiry-flow]");
        const workshopFlow = workflowForm.closest("[data-workshop-flow]");
        const rendered = renderWorkflowCard(result.view);
        if (workshopFlow && workflowKind === "workshop_amend") {
          setWorkshopFlowContent(workshopFlow, rendered, "child");
        } else {
          card?.replaceWith(rendered);
        }
        if (offerFlow) {
          offerFlow.draftId = result.view.draftId;
          setOfferEnquiryActionState(offerFlow, true);
        }
        clearPending();
      } catch (error) {
        presentSubmissionError(workflowForm, submit, error);
      }
      return;
    }
    const lookupForm = event.target.closest("[data-private-lookup]");
    if (!lookupForm) return;
    event.preventDefault();
    const submit = lookupForm.querySelector("button[type='submit']");
    submit.disabled = true;
    setPending("Checking those booking details…");
    const proof = Object.fromEntries(new FormData(lookupForm));
    showFieldErrors(lookupForm);
    try {
      const result = await api.lookupWorkshopBooking(getConversationId(), proof);
      lookupForm.reset();
      lookupForm.remove();
      appendMessage({
        role: "assistant",
        text: result.text || "Your workshop booking has been verified.",
        viewType: result.viewType,
        view: result.view,
      });
      clearPending();
    } catch (error) {
      presentSubmissionError(lookupForm, submit, error);
    }
  };
}
