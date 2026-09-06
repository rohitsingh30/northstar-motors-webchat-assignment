import { expect, test } from "@playwright/test";
import { resetDealershipFixture } from "./support/test-environment.mjs";

test.beforeEach(async ({ request }) => {
  await resetDealershipFixture(request);
});

function settledInput(page) {
  const input = page.locator("northstar-chat #webchat-input");
  return new Proxy(input, {
    get(target, property, receiver) {
      if (property === "press") {
        return async (key, options) => {
          if (key !== "Enter") return target.press(key, options);
          const send = page.locator("northstar-chat #webchat-send");
          const assistants = page.locator("northstar-chat .webchat-message--assistant");
          await expect(send).toBeEnabled({ timeout: 60_000 });
          const before = await assistants.count();
          await target.press(key, options);
          await expect.poll(() => assistants.count(), { timeout: 60_000 })
            .toBeGreaterThan(before);
          await expect(send).toBeEnabled({ timeout: 60_000 });
        };
      }
      const value = Reflect.get(target, property, receiver);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

test("launcher opens a conversational composer without data-entry forms", async ({ page }, testInfo) => {
  await page.goto("/");
  const launcher = page.locator("northstar-chat #webchat-launcher");
  const panel = page.locator("northstar-chat #webchat-panel");
  await expect(launcher).toBeVisible();
  await expect(panel).not.toBeVisible();
  await launcher.click();
  await expect(panel).toBeVisible();
  await expect(page.locator("northstar-chat #webchat-history-panel")).toBeVisible();
  await expect(page.locator("northstar-chat #webchat-history-toggle")).toHaveCount(0);
  await expect(page.locator("northstar-chat #webchat-input")).toBeEditable();
  await expect(page.locator("northstar-chat form form, northstar-chat [data-workflow-details]"))
    .toHaveCount(0);
  const bounds = await panel.boundingBox();
  if (testInfo.project.name.startsWith("desktop")) {
    expect(Math.abs(bounds.width - 350)).toBeLessThanOrEqual(1);
    expect(Math.abs(bounds.x + bounds.width - page.viewportSize().width)).toBeLessThanOrEqual(1);
    expect(Math.abs(bounds.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(bounds.height - page.viewportSize().height)).toBeLessThanOrEqual(1);
    await expect(page.locator("body")).toHaveCSS("margin-right", "350px");
  } else {
    expect(Math.abs(bounds.width - page.viewportSize().width)).toBeLessThanOrEqual(1);
    expect(Math.abs(bounds.height - page.viewportSize().height)).toBeLessThanOrEqual(1);
    await expect(page.locator("body")).toHaveCSS("margin-right", "0px");
  }
  await page.locator("northstar-chat #webchat-close").click();
  await expect(panel).not.toBeVisible();
  await expect(page.locator("body")).toHaveCSS("margin-right", "0px");
});

test("New chat cancels an in-flight turn and restores the recent-chat surface", async ({ page }) => {
  let releaseTurn;
  let markTurnStarted;
  const turnStarted = new Promise((resolve) => { markTurnStarted = resolve; });
  await page.route("**/api/chat/v1/conversations/*/turns", async (route) => {
    markTurnStarted();
    await new Promise((resolve) => { releaseTurn = resolve; });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: 1,
        turnId: "late-turn",
        status: "completed",
        messages: [{ role: "assistant", text: "Late response from cancelled turn" }],
        cards: [],
        quickReplies: [],
        workflow: null,
        pendingInteraction: null,
        clientActions: [],
        error: null,
      }),
    }).catch(() => null);
  });

  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const history = page.locator("northstar-chat #webchat-history-panel");
  const input = page.locator("northstar-chat #webchat-input");
  const newChat = page.locator("northstar-chat #webchat-new");
  await expect(history).toBeVisible();

  await input.fill("Keep this request pending");
  await input.press("Enter");
  await turnStarted;
  await expect(history).not.toBeVisible();
  await expect(newChat).toBeEnabled();
  await newChat.click();

  await expect(history).toBeVisible();
  await expect(page.locator("northstar-chat #webchat-transcript")).not.toContainText(
    "Keep this request pending",
  );
  releaseTurn();
  await page.waitForTimeout(200);
  await expect(page.locator("northstar-chat #webchat-transcript")).not.toContainText(
    "Late response from cancelled turn",
  );
});

test("REAL AI: Enter sends while Shift+Enter creates a draft newline", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const send = page.locator("northstar-chat #webchat-send");
  await expect(send).toBeEnabled({ timeout: 45_000 });
  await input.fill("Hello");
  await input.press("Shift+Enter");
  await input.type("Northstar");
  await expect(input).toHaveValue("Hello\nNorthstar");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-message--user").last()).toContainText("Hello");
  await expect(input).toBeEditable();
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toBeVisible({
    timeout: 20_000,
  });
  await expect(send).toBeEnabled({ timeout: 45_000 });
});

