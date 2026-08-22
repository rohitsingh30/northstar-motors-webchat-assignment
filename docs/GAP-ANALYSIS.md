# Northstar Motors Webchat — Gap Analysis

## Audit scope

Audited on 22 August 2026 against:

- `PRODUCT-BRIEF.md`
- `docs/PRD.md`, including its 25 acceptance scenarios
- The running website and webchat at `http://localhost:4173`
- Desktop and 390 × 844 mobile viewports
- The current automated test suite and relevant webchat implementation
- `samplequestions.md`, including vehicle, sales, workshop, contact, and mutation-flow prompts
- A visual pass of the running widget using the workshop sample journey

This document records audit findings only. No product implementation changes were made as part of
this audit.

## Executive summary

The implementation contains useful server-side foundations for conversation persistence,
dealership integration, workflow drafts, confirmation, idempotency, and private booking lookup.
However, most of those capabilities are unreachable through the running customer conversation.

The current visual shell is a non-blocking right-side panel, but the conversation restored an old
transcript during the sample-question pass. That stale context materially changes intent routing
and can make the result look plausible while answering a different question. The highest current
product risk is therefore conversation-state isolation and end-to-end coverage, not just the panel
treatment.

The running deterministic assistant fails most P0 conversational journeys. Its keyword matching
misclassifies requests, ignores common natural vehicle descriptions, does not maintain refinement
state, and cannot initiate most sales or workshop operations. Result presentation is generic and
frequently labels non-vehicle records as vehicles.

## Live probes and observed results

### Vehicle discovery

**Prompt**

> Find me an electric SUV under £45,000

**Observed result**

The assistant returned generic help text and no vehicle results.

**Expected result**

The assistant should translate the description into fuel type, body style, and maximum-price
filters, then display matching authoritative inventory.

### Holiday opening hours

**Prompt**

> What are Manchester dealership service hours on the next bank holiday?

**Observed result**

The assistant displayed four dealership cards and introduced them as “4 matching vehicles.” It
did not show department-specific hours or the holiday exception.

**Expected result**

The Manchester dealership's published service-department holiday exception should be returned.
Sales hours must not be substituted for service hours.

### Workshop booking

**Prompt**

> Book an MOT at Stockport next Tuesday morning

**Observed result**

The assistant listed all eight service types and introduced them as “8 matching vehicles.” It did
not search Stockport availability, interpret the date, present slots, collect booking details, or
prepare a confirmation.

**Expected result**

The assistant should identify MOT and Stockport, resolve or clarify the date, show suitable live
slots, collect only the missing booking fields, and stage the final booking for explicit
confirmation.

### Published offers

**Prompt**

> Show me the current published new-car offers

**Observed result**

The assistant returned six used BMW vehicles, described as matching vehicles. It did not return
published offer records or a finance notice.

**Expected result**

Only platform-published new-car offers and their exact terms should be displayed, with the
applicable finance notice.

### Persistence

Opening the application in a fresh browser tab restored the existing transcript and open-chat
preference. This part of the product worked during the audit.

## Prioritised findings

### P0 — Critical journey and architecture gaps

#### GAP-001 — [Resolved] Chat blocks the dealership website

The earlier audit observed a modal chat with a full-page backdrop. The current visual pass shows a
non-blocking right-side panel: the website inventory remains visible and the page is not dimmed.
Keep this as a browser regression check because the requirement is architectural and easy to
reintroduce.

This conflicts directly with the product-brief requirement that the webchat remain available
throughout the website without obscuring important content.

#### GAP-002 — Chat cannot be used alongside vehicle details

Vehicle details are also modal. When a vehicle dialog is open, the chat launcher cannot be used.
The customer therefore cannot naturally review a vehicle and ask “Is this available?” or “Can I
test drive this?”

This blocks PRD acceptance scenario AC-17 and undermines selected-vehicle context.

#### GAP-003 — Natural vehicle descriptions are not understood

