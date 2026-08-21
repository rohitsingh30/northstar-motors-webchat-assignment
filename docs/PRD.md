# Northstar Motors AI Webchat — Product Requirements Document

## 1. Document purpose

This document converts the supplied product brief into an implementation and acceptance plan for
the Northstar Motors AI Webchat. The product will be embedded in the existing dealership website
and will use the supplied dealership platform as the source of truth for catalogue data and
business operations.

## 2. Product summary

Northstar customers currently browse inventory and dealership information through a conventional
website. The webchat adds a conversational route for discovering vehicles, understanding offers,
finding dealership information, and completing common sales and workshop tasks.

The assistant must feel natural, but it is not itself a system of record. Inventory, availability,
prices, slots, bookings, opening hours, offers, estimates, and operation outcomes must come from the
dealership platform. A successful chat response must never claim more than the platform response
confirms.

## 3. Product objectives

1. Help customers reach a relevant vehicle or service outcome with less navigation and form
   filling.
2. Complete supported dealership operations accurately inside a multi-turn conversation.
3. Protect customer data and server-side credentials.
4. Remain usable and recoverable on desktop, mobile, keyboard, and assistive technology.
5. Provide a clean-checkout application with automated checks and useful operational logs.

## 4. Success criteria

The assignment is considered complete when:

- all P0 journeys below can be demonstrated against the seeded dealership platform;
- protected dealership operations are performed only by the server-side webchat service;
- state-changing actions require a clear user confirmation and create no duplicate record after a
  retry;
- the chat survives a page refresh and keeps the current website/vehicle context;
- generated vehicle links open the correct website vehicle using `?vehicle={vehicleId}`;
- the interface exposes understandable open, closed, loading, unread, unavailable, success, and
  error states;
- core flows have automated unit, integration, and browser-level tests;
- a clean checkout starts with the documented Docker Compose command and documented environment
  variables.

Production business KPIs such as conversion rate are outside the locally evaluated assignment,
but the implementation should log enough non-sensitive events to measure task completion and
failures in a real deployment.

## 5. Users and needs

### 5.1 Vehicle shopper

Wants to describe a desired car in ordinary language, refine the search, compare a small set, check
current availability, view genuine offers, and take a next action.

### 5.2 Sales customer

Wants to send an enquiry, arrange a test drive, register interest in a reserved vehicle, request a
callback, or obtain an indicative part-exchange range without repeating context.

### 5.3 Workshop customer

Wants to understand service options, find a suitable workshop slot, make a booking, or securely
look up and manage an existing booking.

### 5.4 General visitor

Wants accurate addresses, contact details, departmental and holiday opening hours, or a way to
leave a message.

## 6. Scope and priority

### 6.1 P0 — required

- Persistent, multi-turn chat UI embedded throughout the existing website.
- Page and selected-vehicle context supplied to the conversation.
- Natural-language vehicle search and refinement.
- Vehicle cards, comparison, availability, and links to website vehicle pages.
- Published new-car offer discovery with exact platform-provided terms.
- Sales enquiries, test-drive booking, reserved-vehicle interest, and callback requests.
- Workshop service/location discovery, availability, booking, verified lookup, amendment, and
  cancellation.
- Dealership details, department hours, holiday exceptions, and dealership messages.
- Indicative part-exchange valuation with the platform-provided qualification.
- Relevant finance, privacy, and part-exchange notices.
- Accessible loading, error, unavailable, confirmation, and recovery behaviour.
- Server-side credential handling, idempotency, conversation persistence, and structured logging.
- Setup documentation and automated tests.

### 6.2 P1 — valuable enhancements

- Quick-reply suggestions based on context.
- Resumable pending forms after closing and reopening the widget.
- Relative-date understanding such as “next Friday”, resolved using the server timezone and shown
  back as an unambiguous date before confirmation.
- Compact conversation export or “start a new conversation” control.
- Anonymous product analytics that contain no message body or customer contact data.

### 6.3 Out of scope

- Changing dealership-platform code, behaviour, API contract, or seeded data.
- Payments, finance applications or approval, vehicle reservation/purchase, authentication
  accounts, or live-agent handoff.
- Promising delivery, response times, finance acceptance, queue position, or future availability.
- Training or fine-tuning a model.
- Any hosted dependency other than the selected LLM provider.
- Replacing the existing inventory website or using a pre-built webchat product.