test("REAL AI: vehicle results expose more matches and open details in the host modal", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Show me cars under £35,000");
  await input.press("Enter");

  const vehicleAssistant = page.locator("northstar-chat .webchat-message--assistant")
    .filter({ has: page.locator(".webchat-vehicle-card") }).last();
  await expect(vehicleAssistant.locator(".webchat-vehicle-card")).toHaveCount(3, {
    timeout: 45_000,
  });
  await expect(page.locator("northstar-chat").getByRole("button", { name: "Show me more" }))
    .toBeVisible();
  const viewButtons = vehicleAssistant.getByRole("button", { name: /^View .* details$/ });
  await expect(viewButtons).toHaveCount(3);
  await page.locator("northstar-chat").getByRole("button", { name: "Compare these vehicles" })
    .click();
  await expect(page.locator("northstar-chat .webchat-message--user").last()).toHaveText(
    "compare the vehicles currently shown",
  );
  await expect(page.locator("northstar-chat .webchat-comparison-table").last()).toBeVisible({
    timeout: 45_000,
  });
  const comparisonAssistant = page.locator("northstar-chat .webchat-message--assistant")
    .filter({ has: page.locator(".webchat-comparison-table") }).last();
  await expect(comparisonAssistant.locator(":scope > .webchat-assistant-bubble")).toHaveCount(1);
  await expect(comparisonAssistant.locator(".webchat-message-list li")).toHaveCount(0);
  const followUpAssistant = page.locator("northstar-chat .webchat-message--assistant").last();
  await expect(followUpAssistant).not.toContainText("The main trade-offs are:");
  await expect(followUpAssistant.locator(".webchat-comparison-table")).toHaveCount(0);
  await expect(followUpAssistant.locator(".webchat-suggestion")).toHaveCount(4);

  const assistantCount = await page.locator("northstar-chat .webchat-message--assistant").count();
  await input.fill("Extra space matters most.");
  await input.press("Enter");
  await expect.poll(
    () => page.locator("northstar-chat .webchat-message--assistant").count(),
    { timeout: 45_000 },
  ).toBeGreaterThan(assistantCount);
  await expect(page.locator("northstar-chat .webchat-comparison-table")).toHaveCount(1);

  await viewButtons.first().click();
  await expect(page.locator("#vehicle-dialog")).toBeVisible();
  await expect(page.locator("northstar-chat #webchat-panel")).not.toBeVisible();
  await page.locator("#close-dialog").click();
  await expect(page.locator("northstar-chat #webchat-panel")).toBeVisible();
});

test("REAL AI: booking lookup asks for secure verification details one at a time", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const send = page.locator("northstar-chat #webchat-send");
  await expect(send).toBeEnabled({ timeout: 20_000 });

  await input.fill("Find an existing booking");
  await input.press("Enter");

  const transcript = page.locator("northstar-chat #webchat-transcript");
  await expect(transcript).toContainText("What’s your booking reference?", {
    timeout: 45_000,
  });
  const requested = transcript.locator(".webchat-message--assistant").last()
    .locator(".webchat-message-list li");
  await expect(requested).toHaveCount(0);
  await expect(transcript).not.toContainText("Enter all four booking details");
  await expect(transcript).not.toContainText("What is the booking reference?");
  await expect(page.locator("northstar-chat #webchat-privacy-helper")).toBeVisible();

  await input.fill("WORK-10001");
  await input.press("Enter");
  await input.fill("Taylor");
  await input.press("Enter");
  await input.fill("AB12 CDE");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");

  const booking = page.locator("northstar-chat .webchat-workshop-booking-card").last();
  await expect(booking).toBeVisible({ timeout: 45_000 });
  await expect(booking).toContainText("WORK-10001");
  await expect(booking).toContainText(/confirmed/i);
});