The local assistant does not reliably recognise ordinary descriptions unless narrow trigger words
are present. It does not meaningfully extract the full supported filter set: make, model, fuel,
transmission, body style, availability, dealership, price, mileage, year, and sort.

#### GAP-004 — Multi-turn search refinement is absent

The assistant does not maintain structured search constraints across turns. The required journey
“hybrid SUV below £45,000,” followed by “only Volvos,” cannot be completed reliably.

#### GAP-005 — Intent detection produces confidently incorrect answers

Intent routing uses substring matching. For example, `Manchester` contains `car`, so a Manchester
hours request can be routed through vehicle-search logic. Because incorrect results are presented
confidently, this is more serious than a simple unsupported-intent response.

#### GAP-006 — Published offer discovery is broken

The phrase `new-car offers` is treated as a vehicle search before the offer intent is considered.
The product consequently returns used inventory instead of published offer records and omits the
required finance notice.

#### GAP-007 — Core sales operations are unreachable through local chat

The running conversation could not initiate or complete:

- General, availability, finance, or part-exchange sales enquiries
- Test-drive availability and confirmed booking
- Reserved-vehicle interest registration
- Sales or service callbacks

Server-side workflow code exists, but the local conversational provider does not expose these
journeys to the customer.

#### GAP-008 — Core workshop operations are unreachable through local chat

The running conversation could not initiate or complete:

- Workshop availability search using service, location, and date constraints
- New workshop booking
- Verified existing-booking lookup
- Booking amendment
- Booking cancellation

The private lookup form and workflow services exist in code but are not reachable through the
tested ordinary chat experience.

#### GAP-009 — Contact and part-exchange journeys are unreachable

The conversation could not initiate dealership messages or an indicative part-exchange valuation.
As a result, the required privacy and part-exchange qualification notices are also not delivered in
context.

#### GAP-010 — Page context is collected but ineffective in local mode

The browser sends allow-listed section, path, title, and selected-vehicle ID context. The local
assistant does not use that context to resolve references such as “this vehicle.” The modal conflict
also prevents the intended side-by-side vehicle-review journey.

### P0 — Accuracy, presentation, and recovery gaps

#### GAP-011 — Tool-result summaries use the wrong entity type

Dealerships, service types, and other lists can all be introduced as “matching vehicles.” This
makes otherwise authoritative platform data misleading.

#### GAP-012 — Result cards omit essential decision information

Chat vehicle cards omit visibly important fields such as availability, dealership/location, fuel,
transmission, and imagery. The presentation does not clearly demonstrate price-on-request handling.

#### GAP-013 — Dealership and service cards are incomplete

Observed dealership cards showed only name and town. Service cards showed only a repeated `Name`
label. Addresses, phone numbers, departments, descriptions, ordinary hours, holiday exceptions,
and meaningful next actions were absent.

#### GAP-014 — Offer cards are not designed as offer cards

A generic fact-card renderer is used for several unrelated record types. It can expose raw fields
such as `monthlyPricePence` rather than customer-friendly GBP values, terms, expiry information,
eligibility, and finance notices.

#### GAP-015 — No useful first-open state

The initial conversation area is blank. There is no welcome message, capability explanation,
contextual suggestion, or quick action. The only orientation is the input placeholder.

#### GAP-016 — Progress states are generic

Every request uses “Northstar is working on that…” rather than task-specific states such as
“Searching vehicles,” “Checking availability,” or “Finding workshop times.”

#### GAP-017 — Error recovery is weak

Failures are reduced to status text such as “Select Send to retry.” There is no dedicated retry
control, retained-draft explanation, field-level validation presentation, or suggested alternative
for business errors such as unavailable slots and reserved vehicles.

#### GAP-018 — No clear launcher-level unavailable state

The launcher does not visibly communicate backend or model unavailability. The user must open the
panel before discovering a failure.

### P1 — Visual design and responsive gaps

#### GAP-019 — Desktop layout feels detached and obstructive

The narrow, tall panel, strong modal backdrop, repetitive white cards, and heavy composer create an
internal-tool appearance rather than an integrated dealership assistant. The backdrop destroys the
visual relationship between the conversation and the vehicle/page being discussed.