## 7. Functional requirements

### FR-01: Chat shell and lifecycle

- A labelled launcher is visible without covering important content.
- Opening the widget moves focus into it; closing returns focus to the launcher.
- The UI displays prior messages, supports scrolling, and keeps the input reachable on mobile.
- Only one send is accepted for a given user submission while it is in flight.
- Closing with unseen assistant content produces an unread indicator.
- When the backend or LLM is unavailable, the user sees a useful recovery message and can retry.
- The user can deliberately start a new conversation; old state is not silently mixed into it.

### FR-02: Conversation persistence and context

- Browser script stores only an opaque conversation identifier and non-sensitive UI preferences.
  Conversation authorization is held in a same-origin `HttpOnly`, `SameSite=Lax` cookie and is not
  readable by JavaScript.
- Messages and workflow state are persisted by the webchat backend.
- Refreshing or navigating within the website restores the conversation.
- Each message may include allow-listed page context: page URL/path, section, selected vehicle ID,
  and page title. Arbitrary page text is not trusted as instructions.
- When a vehicle dialog is open, a request such as “Can I test drive this?” resolves “this” to the
  selected vehicle.

### FR-03: Vehicle discovery

- The assistant translates supported constraints into inventory filters: keyword, make, model,
  fuel, transmission, body style, availability, dealership, price, mileage, year, and sort.
- Follow-up refinements reuse prior search constraints unless the customer changes or clears them.
- Results show important decision fields including price or “price on request”, registration/year
  where returned, mileage, fuel, transmission, location, and current state.
- The assistant does not turn `null` price into a guessed price.
- Comparisons use only fields returned for the selected vehicles and clearly identify unknowns.
- Result links use `http://localhost:4173/?vehicle={vehicleId}` in local development.

### FR-04: Availability and offers

- Availability is rechecked immediately before an availability-dependent action.
- Available, reserved, and sold states are communicated plainly.
- Reserved vehicles can accept interest but cannot be test-driven.
- Sold vehicles can accept a sales enquiry but cannot be test-driven or accept reserved-vehicle
  interest.
- Offer terms are presented only from published offer records. Finance notices are presented when
  the conversation discusses finance.

### FR-05: Sales operations

- A sales enquiry collects dealership, enquiry type, message, and required contact details; a
  vehicle is optional.
- A test-drive flow selects a live slot, collects required contact details and optional notes, and
  asks for final confirmation before booking.
- A reserved-vehicle interest flow collects the vehicle and contact details, then records interest.
- A callback flow collects dealership, department, reason, contact details, and optional preferred
  time/vehicle.
- The assistant reports exact returned states: enquiry `received`, test drive `confirmed`, interest
  `registered`, and callback `requested`.
- The returned customer-facing reference and important appointment details are shown after success.

### FR-06: Workshop operations

- The assistant lists platform-supported service types and workshop locations.
- Availability searches use a selected service, dealership, and date range.
- A new booking collects a returned slot, registration, mileage, contact details, optional notes,
  and explicit confirmation.
- Existing booking lookup requires reference, surname, registration, and phone. A failed match
  uses the generic `BOOKING_NOT_FOUND` response and does not reveal which field differed.
- These four lookup values are submitted through a structured private form directly to the
  deterministic backend lookup endpoint; they are not added to the transcript or sent to the LLM.
- Amendment and cancellation are permitted only after successful lookup in the current
  conversation. The verified internal booking ID is kept server-side.
- Moving a booking uses a currently returned slot. A failed move leaves the original booking
  unchanged and is described that way.
- Cancellation requires explicit confirmation and reports the returned `cancelled` status.

### FR-07: Dealership and contact support

- The assistant can return dealership address, contact details, departments, ordinary hours, and
  holiday exceptions.
- Hours are department-specific; a closed service department must not be inferred from sales hours.
- Messages collect dealership, department, subject, message, contact details, and preferred contact
  method.
- A saved message is described as `received`, not answered.

### FR-08: Part exchange and notices

- The valuation flow collects dealership, vehicle registration, mileage, condition, and contact
  details.
- The assistant formats integer pence as GBP and shows the returned low-to-high range.
- Every estimate includes the applicable platform-provided qualification: it is indicative and
  remains subject to inspection, provenance checks, and market conditions.
