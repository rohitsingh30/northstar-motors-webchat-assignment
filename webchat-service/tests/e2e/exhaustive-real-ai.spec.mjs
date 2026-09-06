import { expect, test } from "@playwright/test";
import { resetDealershipFixture } from "./support/test-environment.mjs";

test.beforeEach(async ({ request }) => {
  await resetDealershipFixture(request);
});

const FAILURE_COPY = /not completely sure|internal response error|couldn.t reliably understand|could not be completed|couldn.t present that response clearly|failed to fetch/i;
const DEAD_END_SCHEDULE = /what (?:other|different) day|share another preferred day|another approximate time|what different day/i;
const SERVICES = [
  "break",
  "Diagnostic inspection",
  "Full service",
  "Interim service",
  "MOT",
  "Manufacturer recall",
  "Service and MOT",
  "Tyre fitting",
];
const DEALERSHIPS = ["Bolton", "Liverpool", "Manchester", "Stockport"];
const DEPARTMENTS = ["Sales", "Service", "Parts"];
const MESSAGE_DEPARTMENTS = [...DEPARTMENTS, "General enquiries"];
const VEHICLE_MODELS = [
  "BMW 1 Series",
  "BMW 3 Series",
  "BMW X3",
  "BMW i4",
  "MINI Cooper",
  "MINI Countryman",
  "Jaguar F-PACE",
  "Land Rover Range Rover Evoque",
  "Land Rover Discovery Sport",
  "Volvo XC40",
  "Volvo V60",
  "Kia Sportage",
];

async function openChat(page) {
  await page.goto("/");
  await page.locator("northstar-chat #webchat-launcher").click();
  const input = page.locator("northstar-chat #webchat-input");
  const send = page.locator("northstar-chat #webchat-send");
  const assistants = page.locator("northstar-chat .webchat-message--assistant");
  await expect(send).toBeEnabled({ timeout: 20_000 });
  return { input, send, assistants };
}

async function turn(chat, message) {
  const before = await chat.assistants.count();
  await chat.input.fill(message);
  await chat.input.press("Enter");
  await expect.poll(() => chat.assistants.count(), { timeout: 60_000 }).toBeGreaterThan(before);
  await expect(chat.send).toBeEnabled({ timeout: 60_000 });
  const response = chat.assistants.last();
  await expect(response).not.toContainText(FAILURE_COPY);
  return response;
}

async function assistantTurnMessages(page, response) {
  const turnId = await response.getAttribute("data-turn-id");
  if (!turnId) {
    return response;
  }
  return page.locator(
    `northstar-chat .webchat-message--assistant[data-turn-id="${turnId}"]`,
  );
}

async function assistantTurnText(page, response) {
  const messages = await assistantTurnMessages(page, response);
  return (await messages.allTextContents()).join(" ");
}

async function assistantTurnChoiceLabels(page, response) {
  const messages = await assistantTurnMessages(page, response);
  return messages.locator(".webchat-collection-list li strong").allTextContents();
}