#### GAP-020 — Mobile composer consumes excessive space

At 390 × 844 the panel remained usable and did not visibly overflow horizontally, but the label,
multi-line textarea, and full-width Send button consume a large portion of the viewport. This
reduces the space available for conversation and result cards.

#### GAP-021 — Long card lists are difficult to scan

Cards are displayed as a repetitive vertical stack without strong comparison hierarchy, compact
choices, sticky context, or progressive disclosure. On mobile, even a short result set requires
substantial scrolling.

#### GAP-022 — Visual states are underdeveloped

The product lacks distinctive visual treatments for welcome, empty results, unavailable service,
recoverable error, confirmation success, and contextual suggestions.

### P1 — Accessibility gaps

#### GAP-023 — Completed messages are not reliably announced

The status paragraph uses a polite live region, but the transcript itself is not a live region.
Completed assistant content may therefore not be announced when it replaces the temporary working
status.

#### GAP-024 — Modal semantics unnecessarily remove page access

Opening the chat makes unrelated dealership content inert. This affects keyboard and assistive-
technology users as well as pointer users and conflicts with the intended persistent assistant
model.

#### GAP-025 — Some touch targets appear undersized

The close icon and header actions do not visibly provide the practical 44 × 44 CSS-pixel target
requested by the PRD.

#### GAP-026 — No reduced-motion handling was found

The PRD explicitly requires reduced-motion preferences to be respected. No corresponding handling
was identified in the webchat styling.

#### GAP-027 — No automated browser accessibility coverage

No Playwright, Cypress, axe, keyboard-navigation, screen-reader-structure, or responsive browser
tests were found.

### P1 — Verification and delivery gaps

#### GAP-028 — The 25 acceptance scenarios lack end-to-end coverage

Existing tests cover useful lower-level areas such as persistence, origin security, read tools,
workflow confirmation mechanics, redaction, and provider boundaries. They do not demonstrate most
of the PRD's customer journeys.

#### GAP-029 — No browser test covers confirmation and idempotency

There is no end-to-end test proving that a customer can review a final draft, confirm it once,
receive the exact platform status/reference, retry safely, and avoid duplicate records.

#### GAP-030 — Persistence and page context lack browser regression tests

Conversation restoration worked during this audit, but no browser automation protects it. The
selected-vehicle contextual journey is likewise not covered end to end.

#### GAP-031 — The clean-checkout fallback cannot demonstrate the P0 product

Without an OpenAI key, the documented deterministic provider is the running customer experience.
It supports only a few crude keyword paths and cannot demonstrate the required P0 sales, workshop,
contact, context, or refinement journeys.

#### GAP-032 — The widget is not portable or easy to integrate

The current integration requires host-owned chat markup with exact element IDs, separate CSS and
JavaScript imports, shared host CSS variables, a custom context event, and a bespoke proxy. It is
not an out-of-box widget that another Northstar page or compatible dealership site can mount with
one entry point and a small configuration object.

The integration should provide a self-mounting, style-isolated widget, configurable API base URL,
and a documented public lifecycle/context API. Dealership and LLM credentials must remain in the
backend; widget consumers should never configure secrets.

#### GAP-033 — Vehicle-result navigation reloads instead of integrating with the host page

Vehicle links rely only on a normal document navigation. In an embedded widget this discards page
state and prevents the host application from opening its existing vehicle-detail experience
directly. The widget needs a cancellable navigation event with the safe URL retained as a fallback.

## Sample-question visual pass — 22 August 2026

The following observations came from the running widget, not source inspection alone.