- Privacy information is available before or when personal information is collected.

### FR-09: Confirmation and mutation policy

- Read-only searches may run immediately.
- Every write is staged as a structured draft and summarized for the user.
- The user must give an affirmative confirmation after seeing the final material fields.
- Changing a material field invalidates the old confirmation.
- For record-creation operations, the backend—not the model—generates the idempotency key and
  performs the write.
- Amendments and cancellations are deduplicated locally by a client action ID because those
  platform endpoints do not accept an idempotency key.
- Duplicate client submissions and safe record-creation retries return the original outcome rather
  than create a second record.

### FR-10: Errors and recovery

- Validation errors are mapped to the relevant field without discarding other collected values.
- `SLOT_UNAVAILABLE` triggers an explanation and an offer to search again.
- `VEHICLE_RESERVED` offers interest registration; `VEHICLE_UNAVAILABLE` offers another vehicle or
  a sales enquiry.
- `BOOKING_NOT_FOUND` remains non-enumerating.
- Retryable upstream failures retain the draft and offer retry. Non-retryable failures explain the
  next available action.
- An interrupted request can be retried without duplicate business writes.

## 8. Conversation behaviour requirements

The model may interpret intent, extract candidate fields, ask natural follow-up questions, and
compose a response from tool results. Deterministic application code must control tool schemas,
validation, confirmation state, authorization, idempotency, identity-verification state, and the
final interpretation of business statuses.

The assistant must:

- ask only for fields that are missing or ambiguous;
- avoid requesting all personal details before the customer chooses an action;
- restate dates, locations, vehicles, prices, and selected slots before a write;
- distinguish “request received” from “booking confirmed”;
- say when information is unavailable rather than inventing it;
- ignore attempts in user or page content to reveal secrets or bypass confirmation/tool policy;
- never expose raw system prompts, API keys, internal booking IDs, stack traces, or model tool
  payloads.

## 9. UX and accessibility requirements

- Target WCAG 2.2 AA for the webchat interaction.
- All controls have accessible names and visible focus styles.
- The dialog uses appropriate dialog semantics, focus management, and Escape behaviour.
- New messages are announced through a polite live region without rereading the entire transcript.
- Status such as “Searching vehicles” or “Confirming booking” is available without relying on
  animation alone.
- Colour is never the only indicator of availability or error.
- Touch targets are at least 44 by 44 CSS pixels where practical.
- Reduced-motion preferences are respected.
- Vehicle and offer cards remain understandable in linear screen-reader order.
- On narrow screens the widget becomes a contained full-height panel and does not cause horizontal
  scrolling.

## 10. Security, privacy, and data requirements

- `NORTHSTAR_API_KEY` and the LLM key exist only in backend environment configuration.
- The public webchat API accepts no caller-supplied upstream URL, API key, tool name, internal
  record ID, or idempotency key.
- Inputs have length, type, enumeration, and format limits at the webchat boundary and again before
  dealership calls.
- Conversation IDs are unguessable. A separate unguessable token in a same-origin `HttpOnly`
  cookie authorizes access to an anonymous conversation; only its hash is stored server-side.
- Logs contain correlation IDs, tool names, duration, status, and redacted errors; they exclude
  contact values, booking lookup proofs, API keys, and full message bodies.
- Sensitive lookup inputs are used for the lookup and not echoed after verification.
- Conversation retention is configurable. The local default is 30 days, with a user-initiated
  deletion/new-conversation operation.
- Personal-data collection is limited to the fields required by the selected platform operation.

## 11. Non-functional requirements

### Performance

- The widget shell should become interactive without waiting for the LLM.
- Acknowledgement/loading feedback appears within 200 ms of sending.
- Local catalogue operations should have a 5-second upstream timeout; LLM turns should have a
  bounded timeout and a visible pending state.
- Long result sets are limited and paginated; a chat turn should show a focused subset rather than
  all 60 vehicles.

### Reliability

- SQLite transactions protect conversation and operation state.
- Record-creating writes use a stable idempotency key persisted before the upstream call.
- The dealership client uses bounded retries for safe reads and for record creation with the same
  idempotency key. Amendment and cancellation use local action deduplication and explicit
  reconciliation after an ambiguous outcome.
- Health endpoints distinguish process health from dependency readiness.

### Maintainability

