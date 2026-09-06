import { expect, test } from "@playwright/test";
import { resetDealershipFixture } from "./support/test-environment.mjs";

test.beforeEach(async ({ request }) => {
  await resetDealershipFixture(request);
});

const FAILURE_COPY = /not completely sure|internal response error|couldn.t reliably understand|could not be completed|couldn.t present that response clearly/i;
const STRESS_RUNS = Math.max(1, Math.min(3, Number.parseInt(process.env.REAL_AI_STRESS_RUNS || "1", 10) || 1));

async function openChat(page) {
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  await expect(page.locator("northstar-chat #webchat-send")).toBeEnabled({ timeout: 20_000 });
  return {
    input: page.locator("northstar-chat #webchat-input"),
    send: page.locator("northstar-chat #webchat-send"),
    transcript: page.locator("northstar-chat #webchat-transcript"),
  };
}

async function sendAndExpect(page, message, expected) {
  const { input, send } = await openChat(page);
  const assistantMessages = page.locator("northstar-chat .webchat-message--assistant");
  const assistantCount = await assistantMessages.count();
  await input.fill(message);
  await input.press("Enter");
  await expect(send).toBeEnabled({ timeout: 60_000 });
  for (const value of expected) {
    await expect.poll(
      async () => (await assistantMessages.allTextContents()).slice(assistantCount).join(" "),
      { timeout: 60_000 },
    ).toMatch(value);
  }
  await expect.poll(
    async () => (await assistantMessages.allTextContents()).slice(assistantCount).join(" "),
  ).not.toMatch(FAILURE_COPY);
}

test("PRODUCT BRIEF real AI: a large deictic vehicle reference gets a useful clarification", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await expect(page.locator("#vehicle-grid .vehicle-card")).toHaveCount(12, { timeout: 30_000 });
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = page.locator("northstar-chat #webchat-input");
  const send = page.locator("northstar-chat #webchat-send");
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(send).toBeEnabled({ timeout: 20_000 });

  const before = await assistants.count();
  await input.fill("Can I get finance information for this vehicle?");
  await input.press("Enter");
  await expect.poll(() => assistants.count(), { timeout: 60_000 }).toBeGreaterThan(before);
  await expect(send).toBeEnabled({ timeout: 60_000 });
  const clarification = assistants.last();
  await expect(clarification).toContainText(/which vehicle|make and model|identify the vehicle/i);
  await expect(clarification).not.toContainText(FAILURE_COPY);
  await expect(clarification).not.toContainText(/Option 1|Option 12/i);

  const beforeAnswer = await assistants.count();
  await input.fill("the 2025 Blazing Blue MINI Countryman");
  await input.press("Enter");
  await expect.poll(() => assistants.count(), { timeout: 60_000 }).toBeGreaterThan(beforeAnswer);
  await expect(send).toBeEnabled({ timeout: 60_000 });
  const answer = (await assistants.allTextContents()).slice(beforeAnswer).join(" ");
  expect(answer).toMatch(/finance|subject to status|credit/i);
  expect(answer).not.toMatch(FAILURE_COPY);
});

test("PRODUCT BRIEF real AI: a service answer never refers to nonexistent options", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = page.locator("northstar-chat #webchat-input");
  const send = page.locator("northstar-chat #webchat-send");
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(send).toBeEnabled({ timeout: 20_000 });

  const before = await assistants.count();
  await input.fill("What is tyre cost?");
  await input.press("Enter");
  await expect(send).toBeEnabled({ timeout: 60_000 });
  const response = (await assistants.allTextContents()).slice(before).join(" ");
  expect(response).toMatch(/Tyre fitting/i);
  expect(response).toMatch(/90 minutes/i);
  expect(response).not.toMatch(/Which of these options/i);
  expect(response).not.toMatch(FAILURE_COPY);
});