test.describe("PRODUCT BRIEF real AI: exhaustive appointment matrix", () => {
  test.skip(process.env.REAL_AI_EXHAUSTIVE !== "1", "Runs only in the real-AI release gate");

  for (const [serviceIndex, service] of SERVICES.entries()) {
    for (const [locationIndex, location] of DEALERSHIPS.entries()) {
      test(`workshop: ${service} × ${location} survives an unavailable preference`, async ({ page }) => {
        test.setTimeout(300_000);
        const chat = await openChat(page);
        await turn(chat, "Book a workshop appointment");
        const serviceResponse = await turn(chat, service);
        await expect(serviceResponse).toContainText(/Bolton|Liverpool|Manchester|Stockport/i);
        const locationResponse = await turn(chat, `Use the workshop in ${location}`);
        await expect(locationResponse).toContainText(/day|date|morning|afternoon/i);

        const appointments = page.locator(
          "northstar-chat .webchat-appointment-replies .webchat-suggestion",
        );
        const beforeAppointments = await appointments.count();
        const period = (serviceIndex + locationIndex) % 2 === 0 ? "mrongin" : "afternon";
        const scheduleResponse = await turn(chat, `tomorrow ${period}`);
        await expect.poll(() => appointments.count(), { timeout: 60_000 })
          .toBeGreaterThan(beforeAppointments);
        await expect(scheduleResponse).not.toContainText(DEAD_END_SCHEDULE);
      });
    }
  }

  for (const [index, model] of VEHICLE_MODELS.entries()) {
    test(`test drive: ${model} returns a concrete choice after preference miss`, async ({ page }) => {
      test.setTimeout(240_000);
      const chat = await openChat(page);
      let vehicleResponse = await turn(chat, `Book a test drive for the ${model}`);
      const responseText = await vehicleResponse.innerText();
      if (/which .+ would you like|which available vehicle/i.test(responseText)) {
        // A model name can identify several stock records. Follow the application's legitimate
        // finite-reference branch through the real semantic resolver instead of assuming the
        // inventory has one listing or allowing the test to choose a hidden vehicle ID.
        vehicleResponse = await turn(chat, "the first one");
      }
      await expect(vehicleResponse).toContainText(/day|date|morning|afternoon/i);

      const appointments = page.locator(
        "northstar-chat .webchat-appointment-replies .webchat-suggestion",
      );
      const beforeAppointments = await appointments.count();
      const period = index % 2 === 0 ? "mornign" : "afternon";
      const scheduleResponse = await turn(chat, `tomorrow ${period}`);
      await expect.poll(() => appointments.count(), { timeout: 60_000 })
        .toBeGreaterThan(beforeAppointments);
      await expect(scheduleResponse).not.toContainText(DEAD_END_SCHEDULE);
    });
  }
});