| Sample question or step | What rendered | Gap / implication |
| --- | --- | --- |
| “Book an MOT at Stockport next Tuesday morning” | The transcript said “I found 8 matching vehicles” and displayed every service type. | Service, town, and date were not carried into the availability search. The result summary also used the wrong entity type. |
| Click `Find times` on the first service card | The picker showed `At Liverpool`, Saturday 22 August at 03:00 pm. | The city label itself is now visible, but the selected slot is not scoped to the requested Stockport journey. A customer could book the wrong workshop. |
| “Show me the current published new-car offers” | The restored transcript showed used BMW vehicle cards. | Offer intent still needs a clean-session browser assertion proving offer records and finance notice, not vehicle inventory. |
| Vehicle inventory behind the widget | A vehicle card visibly carried a `SOLD` badge. | Sold stock is still exposed on the primary dealership discovery surface, even if chat result rendering filters some sold records. |
| Reopen the widget / run another question | The widget restored a long prior transcript before the new test. | Visual acceptance tests are contaminated by persisted conversation state; a clean test fixture or deterministic “new conversation” setup is required. |
| “Show cars under £35,000” | Returned a long vehicle list, including repeated model/year records. | The basic price intent works, but the result set is noisy and duplicates are not explained. |
| “Find BMWs in Stockport” | Returned BMW 3 Series records at Stockport. | Make + town filtering works for this phrasing, but the result cards still expose booking actions without a verified availability state. |
| “Actually diesel under £30,000” | Returned diesel BMW 3 Series records under £30,000. | A refinement can work in one path, but it is not isolated from prior conversation state and has no visible active-filter summary. |
| “What are the current new-car offers?” | Returned offer prose with monthly payment, upfront amount, APR, term, and mileage. | This clean-session run produced offer data; add a typed offer card and assert the finance notice rather than relying on plain text. |
| “I need an MOT in Manchester” | Prose listed 11:00 slots, while the rendered picker showed 12:00 pm. | The assistant summary and picker disagree on the actual appointment time. |
| “What are your Saturday opening hours?” | Returned all four locations and Sales/Service/Parts hours. | Broad hours coverage works, but the response is overly long and does not ask which department/location matters. |
| “I want to test drive the BMW 1 Series” | Returned three vehicle cards; the rendered `Book test drive` action remained disabled in the chat result. | The discovery step does not progress to vehicle/slot selection, so the test-drive journey is not customer-completable. |

The location presentation fix is therefore only partially successful: `At [city]` is present in the
slot picker, but the sample journey still selects the wrong city because the routing and filtering
contract is not being verified end to end.

### GAP-034 — Sample questions cannot be evaluated in a clean conversation

The widget restores the previous conversation and immediately renders its old transcript. During
the visual pass, the MOT/Stockport prompt appeared alongside earlier vehicle, dealership, and offer
turns. Because the local provider considers conversation history when resolving intent, this can
produce a valid-looking response for the wrong request.

The browser acceptance harness needs an isolated conversation per scenario, a deterministic seeded
conversation fixture, or an explicit test-only reset that does not delete a user’s real history.

### GAP-035 — Workshop service, town, and date are not proven through the rendered journey

The sample question “Book an MOT at Stockport next Tuesday morning” should resolve a service type,
workshop location, and date window before showing slots. The visual result instead showed all eight
services and labelled them as matching vehicles. This is a P0 correctness gap even though the
underlying workshop-slot endpoint and service cards exist.

### GAP-036 — Workshop slot location can contradict the customer’s requested town

The slot picker now displays a city label, which improves clarity. However, the visual pass reached
Liverpool from the Stockport sample journey. The UI must retain the resolved location in the request,
show it in the heading and confirmation, and refuse to present a different city as if it were the
requested result.

### GAP-037 — Sold inventory remains visible on the dealership’s primary surface

The main website inventory grid still visibly includes sold vehicles with a `SOLD` badge. A sold
vehicle is not a useful discovery result for a customer looking to buy or book a test drive. If the
business wants historical/sold stock for a separate purpose, it needs a deliberate archive or
“recently sold” surface rather than mixing it into current stock.

### GAP-038 — Sample-question coverage does not distinguish read-only from mutation journeys

The sample file explicitly requires invalid contact details, correction, review, cancellation,
confirmation, and reopening `View booked test drive`. Existing tests cover workflow mechanics, but
there is no browser-level scenario that exercises these states against a reset database while
asserting no duplicate booking. The same gap applies to workshop booking, amendment, and
cancellation.

