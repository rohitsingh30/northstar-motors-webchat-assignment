# Widget view modules

Views convert closed server-authored payloads into safe Shadow DOM elements. They do not perform
network requests or own conversation state.

## Files

| File | Responsibility |
| --- | --- |
| [`message.js`](./message.js) | Small closed view-type dispatcher and stable public facade |
| [`message-content.js`](./message-content.js) | Safe assistant text grouping into paragraphs and explicit list presentation |
| [`suggestions.js`](./suggestions.js) | Balanced suggestion chips and typed action data |
| [`vehicle.js`](./vehicle.js) | Vehicle card, availability card, image/navigation behavior, comparison table |
| [`appointments.js`](./appointments.js) | Test-drive/workshop slot pickers, detail forms, inline confirmations, and booking disclosures |
| [`information-cards.js`](./information-cards.js) | Offers, dealerships, hours, services, and generic read-only facts |
| [`workflow-forms.js`](./workflow-forms.js) | Collecting-state forms and draft cards |
| [`workflow-confirmations.js`](./workflow-confirmations.js) | Application-owned protected-write review cards |
| [`workflow-receipts.js`](./workflow-receipts.js) | Public receipts, private lookup form, and restored booking views |
| [`workflow-cards.js`](./workflow-cards.js) | Stable workflow-renderer facade |

## Adding a view

1. Define a versioned server payload with only public fields.
2. Add a small renderer or focused module.
3. Register the view type in `message.js`.
4. Add it to response renderability when provider-loop termination requires it.
5. Use DOM properties, validated IDs, and controlled navigation; never model HTML.
6. Add integration coverage and a manual accessibility/browser check.

Add behavior to the owning family module. `message.js` should remain limited to dispatch and stable
exports; it must not accumulate card or form implementation details.