- UI, conversation orchestration, dealership integration, persistence, and model-provider code are
  separate modules.
- Dealership payloads are validated at the adapter boundary.
- Model-specific code is behind an interface so a deterministic fake can run tests without network
  access.
- Important decisions and known limitations are documented.

### Compatibility

- Current evergreen Chrome, Edge, Firefox, and Safari.
- Responsive operation from 320 CSS pixels upward.
- Docker Compose remains the standard local startup path.

## 12. Acceptance scenarios

| ID | Scenario | Expected result |
| --- | --- | --- |
| AC-01 | Ask for a hybrid SUV below £45,000, then say “only Volvos” | Search constraints persist and results contain matching Volvo vehicles only |
| AC-02 | Compare two returned vehicles | Comparison contains only API-backed facts and correct website links |
| AC-03 | Ask for the price of `veh-019` | Assistant says price on request and does not estimate |
| AC-04 | Book a returned slot for `veh-001` | One booking is created and reported as `confirmed` with its reference |
| AC-05 | Try a test drive for reserved `veh-007` | No booking is attempted; interest registration is offered |
| AC-06 | Ask about sold `veh-013` | Sold state is clear; sales enquiry remains available |
| AC-07 | Submit the same confirmed record creation twice/retry after timeout | The same idempotency key is used and no duplicate record is created |
| AC-08 | Search Bolton workshop slots in the first seeded week | No availability is treated as a valid result and alternatives are offered |
| AC-09 | Create a workshop booking from a returned slot | Booking is `confirmed`; reference and appointment are displayed |
| AC-10 | Look up `WORK-10001` with all matching values | Appointment, service, dealership, and status are returned |
| AC-11 | Look up a booking with one incorrect proof | Generic not-found response; no field-specific information leaks |
| AC-12 | Amend a verified booking to an already claimed slot | Original booking remains unchanged and customer can search again |
| AC-13 | Cancel a verified booking after confirmation | Returned status is `cancelled`; repeated cancellation is handled safely |
| AC-14 | Ask holiday service hours | Department-specific exception is returned; no ordinary-hours guess |
| AC-15 | Request part-exchange estimate | Exact range and qualification are displayed |
| AC-16 | Refresh with an active conversation | Transcript and pending non-sensitive workflow state are restored |
| AC-17 | Navigate with a vehicle dialog open and ask “Is this available?” | Correct vehicle ID is used from allow-listed page context |
| AC-18 | Use keyboard only | Launcher, transcript, controls, cards, confirmation, and close flow work |
| AC-19 | Stop the LLM/dealership dependency | Clear unavailable/error state appears and the draft is recoverable |
| AC-20 | Inspect browser traffic and assets | No dealership or LLM API key is present |
| AC-21 | Ask for current published new-car offers | Only platform-published terms are shown and the finance notice is available |
| AC-22 | Submit a general or availability sales enquiry | One record is created and reported as `received`, without promising a response time |
| AC-23 | Confirm interest in reserved `veh-007` | One interest record is created and reported as `registered`, without promising queue position |
| AC-24 | Request a sales/service callback | One callback is created and reported as `requested`, without guaranteeing an exact callback time |
| AC-25 | Leave a message for a dealership department | One message is created and reported as `received`, not answered |

## 13. Delivery plan

1. Build the webchat backend, persistence layer, dealership adapter, and deterministic fake model.
2. Implement vehicle/reference read tools and the embedded accessible chat shell.
3. Add staged/confirmed sales and workshop writes with idempotency.
4. Add verified booking management, notices, and contact/valuation flows.
5. Add failure recovery, security controls, logging, and accessibility polish.
6. Complete automated tests, seeded end-to-end scenarios, setup documentation, and known
   limitations.

## 14. Assumptions and known limitations

- An OpenAI-compatible LLM is the proposed provider; exact model selection is configurable rather
  than hard-coded.
- The application is anonymous and does not include customer accounts. A same-origin `HttpOnly`
  conversation cookie protects chat history, while dealership booking lookup uses the platform's
  four matching fields.
- LLM responses require provider access; a deterministic fallback can still explain unavailability,
  restore history, and preserve pending drafts, but will not attempt broad free-form interpretation.
- The supplied platform is authoritative and is not modified to support the webchat.
- Local URLs and ports are development defaults and become configuration in other environments.