test.describe("PRODUCT BRIEF real AI: exhaustive finite-choice matrix", () => {
  test.skip(process.env.REAL_AI_EXHAUSTIVE !== "1", "Runs only in the real-AI release gate");

  const callbackPairs = [
    ...DEALERSHIPS.map((dealership) => ({ dealership, department: "Sales" })),
    ...DEPARTMENTS.slice(1).map((department) => ({ dealership: "Bolton", department })),
  ];
  const messagePairs = [
    ...DEALERSHIPS.map((dealership) => ({ dealership, department: "Sales" })),
    ...MESSAGE_DEPARTMENTS.slice(1).map((department) => ({ dealership: "Bolton", department })),
  ];

  for (const { dealership, department } of callbackPairs) {
    test(`callback accepts ${dealership} → ${department}`, async ({ page }) => {
      test.setTimeout(240_000);
      const chat = await openChat(page);
      await turn(chat, "Please have a dealership call me");
      const departmentChoice = await turn(chat, dealership);
      await expect(departmentChoice).toContainText(/Sales|Service|Parts/i);
      const reasonQuestion = await turn(chat, department);
      await expect(reasonQuestion).toContainText(/reason|discuss|help.*with|calling about/i);
    });

  }

  for (const { dealership, department } of messagePairs) {
    test(`dealership message accepts ${dealership} → ${department}`, async ({ page }) => {
      test.setTimeout(240_000);
      const chat = await openChat(page);
      await turn(chat, "I want to leave a message for a dealership");
      const departmentChoice = await turn(chat, dealership);
      await expect(departmentChoice).toContainText(/Sales|Service|Parts/i);
      const subjectQuestion = await turn(chat, department);
      await expect(subjectQuestion).toContainText(/subject|about|message/i);
    });
  }

  for (const condition of ["Excellent", "Good", "Fair"]) {
    test(`part-exchange estimate accepts ${condition} condition`, async ({ page }) => {
      test.setTimeout(300_000);
      const chat = await openChat(page);
      await turn(chat, "I want a part-exchange estimate");
      await turn(chat, "AB12 CDE");
      await turn(chat, "24000 miles");
      const estimate = await turn(chat, condition);
      await expect(estimate).toContainText(/Indicative estimate|£/i);
      await expect(estimate).toContainText(/inspection|indicative/i);
    });
  }

  for (const contactMethod of ["Email", "Phone"]) {
    test(`dealership message accepts ${contactMethod} reply preference`, async ({ page }) => {
      test.setTimeout(360_000);
      const chat = await openChat(page);
      await turn(chat, "I want to leave a message for a dealership");
      await turn(chat, "Bolton");
      await turn(chat, "Parts");
      await turn(chat, "Replacement part availability");
      await turn(chat, "Please tell me whether you stock a replacement wing mirror");
      const protectedQuestion = await turn(chat, contactMethod);
      await expect(protectedQuestion).toContainText(/first and last name/i);
    });
  }

  test("vehicle disambiguation accepts a natural ordinal reply", async ({ page }) => {
    test.setTimeout(240_000);
    const chat = await openChat(page);
    const choices = await turn(chat, "Book a test drive for a MINI Cooper in Manchester");
    const choicesText = await assistantTurnText(page, choices);
    expect(choicesText).toMatch(/Option 1/i);
    expect(choicesText).toMatch(/Option 2/i);
    expect(choicesText).toMatch(/Option 3/i);

    const continued = await turn(chat, "show me option 2");
    const continuedText = await assistantTurnText(page, continued);
    expect(continuedText).toMatch(/day|date|morning|afternoon|evening/i);
    expect(continuedText).not.toMatch(/Which MINI Cooper/i);
  });

  test("paginated vehicle results produce one unique executable choice order", async ({ page }) => {
    test.setTimeout(300_000);
    const chat = await openChat(page);
    await turn(chat, "I need a petrol automatic SUV with low mileage");
    await turn(chat, "show me more");
    const choices = await turn(chat, "book a test drive for mini");
    const labels = await assistantTurnChoiceLabels(page, choices);

    expect(labels).toHaveLength(4);
    expect(labels.map((label) => label.match(/^Option (\d+)/)?.[1])).toEqual([
      "1", "2", "3", "4",
    ]);
    expect(labels).toEqual([
      expect.stringMatching(/Option 1.*2023 MINI Countryman/i),
      expect.stringMatching(/Option 2.*2026 MINI Countryman/i),
      expect.stringMatching(/Option 3.*2025 MINI Countryman/i),
      expect.stringMatching(/Option 4.*2021 MINI Countryman/i),
    ]);
    const continued = await turn(chat, "option 4");
    const continuedText = await assistantTurnText(page, continued);
    expect(continuedText).not.toMatch(/Which MINI/i);
    expect(continuedText).toMatch(/2021.*MINI.*Countryman/i);
    expect(continuedText).toMatch(/day|date|morning|afternoon|evening/i);
  });

  test("pagination refreshes an already-open test-drive vehicle choice", async ({ page }) => {
    test.setTimeout(360_000);
    const chat = await openChat(page);
    await turn(chat, "Show me cars under £35,000");
    const originalChoices = await turn(chat, "book a test drive for one of these vehicles");
    await expect(originalChoices).toContainText(/BMW 3 Series|MINI Countryman/i);

    await turn(chat, "show me more");
    await expect(page.locator("northstar-chat .webchat-vehicle-card").last())
      .toContainText(/MINI Cooper/i);

    const continued = await turn(chat, "book test drive for MINI Cooper");
    await expect(continued).toContainText(/day|date|morning|afternoon|evening/i);
    await expect(continued).not.toContainText(/Which vehicle would you like/i);
    await expect(continued).not.toContainText(/BMW 3 Series.*Countryman/i);
  });

  test("an explicit vehicle replacement cannot reuse the stale test-drive target", async ({ page }) => {
    test.setTimeout(420_000);
    const chat = await openChat(page);
    await turn(chat, "show me black cars");
    await turn(chat, "book a test drive for mini");
    await turn(chat, "mini countryman");

    const interruption = await turn(chat, "any mini available at manchester?");
    const interruptionText = await assistantTurnText(page, interruption);
    expect(interruptionText).toMatch(/MINI Cooper/i);
    expect(interruptionText).not.toMatch(/day|date|morning|afternoon|evening/i);

    const replacement = await turn(chat, "book appointment for mini cooper in manchester");
    const replacementText = await assistantTurnText(page, replacement);
    expect(replacementText).toMatch(/MINI Cooper/i);
    expect(replacementText).not.toMatch(/different currently available MINI model/i);
    expect(replacementText).not.toMatch(/MINI Countryman.*Stockport/i);

    const afterChoice = await turn(chat, "show me option 2");
    await expect(afterChoice).not.toContainText(/Which MINI Cooper/i);
    const appointmentReplies = page.locator(
      "northstar-chat .webchat-appointment-replies .webchat-suggestion",
    );
    if (await appointmentReplies.count() === 0) {
      await expect(afterChoice).toContainText(/day|date|morning|afternoon|evening/i);
      await turn(chat, "monday afternoon");
    }
    await expect(appointmentReplies.first()).toBeVisible({ timeout: 60_000 });
  });

  test("a new vehicle candidate branch can continue into a replacement test drive", async ({ page }) => {
    test.setTimeout(420_000);
    const chat = await openChat(page);
    let selected = await turn(chat, "Book a test drive for a MINI Countryman");
    if (/which mini countryman/i.test(await selected.innerText())) {
      selected = await turn(chat, "option 1");
    }
    await expect(selected).toContainText(/day|date|morning|afternoon|evening/i);

    const volvos = await turn(chat, "Show me Volvo SUVs instead");
    const volvosText = await assistantTurnText(page, volvos);
    expect(volvosText).toMatch(/Volvo XC40/i);
    expect(volvosText).not.toMatch(/day|date|morning|afternoon|evening/i);

    const moreVolvos = await turn(chat, "show me more");
    const moreVolvosText = await assistantTurnText(page, moreVolvos);
    expect(moreVolvosText).toMatch(/Volvo XC40/i);
    expect(moreVolvosText).not.toMatch(/MINI Countryman.*day|day.*MINI Countryman/i);

    const replacement = await turn(chat, "book a test drive for option 1");
    const replacementText = await assistantTurnText(page, replacement);
    expect(replacementText).toMatch(/Volvo.*XC40/i);
    expect(replacementText).toMatch(/day|date|morning|afternoon|evening/i);
    expect(replacementText).not.toMatch(/MINI Countryman/i);
  });

  test("a customer can return from a vehicle candidate branch to the paused test drive", async ({ page }) => {
    test.setTimeout(420_000);
    const chat = await openChat(page);
    let selected = await turn(chat, "Book a test drive for a MINI Countryman");
    if (/which mini countryman/i.test(await selected.innerText())) {
      selected = await turn(chat, "option 1");
    }
    await expect(selected).toContainText(/day|date|morning|afternoon|evening/i);

    const volvos = await turn(chat, "What Volvo SUVs are available?");
    const volvosText = await assistantTurnText(page, volvos);
    expect(volvosText).toMatch(/Volvo XC40/i);
    expect(volvosText).not.toMatch(/day|date|morning|afternoon|evening/i);

    const resumed = await turn(chat, "Go back to my MINI test drive");
    await expect(resumed).toContainText(/day|date|morning|afternoon|evening/i);
    await expect(resumed).toContainText(/MINI/i);
    await expect(resumed).not.toContainText(/Volvo XC40/i);
    const slots = await turn(chat, "Monday morning");
    await expect(slots).toContainText(/MINI Countryman/i);
    await expect(slots).not.toContainText(/Volvo XC40/i);
  });

  test("retained vehicle filters stay explicit through make and location steering", async ({ page }) => {
    test.setTimeout(420_000);
    const chat = await openChat(page);
    await turn(chat, "show me black cars");
    const selected = await turn(chat, "book a test drive for volvo");
    await expect(selected).toContainText(/day|date|morning|afternoon|evening/i);

    const volvos = await turn(chat, "do you have volvo in manchester?");
    const volvosText = await assistantTurnText(page, volvos);
    expect(volvosText).toMatch(/Volvo/i);
    expect(volvosText).toMatch(/Manchester/i);
    // There are no Volvos in Manchester in the fixture. A fresh search legitimately clears black;
    // a refinement retains it. In either case the required grounded summary exposes the true scope.
    expect(volvosText).toMatch(/Make:\s*Volvo/i);
    expect(volvosText).toMatch(/Location:\s*Manchester/i);

    const bmws = await turn(chat, "any bmw in manchester");
    const bmwsText = await assistantTurnText(page, bmws);
    expect(bmwsText).toMatch(/BMW/i);
    expect(bmwsText).toMatch(/Manchester/i);
    if (/couldn.t find|no .+ vehicles/i.test(bmwsText)) {
      // Broad BMW stock exists in Manchester, so an empty result is legitimate only when the
      // original black constraint was retained and explicitly disclosed.
      expect(bmwsText).toMatch(/black/i);
    } else {
      await expect(page.locator("northstar-chat .webchat-vehicle-card").last())
        .toContainText(/BMW/i);
    }
  });
});