const readScenarios = [
  {
    requirement: "natural categorical vehicle search",
    messages: [
      "Find me black cars",
      "Show me vehicles in a black colour",
      "What black-coloured cars are available?",
    ],
    expected: [/Black/i],
  },
  {
    requirement: "vehicle availability and reserved status",
    messages: [
      "Is the 2026 Jaguar F-PACE in Corris Grey available?",
      "Can I still buy that Corris Grey 2026 Jaguar F-PACE?",
      "Check the stock status of the grey 2026 F-PACE for me",
    ],
    expected: [/Jaguar F-PACE/i, /reserved/i],
  },
  {
    requirement: "vehicle sold status",
    messages: [
      "Is the 2025 BMW 1 Series in Fire Red available?",
      "Can I buy the Fire Red 2025 BMW 1 Series?",
      "Check whether the 2025 red BMW 1 Series is still for sale",
    ],
    expected: [/BMW 1 Series/i, /sold/i],
  },
  {
    requirement: "vehicle available status",
    messages: [
      "Is the 2025 MINI Countryman in Blazing Blue available?",
      "Can I buy the blue 2025 MINI Countryman?",
      "Check whether the 2025 Blazing Blue Countryman is in stock",
    ],
    expected: [/MINI Countryman/i, /available/i],
  },
  {
    requirement: "published new-car offers without invented terms",
    messages: [
      "Show me the current PCH offers",
      "What Personal Contract Hire deals are live now?",
      "I want to see Northstar's published lease offers",
    ],
    expected: [/PCH/i, /subject to status/i],
  },
  {
    requirement: "finance notice",
    messages: [
      "What is PCP and what qualification applies?",
      "Explain Personal Contract Purchase and any eligibility warning",
      "How does PCP work, and is finance guaranteed?",
    ],
    expected: [/Personal Contract Purchase/i, /subject to status/i],
  },
  {
    requirement: "matched workshop service",
    messages: [
      "Do you offer tyre fitting?",
      "Can Northstar replace and balance my tyres?",
      "Is getting new tyres fitted a supported workshop service?",
    ],
    expected: [/Tyre fitting/i],
  },
  {
    requirement: "ambiguous workshop service",
    messages: [
      "Do you offer a service?",
      "What does a car service cost?",
      "How long does a vehicle service take?",
    ],
    expected: [/Full service/i, /Interim service/i, /which|choose|mean/i],
  },
  {
    requirement: "unavailable workshop service",
    messages: [
      "Do you offer car cleaning as a workshop service?",
      "Can I book vehicle valeting with the workshop?",
      "Is interior detailing in your service catalogue?",
    ],
    expected: [
      /couldn.t (?:find|confirm)|not (?:currently )?available|unavailable|unsupported/i,
      /supported workshop services|workshop services.*supported|service catalogue/i,
    ],
  },
  {
    requirement: "dealership contact, departments, regular and holiday hours",
    messages: [
      "Show Northstar Stockport's address, departments, and sales opening hours including holiday exceptions",
      "Where is the Stockport branch, which teams are there, and when is sales open on the bank holiday?",
      "Give me Stockport contact details, departments, normal sales hours, and special holiday hours",
    ],
    expected: [/24 Wellington Road/i, /Sales/i, /Bank holiday/i],
  },
  {
    requirement: "privacy notice",
    messages: [
      "How will Northstar use my personal information if I submit an enquiry?",
      "What is the privacy notice for details I enter in chat?",
      "Tell me how my contact data is handled",
    ],
    expected: [/privacy|personal information|personal data/i],
  },
  {
    requirement: "part-exchange qualification",
    messages: [
      "Is an online part-exchange estimate guaranteed?",
      "Will the part-exchange valuation definitely be the price I receive?",
      "What conditions apply to the online trade-in estimate?",
    ],
    expected: [/indicative|not.*guaranteed/i, /inspection/i],
  },
];

for (const scenario of readScenarios) {
  for (const [index, message] of scenario.messages.slice(0, STRESS_RUNS).entries()) {
    test(`PRODUCT BRIEF real AI: ${scenario.requirement} [variant ${index + 1}]`, async ({ page }) => {
      test.setTimeout(90_000);
      await sendAndExpect(page, message, scenario.expected);
    });
  }
}