test("REAL AI: part exchange starts with one conversational protected question", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });
  await input.fill("I’d like a part-exchange estimate");
  await input.press("Enter");

  const assistant = page.locator("northstar-chat .webchat-message--assistant");
  await expect(assistant.last()).toContainText("What’s your vehicle registration?", {
    timeout: 45_000,
  });
  const requested = assistant.last().locator(".webchat-message-list li");
  await expect(requested).toHaveCount(0);
  await expect(page.locator("northstar-chat #webchat-transcript")).not.toContainText("registration or VIN");
  await expect(page.locator("northstar-chat #webchat-transcript")).not.toContainText("service history");

  await input.fill("AB12 CDE");
  await input.press("Enter");
  await input.fill("42150");
  await input.press("Enter");
  await input.fill("Good");
  await input.press("Enter");

  const estimate = page.locator("northstar-chat .webchat-part-exchange-estimate").last();
  await expect(estimate).toBeVisible({ timeout: 45_000 });
  await expect(estimate).toContainText("Indicative estimate");
  await expect(estimate).toContainText(/subject to physical inspection/i);

  await input.fill("will you pick my car?");
  await input.press("Enter");
  await expect(assistant.last()).toContainText(/not.*confirmed|do(?:n['’]t| not) have confirmed/i, {
    timeout: 60_000,
  });
  await expect(assistant.last()).toContainText(/contact.*dealership/i);
  await expect(assistant.last()).not.toContainText(/Which dealership would you prefer/i);
  await expect(page.locator("northstar-chat #webchat-privacy-helper")).not.toBeVisible();
});

test("REAL AI: workshop booking withholds appointment details until final confirmation", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Book a workshop appointment");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-service-card")).toHaveCount(0);
  await expect(page.locator(
    "northstar-chat .webchat-assistant-bubble .webchat-collection-list li",
  )).toHaveCount(8, {
    timeout: 45_000,
  });
  await expect(page.locator("northstar-chat .webchat-message-list li")).toHaveCount(0);
  await expect(page.locator("northstar-chat .webchat-collection-list")).toContainText("MOT");
  await expect(page.locator("northstar-chat .webchat-collection-list")).toContainText(
    "Annual MOT inspection.",
  );
  await expect(page.locator("northstar-chat .webchat-collection-chip")).toHaveCount(0);
  await expect(page.locator("northstar-chat .webchat-collector-replies button")).toHaveCount(0);

  await input.fill("break");
  await input.press("Enter");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 45_000 });
  const locationChoice = page.locator("northstar-chat .webchat-message--assistant")
    .filter({ hasText: "Northstar Liverpool" }).last();
  await expect(locationChoice).toBeVisible({ timeout: 45_000 });
  await expect(locationChoice).not.toContainText("not completely sure");
  await expect(page.locator("northstar-chat .webchat-collector-replies button")).toHaveCount(0);

  await input.fill("Liverpool");
  await input.press("Enter");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 45_000 });

  await input.fill("the earliest available appointment");
  await input.press("Enter");
  const appointmentChoices = page.locator(
    "northstar-chat .webchat-appointment-replies .webchat-suggestion",
  );
  await expect(appointmentChoices.first()).toBeVisible({ timeout: 45_000 });
  expect(await appointmentChoices.count()).toBeLessThanOrEqual(4);
  await expect(page.locator("northstar-chat .webchat-selected-appointment")).toHaveCount(0);
  await appointmentChoices.first().click();
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "What’s your vehicle registration?",
    { timeout: 45_000 },
  );
  await expect(page.locator("northstar-chat .webchat-selected-appointment")).toHaveCount(0);

  await input.fill("AB12 CDE");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "current mileage",
  );
  await input.fill("25000");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "first and last name",
  );
  await input.fill("Alex Morgan");
  await input.press("Enter");
  await input.fill("alex.morgan@example.com");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");

  const confirmation = page.locator("northstar-chat .webchat-confirmation").last();
  await expect(confirmation).toContainText("Review your appointment", { timeout: 45_000 });
  await expect(confirmation).toContainText("AB12 CDE");
  await page.locator("northstar-chat .webchat-confirmation-replies")
    .last().getByRole("button", { name: /^Confirm/i }).click();
  const receipt = page.locator("northstar-chat .webchat-receipt-card").last();
  await expect(receipt).toBeVisible({ timeout: 60_000 });
  await expect(receipt).toContainText(/confirmed|booked/i);
});