test.describe("PRODUCT BRIEF real AI: exhaustive steering matrix", () => {
  test.skip(process.env.REAL_AI_EXHAUSTIVE !== "1", "Runs only in the real-AI release gate");

  const workflows = [
    { name: "workshop", start: "Book a workshop appointment", resume: /Brake inspection/i },
    { name: "callback", start: "Please have a dealership call me", resume: /Northstar Bolton/i },
    { name: "message", start: "Leave a message for a dealership", resume: /Northstar Bolton/i },
    { name: "part exchange", start: "I want a part-exchange estimate", resume: /registration/i },
    { name: "booking lookup", start: "Find my workshop booking", resume: /booking reference/i },
    { name: "booking amendment", start: "Amend my workshop booking", resume: /booking reference/i },
    { name: "booking cancellation", start: "Cancel my workshop booking", resume: /booking reference/i },
    { name: "sales enquiry", start: "I have a general question for sales", resume: /Northstar Bolton/i },
    { name: "test drive", start: "Book a test drive", resume: /vehicle|BMW|MINI/i },
    {
      name: "reserved interest",
      start: "Register my interest in the 2026 Jaguar F-PACE in Corris Grey",
      resume: /first and last name/i,
    },
  ];

  for (const workflow of workflows) {
    test(`${workflow.name} survives information interruption and explicit resume`, async ({ page }) => {
      test.setTimeout(240_000);
      const chat = await openChat(page);
      const openingQuestion = await turn(chat, workflow.start);
      await expect(openingQuestion).toContainText(workflow.resume);
      const information = await turn(
        chat,
        "Before that, what time does Northstar Stockport sales open on Monday?",
      );
      await expect(information).toContainText(/Monday|09:00|opening/i);
      await expect(information).not.toContainText(workflow.resume);
      const resumed = await turn(chat, "Continue my previous request");
      await expect(resumed).toContainText(workflow.resume);
    });

    test(`${workflow.name} can switch away and resume its exact question`, async ({ page }) => {
      test.setTimeout(300_000);
      const chat = await openChat(page);
      const openingQuestion = await turn(chat, workflow.start);
      await expect(openingQuestion).toContainText(workflow.resume);
      const switchRequest = workflow.name === "workshop"
        ? "Actually, ask a Northstar dealership to call me instead"
        : "Actually, start a workshop booking instead";
      const switched = await turn(chat, switchRequest);
      await expect(switched).toContainText(
        workflow.name === "workshop" ? /Northstar Bolton/i : /Brake inspection/i,
      );
      const resumed = await turn(chat, `Go back to my ${workflow.name} request`);
      await expect(resumed).toContainText(workflow.resume);
    });
  }
});