const workflowEntryScenarios = [
  {
    requirement: "general sales enquiry",
    messages: [
      "I have a general question for your sales team",
      "I want to send a general sales enquiry",
      "Could a salesperson answer a general question for me?",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "availability sales enquiry",
    messages: [
      "I want to make an availability sales enquiry",
      "Send sales a question about whether a car is available",
      "I need to ask the sales team about vehicle availability",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "finance sales enquiry",
    messages: [
      "I want to make a finance sales enquiry",
      "Please send the sales team a question about finance",
      "I need to enquire about funding a car purchase",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "part-exchange sales enquiry",
    messages: [
      "I want to make a sales enquiry about part exchange",
      "Please send the sales team a trade-in question",
      "I need to ask sales about part-exchanging my car",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "interest registration for a reserved vehicle",
    messages: [
      "Register my interest in the 2026 Jaguar F-PACE in Corris Grey",
      "The grey 2026 F-PACE is reserved; let me leave my interest",
      "Tell Northstar I am interested in that reserved Corris Grey Jaguar F-PACE",
    ],
    expected: [/first.*last name/i],
  },
  {
    requirement: "new workshop booking",
    messages: [
      "Book a workshop appointment",
      "I need to arrange a service visit",
      "Help me schedule my car into the workshop",
    ],
    expected: [/Brake inspection/i, /Which.*service/i],
  },
  {
    requirement: "existing workshop booking retrieval",
    messages: [
      "Find my existing workshop booking",
      "I want to check the status of my service appointment",
      "Look up my workshop reservation",
    ],
    expected: [/booking reference/i],
  },
  {
    requirement: "workshop booking amendment",
    messages: [
      "I need to amend an existing workshop booking",
      "Change my workshop appointment",
      "I want to reschedule a service booking I already made",
    ],
    expected: [/booking reference/i],
  },
  {
    requirement: "workshop booking cancellation",
    messages: [
      "I need to cancel an existing workshop booking",
      "Cancel my service appointment",
      "I no longer want my workshop reservation",
    ],
    expected: [/booking reference/i],
  },
  {
    requirement: "dealership message",
    messages: [
      "I want to leave a message for a dealership",
      "Can I send the parts team at a branch a message?",
      "Help me contact a dealership in writing",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "sales callback",
    messages: [
      "Please have a dealership call me",
      "Ask someone at Northstar to phone me",
      "I'd like a callback from a branch",
    ],
    expected: [/Northstar Bolton/i],
  },
  {
    requirement: "indicative part-exchange estimate",
    messages: [
      "I’d like a part-exchange estimate",
      "Value my current car for trade-in",
      "What might Northstar offer in part exchange for my vehicle?",
    ],
    expected: [/vehicle registration/i],
  },
];

for (const scenario of workflowEntryScenarios) {
  for (const [index, message] of scenario.messages.slice(0, STRESS_RUNS).entries()) {
    test(`PRODUCT BRIEF real AI: ${scenario.requirement} [variant ${index + 1}]`, async ({ page }) => {
      test.setTimeout(90_000);
      await sendAndExpect(page, message, scenario.expected);
    });
  }
}

test("PRODUCT BRIEF real AI: natural stock refinement retains conversation state", async ({ page }) => {
  test.setTimeout(150_000);
  const { input, send, transcript } = await openChat(page);
  await input.fill("Show me SUVs under £35,000");
  await input.press("Enter");
  await expect(page.locator("northstar-chat .webchat-vehicle-card")).toHaveCount(3, {
    timeout: 60_000,
  });
  await expect(send).toBeEnabled({ timeout: 60_000 });
  await input.fill("Only MINIs, and raise the budget to £40,000");
  await input.press("Enter");
  await expect(send).toBeEnabled({ timeout: 60_000 });
  await expect(page.locator("northstar-chat .webchat-vehicle-card").last()).toContainText("MINI");
  await expect(transcript).not.toContainText(FAILURE_COPY);
});
