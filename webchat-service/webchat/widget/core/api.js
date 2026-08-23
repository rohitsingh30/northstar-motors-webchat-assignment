// Keep all browser traffic behind the stable public webchat API.
export function createChatApi(apiBase = "/api/chat/v1") {
  const root = apiBase.replace(/\/$/, "");

  async function request(path, options = {}) {
    const response = await fetch(`${root}${path}`, {
      ...options,
      credentials: "include",
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      const validationErrors = Array.isArray(problem.detail) ? problem.detail : [];
      const fieldErrors = { ...(problem.error?.fieldErrors || {}) };
      const formErrors = [];
      validationErrors.forEach((item) => {
        const field = item.loc?.at(-1);
        const message = String(item.msg || "Check this field.").replace(/^Value error, /, "");
        if (typeof field === "string" && !["body", "query", "path"].includes(field)) {
          fieldErrors[field] = message;
        } else {
          formErrors.push(message);
        }
      });
      const error = new Error(
        problem.error?.message
          || formErrors[0]
          || (Object.keys(fieldErrors).length ? "Check the highlighted fields." : null)
          || problem.detail
          || "Northstar chat is unavailable.",
      );
      error.status = response.status;
      error.code = problem.error?.code || "CHAT_REQUEST_FAILED";
      error.retryable = problem.error?.retryable === true;
      error.fieldErrors = fieldErrors;
      error.recovery = problem.error?.recovery || {};
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  return {
    listConversations: () => request("/conversations"),
    createConversation: (pageContext) => request("/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pageContext }),
    }),
    restoreConversation: (conversationId) => request(`/conversations/${encodeURIComponent(conversationId)}`),
    sendTurn: (conversationId, clientMessageId, text, pageContext, action) => request(
      `/conversations/${encodeURIComponent(conversationId)}/turns`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientMessageId, text, pageContext, ...(action ? { action } : {}) }),
      },
    ),
    deleteConversation: (conversationId) => request(
      `/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    ),
    confirmDraft: (conversationId, draftId, clientActionId, expectedKind = "") => request(
      `/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draftId)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientActionId, ...(expectedKind ? { expectedKind } : {}) }),
      },
    ),
    cancelDraft: (conversationId, draftId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draftId)}/cancel`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
    lookupWorkshopBooking: (conversationId, proof) => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-booking-lookup`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(proof),
      },
    ),
    prepareExistingWorkshopAction: (conversationId, mode, bookingReference = "") => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-existing-action`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, ...(bookingReference ? { bookingReference } : {}) }),
      },
    ),
    getTestDriveOptions: (conversationId, vehicleId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/test-drive-options`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vehicleId }),
      },
    ),
    prepareTestDrive: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/test-drive-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    getOfferEnquiryOptions: (conversationId, offerId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/offer-enquiry-options`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offerId }),
      },
    ),
    getWorkshopOptions: (conversationId, serviceTypeId, dealershipId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-options`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          serviceTypeId,
          ...(dealershipId ? { dealershipId } : {}),
        }),
      },
    ),
    prepareWorkshopBooking: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    preparePartExchange: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/part-exchange-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    estimatePartExchange: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/part-exchange-estimates`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareCallback: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/callback-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareSalesEnquiry: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/sales-enquiry-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareVehicleInterest: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/vehicle-interest-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareDealershipMessage: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/dealership-message-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareWorkshopAmendment: (conversationId, details) => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-amendment-drafts`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    getWorkshopAmendmentOptions: (conversationId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/workshop-amendment-options`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
  };
}