test.describe("PRODUCT BRIEF real AI: exhaustive compound-intent matrix", () => {
  test.skip(process.env.REAL_AI_EXHAUSTIVE !== "1", "Runs only in the real-AI release gate");

  const compounds = [
    {
      request: "Show me black cars and the current PCH offers",
      expected: [/Black/i, /PCH/i],
    },
    {
      request: "Is the 2025 MINI Countryman in Blazing Blue available, and book its test drive",
      expected: [/MINI Countryman/i, /day|date|morning|afternoon/i],
    },
    {
      request: "Tell me whether tyre fitting is supported and ask the service team to call me",
      expected: [/Tyre fitting/i, /Northstar Bolton/i],
    },
    {
      request: "Explain PCP eligibility and start a finance sales enquiry",
      expected: [/subject to status/i, /Northstar Bolton/i],
    },
    {
      request: "Give me Stockport sales opening hours and show available MINI cars",
      expected: [/09:00|opening/i, /MINI/i],
    },
  ];

  for (const [index, compound] of compounds.entries()) {
    test(`compound ${index + 1} retains every requested outcome`, async ({ page }) => {
      test.setTimeout(180_000);
      const chat = await openChat(page);
      const response = await turn(chat, compound.request);
      for (const expected of compound.expected) {
        await expect(response).toContainText(expected);
      }
    });
  }
});