### GAP-039 — Contact and dealership prompts need rendered acceptance assertions

The sample questions cover department-specific phone/email, departments, Saturday hours, bank
holidays, holiday exceptions, service messages, callbacks, finance, privacy, and valuation
qualification. The current browser evidence does not demonstrate that each prompt renders the right
typed card or notice; generic dealership/service cards can make an unsupported answer appear
complete.

### GAP-040 — Workshop prose and picker disagree on appointment time

For “I need an MOT in Manchester”, the assistant prose listed 11:00 appointments while the
rendered picker exposed 12:00 pm for the same Saturday. A customer cannot know which time will be
booked. The summary and slot component must share one normalized availability payload and timezone.

### GAP-041 — Test-drive result actions can remain disabled after discovery

“I want to test drive the BMW 1 Series” returned plausible vehicle cards, but the chat card's
`Book test drive` control was disabled during the visual pass and did not open the booking flow.
Discovery must resolve a single vehicle (or offer an explicit choice) and make the next action
available only when that vehicle's test-drive availability has been loaded.

### GAP-042 — Vehicle result sets need deduplication and active-filter context

The under-£35,000 query returned repeated BMW 1 Series and BMW 3 Series records across years with
the same visible specification. The result view needs a compact active-filter summary, clear year
identity, and deduplication or an explicit “same model, different vehicle” explanation.

## Remediation tracker

## Full sample-question coverage matrix

This is the complete question inventory from `samplequestions.md`. “Observed” means the prompt was
run in the browser during this audit. “Code-only” means the underlying workflow or unit tests were
inspected, but the customer-facing browser path was not proven. Mutation paths were intentionally
stopped before submitting real contact details or creating/cancelling a live booking.

