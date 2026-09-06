// Keep all browser traffic behind the stable public webchat API.
export function createChatApi(apiBase = "/api/chat/v1") {
  const root = apiBase.replace(/\/$/, "");
  const activeRequests = new Set();
  const replacementPath = (path, options = {}) => options.replacesDraftId
    ? `${path}?replacesDraftId=${encodeURIComponent(options.replacesDraftId)}`
    : path;

  async function request(path, options = {}) {
    const controller = new AbortController();
    activeRequests.add(controller);
    try {
      const response = await fetch(`${root}${path}`, {
        ...options,
        signal: controller.signal,
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
      return response.status === 204 ? null : await response.json();
    } finally {
      activeRequests.delete(controller);
    }
  }

  return {
    cancelPendingRequests: () => {
      activeRequests.forEach((controller) => controller.abort());
      activeRequests.clear();
    },
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
    cancelDraft: (conversationId, draftId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draftId)}/cancel`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
    supersedeDraft: (conversationId, draftId) => request(
      `/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draftId)}/supersede`,
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
    prepareTestDrive: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/test-drive-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareWorkshopBooking: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/workshop-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    preparePartExchange: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/part-exchange-drafts`, options),
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
    prepareCallback: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/callback-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareSalesEnquiry: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/sales-enquiry-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareVehicleInterest: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/vehicle-interest-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
    prepareDealershipMessage: (conversationId, details, options) => request(
      replacementPath(`/conversations/${encodeURIComponent(conversationId)}/dealership-message-drafts`, options),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(details),
      },
    ),
  };
}