test("REAL AI: displayed vehicle references continue into a test drive", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Show me cars under £35,000");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-vehicle-card")).toHaveCount(3, {
    timeout: 45_000,
  });

  await input.fill("book a test drive for mini");
  await input.press("Enter");
  const transcript = page.locator("northstar-chat #webchat-transcript");
  await expect(transcript).toContainText("MINI Countryman", { timeout: 45_000 });
  await expect(transcript).toContainText(/what day|day or date/i);
  await expect(transcript).not.toContainText("not completely sure");

  await input.fill("Tuesday at about 10am");
  await input.press("Enter");
  const appointments = page.locator(
    "northstar-chat .webchat-appointment-replies .webchat-suggestion",
  );
  await expect(appointments.first()).toBeVisible({ timeout: 60_000 });
  await appointments.first().click();
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "first and last name",
    { timeout: 60_000 },
  );
  await input.fill("Alex Morgan");
  await input.press("Enter");
  await input.fill("alex.morgan@example.com");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");

  const confirmation = page.locator("northstar-chat .webchat-confirmation").last();
  await expect(confirmation).toContainText("Review your test drive", { timeout: 45_000 });
  await page.locator("northstar-chat .webchat-confirmation-replies")
    .last().getByRole("button", { name: /^Confirm/i }).click();
  await expect(page.locator("northstar-chat .webchat-receipt-card").last()).toContainText(
    "Test drive booked",
    { timeout: 60_000 },
  );

  await input.fill("Can you tell me about any offers on this vehicle?");
  await input.press("Enter");
  const offerResponse = page.locator("northstar-chat .webchat-message--assistant").last();
  await expect(offerResponse).not.toContainText(/Which .* vehicle|not completely sure/i);
  await expect(page.locator("northstar-chat .webchat-offer-card").last()).toContainText(
    /MINI Countryman/i,
    { timeout: 60_000 },
  );
});

test("REAL AI: compound outcomes retain the displayed vehicle reference", async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Show me cars under £35,000");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-vehicle-card")).toHaveCount(3, {
    timeout: 45_000,
  });

  await input.fill("Book a test drive for the MINI and show me current PCH offers");
  await input.press("Enter");
  const transcript = page.locator("northstar-chat #webchat-transcript");
  await expect(transcript).toContainText("MINI Countryman", { timeout: 60_000 });
  await expect(transcript).toContainText("PCH", { timeout: 60_000 });
  await expect(transcript).not.toContainText("not completely sure");
});

test("REAL AI: callback department choices continue", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const transcript = page.locator("northstar-chat #webchat-transcript");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Please have a dealership call me");
  await input.press("Enter");
  await expect(transcript).toContainText("Northstar Bolton", { timeout: 45_000 });

  await input.fill("bolton");
  await input.press("Enter");
  await expect(transcript).toContainText("Parts", { timeout: 45_000 });

  await input.fill("Parts");
  await input.press("Enter");
  await expect(transcript).toContainText(/reason|what.*discuss|help.*with/i, { timeout: 45_000 });
  await expect(transcript).not.toContainText("not completely sure");

  await input.fill("I need help finding a replacement wing mirror");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "first and last name",
    { timeout: 60_000 },
  );
  await input.fill("Alex Morgan");
  await input.press("Enter");
  await input.fill("alex.morgan@example.com");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-confirmation").last()).toContainText(
    /callback/i,
    { timeout: 45_000 },
  );
  await page.locator("northstar-chat .webchat-confirmation-replies")
    .last().getByRole("button", { name: /^Confirm/i }).click();
  await expect(page.locator("northstar-chat .webchat-receipt-card").last()).toContainText(
    "Callback requested",
    { timeout: 60_000 },
  );
});