| Area | Prompt / step | Result | Classification |
| --- | --- | --- | --- |
| Vehicle | Cars under £35,000 | Returned vehicles, but repeated model/year records and no active-filter summary. | Observed — GAP-042 |
| Vehicle | Petrol automatic SUV with low mileage | No clean browser evidence of all three constraints being applied together. | Not proven — GAP-003 |
| Vehicle | BMWs in Stockport | Returned BMW 3 Series records at Stockport. | Observed — partial pass |
| Vehicle | Below £25,000 and under 20,000 miles | No clean browser evidence of both constraints together. | Not proven — GAP-003 |
| Vehicle | Refine to diesel and under £30,000 | Returned diesel BMW records under the price cap in one continued conversation. | Observed — partial pass; GAP-004 |
| Vehicle | 2024 or newer | No rendered assertion of the year refinement. | Not proven — GAP-004 |
| Vehicle | Compare BMW 1 Series and 3 Series | No typed comparison result proven in the visual pass. | Not proven — GAP-031 |
| Vehicle | Mileage, fuel, gearbox, monthly payment for this car | Vehicle cards expose these fields, but “this car” context is not reliable across the widget/page. | Partial — GAP-010/GAP-012 |
| Vehicle | Open details for second vehicle | Normal vehicle links exist; chat-to-host detail integration reloads the page. | Partial — GAP-033 |
| Vehicle | Is this vehicle available? | No reliable selected-vehicle context in the chat flow. | Not proven — GAP-002/GAP-010 |
| Vehicle | Meaning of reserved | Reserved badges exist, but contextual explanation and next action were not proven. | Not proven — GAP-017 |
| Vehicle | Current new-car offers | Clean run returned offer terms as prose; typed offer card and finance notice were not asserted. | Partial — GAP-006/GAP-039 |
| Sales | BMW 3 Series availability | No clean browser journey proving inventory availability semantics. | Not proven — GAP-007 |
| Sales | General buying question | No dedicated sales-support state proven. | Not proven — GAP-007 |
| Sales | Finance information | No customer-facing finance flow and notice proven. | Not proven — GAP-007/GAP-039 |
| Sales | Part-exchange enquiry | No valuation/contact flow proven. | Not proven — GAP-009 |
| Sales | Reserved vehicle register interest | No private interest-registration flow proven. | Not proven — GAP-007 |
| Sales | Callback tomorrow afternoon | No callback form, department, or confirmation proven. | Not proven — GAP-007/GAP-039 |
| Sales | Test drive this vehicle | Vehicle cards rendered, but the chat booking action remained disabled. | Observed failure — GAP-041 |
| Test drive | Select vehicle/date/time | Could not reach a selectable test-drive slot from the chat result. | Observed failure — GAP-041 |
| Test drive | Invalid contact details / correction | Not safely reachable without a working booking form; no real data entered. | Blocked by prior failure — GAP-038 |
| Test drive | Review booking | Not reached. | Blocked by prior failure — GAP-038 |
| Test drive | Close from top-right | Generic dialog close works; booking-flow close was not reached. | Partial — GAP-038 |
| Test drive | Confirm booking | Intentionally not submitted; no browser assertion of receipt/idempotency. | Not run — GAP-038 |
| Test drive | Reopen/close View booked test drive | Not reached because no booking was safely created. | Not proven — GAP-038 |
| Workshop | Supported service types | Service list rendered, but prior runs labelled services as vehicles. | Observed failure — GAP-011/GAP-013 |
| Workshop | Workshop locations | Locations and departments returned; card typing and concise presentation need assertions. | Observed partial — GAP-039 |
| Workshop | MOT in Manchester | Prose and picker disagreed on 11:00 vs 12:00 pm. | Observed failure — GAP-040 |
| Workshop | Annual service next week | No clean browser proof of service/date/location filtering. | Not proven — GAP-035 |
| Workshop | Book workshop appointment | No end-to-end booking form/confirmation proven. | Not proven — GAP-008 |
| Workshop | Change existing booking | Private identity-verification form not proven in browser. | Not proven — GAP-008/GAP-038 |
| Workshop | Cancel existing booking | Private identity-verification form not proven in browser. | Not proven — GAP-008/GAP-038 |
| Workshop | Find existing booking | Private identity-verification form not proven in browser. | Not proven — GAP-008/GAP-038 |
| Contact | Stockport dealership location | Location data is present in the host page and chat responses. | Partial pass — GAP-039 |
| Contact | Manchester sales phone/email | No typed department-specific contact card assertion. | Not proven — GAP-039 |
| Contact | Dealership departments | Department data appears, but generic card labels remain confusing. | Partial — GAP-013/GAP-039 |
| Contact | Saturday opening hours | All four sites and departments rendered; response is verbose. | Observed partial — GAP-039 |
| Contact | Bank holiday opening | No clean department-specific exception assertion. | Not proven — GAP-039 |
| Contact | Holiday exceptions | No complete exception matrix proven. | Not proven — GAP-039 |
| Contact | Leave service message | No message form/validation/receipt proven. | Not proven — GAP-009 |
| Contact | Dealership callback | No callback form/confirmation proven. | Not proven — GAP-009 |
| Contact | How finance works | No dedicated finance explainer state proven. | Not proven — GAP-007/GAP-039 |
| Contact | Personal-data use | No privacy answer/notice proven in the customer flow. | Not proven — GAP-009/GAP-039 |
| Contact | Part-exchange calculation | No qualification or indicative estimate proven. | Not proven — GAP-009 |
| Contact | Indicative 2019 BMW 3 estimate | No registration/mileage/condition qualification flow proven. | Not proven — GAP-009 |

### Safety boundary for mutation tests

The audit exercised discovery, rendering, routing, slot selection where available, and close/error
states. It did not submit a real name, email, phone number, test-drive booking, workshop booking,
amendment, or cancellation. Those cases remain open until they can run against a seeded test backend
with deterministic reset, private-form assertions, confirmation receipts, and duplicate-request
checks.