test("REAL AI: contextual callback handoff retains the originating customer question", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Do you pick up my car?");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/contact.*dealership/i, { timeout: 60_000 });

  await input.fill("yes");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/Request a callback|Send a message/i, {
    timeout: 60_000,
  });

  await input.fill("Please have a dealership call me");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Northstar Stockport", { timeout: 60_000 });

  await input.fill("Stockport");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/first and last name/i, { timeout: 60_000 });
  await expect(assistants.last()).not.toContainText(/Which team do you need/i);
  await expect(page.locator("northstar-chat #webchat-transcript")).not.toContainText(
    /not completely sure|internal response error/i,
  );
});

test("REAL AI: a non-selection keeps the complete required workflow choice", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Please have a dealership call me");
  await input.press("Enter");
  await input.fill("Stockport");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/Sales.*Service.*Parts/is, { timeout: 60_000 });

  await input.fill("any team");
  await input.press("Enter");
  const repeatedChoice = assistants.last();
  await expect(repeatedChoice).toContainText(/Sales.*Service.*Parts/is, { timeout: 60_000 });
  await expect(repeatedChoice.getByRole("button")).toHaveCount(3);
  await expect(repeatedChoice).not.toContainText(/not completely sure|internal response error/i);
});

test("REAL AI: a dealership message accepts every public choice and reaches a receipt", async ({ page }) => {
  test.setTimeout(360_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("I want to leave a message for a dealership");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Northstar Bolton", { timeout: 60_000 });
  await input.fill("Bolton");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Parts", { timeout: 60_000 });
  await input.fill("Parts");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/subject|about/i, { timeout: 60_000 });
  await input.fill("Replacement wing mirror availability");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/message|tell/i, { timeout: 60_000 });
  await input.fill("Please let me know whether you can supply one for a 2022 MINI Cooper");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/Email|Phone/, { timeout: 60_000 });
  await input.fill("Email");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("first and last name", { timeout: 60_000 });
  await input.fill("Alex Morgan");
  await input.press("Enter");
  await input.fill("alex.morgan@example.com");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-confirmation").last()).toContainText(
    "Dealership message",
    { timeout: 45_000 },
  );
  await page.locator("northstar-chat .webchat-confirmation-replies")
    .last().getByRole("button", { name: /^Confirm/i }).click();
  await expect(page.locator("northstar-chat .webchat-receipt-card").last()).toContainText(
    "Message submitted",
    { timeout: 60_000 },
  );
});

test("REAL AI: a finance sales enquiry reaches protected review and submission", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("I want to make a finance sales enquiry");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Northstar Bolton", { timeout: 60_000 });
  await input.fill("Bolton");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/message|question|ask/i, { timeout: 60_000 });
  await input.fill("Please explain the deposit options on your current PCP offers");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("first and last name", { timeout: 60_000 });
  await input.fill("Alex Morgan");
  await input.press("Enter");
  await input.fill("alex.morgan@example.com");
  await input.press("Enter");
  await input.fill("07700 900123");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-confirmation").last()).toContainText(
    "Dealership enquiry",
    { timeout: 45_000 },
  );
  await page.locator("northstar-chat .webchat-confirmation-replies")
    .last().getByRole("button", { name: /^Confirm/i }).click();
  await expect(page.locator("northstar-chat .webchat-receipt-card").last()).toContainText(
    "Sales enquiry submitted",
    { timeout: 60_000 },
  );
});

test("REAL AI: a workflow can switch away and later resume its exact open question", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const transcript = page.locator("northstar-chat #webchat-transcript");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Book a workshop appointment");
  await input.press("Enter");
  await expect(transcript).toContainText("Brake inspection", { timeout: 45_000 });

  await input.fill("Actually, please have a dealership call me instead");
  await input.press("Enter");
  await expect(transcript).toContainText("Northstar Bolton", { timeout: 45_000 });

  await input.fill("Go back to my workshop booking");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-message--assistant").last()).toContainText(
    "Brake inspection",
    { timeout: 45_000 },
  );

  await input.fill("break");
  await input.press("Enter");
  await expect(transcript).toContainText("Northstar Liverpool", { timeout: 45_000 });
  await expect(transcript).not.toContainText("not completely sure");
});

test("REAL AI: an informational interruption preserves the public workflow question", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Book a workshop appointment");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Brake inspection", { timeout: 45_000 });

  await input.fill("Before that, what time does Northstar Stockport sales open on Monday?");
  await input.press("Enter");
  await expect(page.locator("northstar-chat #webchat-transcript")).toContainText(
    /Monday|09:00|opening/i,
    { timeout: 60_000 },
  );

  await input.fill("MOT");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Liverpool", { timeout: 60_000 });
  await expect(assistants.last()).not.toContainText(/not completely sure|internal response error/i);
});

test("REAL AI: a price question naming an open service choice does not select it", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("Book a workshop appointment");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("Brake inspection", { timeout: 45_000 });

  const beforePrice = await assistants.count();
  await input.fill("what is the cost of mot?");
  await input.press("Enter");
  await expect.poll(() => assistants.count(), { timeout: 90_000 }).toBeGreaterThan(beforePrice);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 90_000 });
  const priceAnswer = (await assistants.allTextContents()).slice(beforePrice).join(" ");
  expect(priceAnswer).toMatch(/MOT/i);
  expect(priceAnswer).toMatch(/£54\.99/);
  expect(priceAnswer).not.toMatch(/don.t have pricing|pricing information.*moment/i);
  expect(priceAnswer).not.toMatch(/Northstar Bolton|Northstar Liverpool|Northstar Manchester/i);

  await input.fill("MOT");
  await input.press("Enter");
  await expect(page.locator("northstar-chat #webchat-transcript")).toContainText(
    "Northstar Liverpool",
    { timeout: 90_000 },
  );
  await expect(assistants.last()).not.toContainText(/not completely sure|internal response error/i);
});

test("REAL AI: an informational interruption can return to a protected collector", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("I’d like a part-exchange estimate");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("vehicle registration", { timeout: 45_000 });

  await input.fill("What time does Northstar Stockport sales open on Monday?");
  await input.press("Enter");
  await expect(assistants.last()).toContainText(/Monday|09:00|opening/i, { timeout: 60_000 });

  await input.fill("Continue my request");
  await input.press("Enter");
  await expect(assistants.last()).toContainText("vehicle registration", { timeout: 20_000 });
  await expect(assistants.last()).not.toContainText(/not completely sure|internal response error/i);
});

test("workshop booking renders each service choice exactly once", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = page.locator("northstar-chat #webchat-input");
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  const services = [
    ["Brake inspection", "Brake condition and performance inspection."],
    ["Diagnostic inspection", "Investigation of a warning light or fault."],
    ["Full service", "Comprehensive annual vehicle service."],
    ["Interim service", "Routine oil and safety inspection."],
    ["MOT", "Annual MOT inspection."],
    ["Manufacturer recall", "Manufacturer recall or quality enhancement."],
    ["Service and MOT", "Combined full service and MOT."],
    ["Tyre fitting", "Tyre replacement and balancing."],
  ];
  await page.route("**/api/chat/v1/conversations/*/turns", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: 1,
        turnId: "11111111-1111-4111-8111-111111111111",
        status: "completed",
        stateVersion: 1,
        assistantMode: "hosted",
        messages: [{
          id: "assistant-service-choice",
          role: "assistant",
          text: "Which workshop service would you like to book?",
          createdAt: new Date().toISOString(),
          purpose: "workflow_prompt",
          viewType: "service_list",
          view: {
            selectionOnly: true,
            collectionPresentation: {
              schemaVersion: 1,
              layout: "chip_grid",
              purpose: "choice",
              items: services.map(([label, description]) => ({ label, description })),
            },
          },
          blocks: [{
            type: "paragraph",
            segments: [{ type: "text", text: "Which workshop service would you like to book?" }],
          }],
        }],
        cards: [],
        quickReplies: [],
        workflow: null,
        pendingInteraction: null,
        clientActions: [],
        error: null,
      }),
    });
  });

  await input.fill("Book a workshop appointment");
  await input.press("Enter");

  const serviceCollection = page.locator("northstar-chat .webchat-collection-list");
  await expect(serviceCollection.locator("li")).toHaveCount(8, { timeout: 45_000 });
  const serviceTurn = page.locator("northstar-chat .webchat-message--collection-prompt").last();
  const serviceBubbles = serviceTurn.locator(":scope > .webchat-assistant-bubble");
  await expect(serviceBubbles).toHaveCount(2);
  await expect(serviceBubbles.nth(0).locator(".webchat-collection-list li")).toHaveCount(8);
  await expect(serviceBubbles.nth(1)).toContainText(
    "Which workshop service would you like to book?",
  );
  await expect(page.locator("northstar-chat .webchat-message-list li")).toHaveCount(0);
  await expect(serviceCollection).toContainText("MOT");
  await expect(serviceCollection).toContainText("Annual MOT inspection.");
  await expect(page.locator("northstar-chat .webchat-collection-chip")).toHaveCount(0);
});