| Gap | State | Evidence |
| --- | --- | --- |
| GAP-001 | Implemented | Full-height right panel reflows the desktop page; mobile is full-screen. |
| GAP-002 | Open | The supplied website's modal behaviour is preserved; solve inside the plugin without host changes. |
| GAP-023 | Implemented | Transcript is a polite additive `role=log` live region. |
| GAP-024 | Implemented | Chat and vehicle detail no longer make the host page inert. |
| GAP-025 | Implemented | Header and action controls have 44px minimum targets. |
| GAP-026 | Implemented | Widget CSS disables motion under `prefers-reduced-motion`. |
| GAP-032 | Implemented; integration smoke passed | Service hosts widget; website adds one script and keeps no widget source or application changes. |
| GAP-019 | Implemented | Desktop panel reduced to 380px with a quiet header, flatter messages, and subtle border/shadow. |
| GAP-020 | Implemented | Compact single-row composer preserves transcript space on narrow screens. |
| GAP-033 | Implemented | Safe href, history adapter, and optional callback keep chat open without host imports. |
| GAP-031 | Partially remediated | Unit-level local routing covers more sample intents, but the visual pass still shows stale-context contamination, wrong workshop routing, and incorrect offer presentation. |
| GAP-034 | Open | The visual pass restored an old transcript before the sample journey. |
| GAP-035 | Open | MOT + Stockport + date fell back to the full service list. |
| GAP-036 | Open | A Stockport journey rendered a Liverpool slot; the city label alone is insufficient. |
| GAP-037 | Open | The website inventory grid still exposes a sold vehicle. |
| GAP-038 | Open | Mutation flows lack isolated browser coverage and duplicate-booking assertions. |
| GAP-039 | Open | Contact, hours, finance, privacy, and valuation prompts lack rendered acceptance assertions. |
| GAP-040 | Open | MOT prose showed 11:00 while the picker showed 12:00 pm. |
| GAP-041 | Open | BMW 1 Series test-drive cards exposed a disabled booking action. |
| GAP-042 | Open | Price-filter results repeated near-identical records without filter context. |

### Sample-question follow-up

The sample-question pass found that a previous vehicle intent could leak into dealership and
workshop questions, descriptive vehicle filters were dropped, and several contact, finance,
comparison, and privacy questions fell through to generic vehicle search. Unit-level routing has
since been separated in `webchat-service`, with workshop towns and service names resolved from live
platform data. The visual pass shows that this is not yet equivalent to a customer-journey fix:
the restored transcript still contaminates intent, the workshop request can land on the wrong city,
and published offers can still render as used vehicles. The dealership website feature code and
platform data source were not changed by the routing work.

## Checks that passed

- The conversation transcript and open-chat preference survived opening the site in a fresh tab.
- A working/loading status appeared immediately after sending a message.
- Opening the panel moved focus to the message input.
- The close flow is designed to return focus to the launcher.
- At a 390 × 844 viewport, the panel remained usable without obvious horizontal overflow.
- The launcher, dialog, transcript, input, Send button, and generated vehicle links expose basic
  accessible names.
- The host website visibly distinguishes available, reserved, and sold vehicles.
- The host site's inventory presentation is substantially more informative than the equivalent
  chat cards.

## Recommended remediation order

1. Establish the reusable, style-isolated widget boundary and public integration API.
2. Preserve the non-blocking desktop panel and deliberate mobile full-screen treatment; add a
   browser regression proving chat remains usable while vehicle details are visible.
3. Make conversational capability honest and complete in the default local configuration:
   structured intent routing, natural vehicle filters, page context, and persisted refinements.
4. Connect every P0 sales, workshop, contact, and valuation workflow to the conversation, while
   retaining deterministic confirmation and idempotency controls.
5. Introduce typed, customer-friendly vehicle, comparison, offer, dealership, hours, service, slot,
   confirmation, and receipt views.
6. Add welcome, quick-action, contextual, progress, empty, unavailable, error, retry, and success
   states.
7. Complete keyboard, announcement, touch-target, focus, reduced-motion, and responsive work.
8. Automate the seeded PRD acceptance scenarios in a real browser against the composed services.