test("REAL AI: workshop service discovery is a structured bullet list rather than prose", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = settledInput(page);
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  await input.fill("What workshop services do you provide?");
  await input.press("Enter");

  const serviceAssistant = page.locator("northstar-chat .webchat-message--assistant")
    .filter({ has: page.locator(".webchat-collection-list") }).last();
  const serviceItems = serviceAssistant.locator(
    ".webchat-collection-list li, .webchat-message-list li",
  );
  await expect(serviceItems).toHaveCount(8, { timeout: 45_000 });
  await expect(serviceAssistant).toContainText(
    "Brake condition and performance inspection.",
  );
  await expect(serviceAssistant).not.toContainText("inspection.;");
});

test("a draft typed while a failed reply is pending is never overwritten", async ({ page }) => {
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = page.locator("northstar-chat #webchat-input");
  const send = page.locator("northstar-chat #webchat-send");
  await expect(send).toBeEnabled({ timeout: 20_000 });

  await page.route("**/api/chat/v1/conversations/*/turns", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.abort("failed");
  });
  await input.fill("Tell me about current offers");
  await input.press("Enter");
  await expect(send).toBeDisabled();
  await expect(input).toBeEditable();
  await input.fill("My next question stays here");

  await expect(send).toBeEnabled({ timeout: 10_000 });
  await expect(input).toHaveValue("My next question stays here");
  await expect(page.locator("northstar-chat [data-chat-action='retry-turn']")).toBeVisible();
});

test("a failed appointment preparation waits for an explicit retry", async ({ page }) => {
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });

  let attempts = 0;
  await page.route("**/api/chat/v1/conversations/*/workshop-drafts*", async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "RATE_LIMITED",
          message: "Too many chat requests were sent in a short period. Please wait one minute and retry.",
          retryable: true,
        },
      }),
    });
  });

  await page.evaluate(() => {
    const conversationId = localStorage.getItem("northstarConversationId");
    sessionStorage.setItem(`northstarWorkflowConversation:v1:${conversationId}`, JSON.stringify({
      version: 1,
      conversationId,
      kind: "workshop_booking",
      status: "ready",
      currentQuestionGroup: null,
      currentField: null,
      answers: {
        registration: "AB12CDE",
        mileage: 20000,
        fullName: "Alex Example",
        firstName: "Alex",
        lastName: "Example",
        email: "alex@example.com",
        phone: "07700900123",
      },
      context: {
        slotId: "slot-workshop-001",
        serviceTypeId: "service-brakes",
        dealershipId: "northstar-liverpool",
      },
      choices: {},
      corrections: {},
      draftId: null,
      mode: null,
      updatedAt: new Date().toISOString(),
    }));
  });
  await page.reload();
  await page.locator("northstar-chat #webchat-launcher").click();

  const errors = page.locator("northstar-chat [data-request-failure-key^='RATE_LIMITED:']");
  await expect(errors).toHaveCount(1);
  await expect(page.locator("northstar-chat [data-chat-action='retry-workflow']")).toHaveCount(1);
  await page.waitForTimeout(300);
  expect(attempts).toBe(1);

  await page.locator("northstar-chat [data-chat-action='retry-workflow']").click();
  await expect.poll(() => attempts).toBe(2);
  await expect(errors).toHaveCount(1);
});
