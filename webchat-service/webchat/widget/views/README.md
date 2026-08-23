# Widget view modules

Views convert closed server-authored payloads into safe Shadow DOM elements. They do not perform
network requests or own conversation state.

## Files

| File | Responsibility |
| --- | --- |
| [`message.js`](./message.js) | View-type renderer registry; vehicle lists/details, slots, forms, confirmations, receipts, offers, dealerships, hours, services, booking details, scoped business facts, and restored version-1 business cards |
| [`message-content.js`](./message-content.js) | Safe assistant text grouping into paragraphs and explicit list presentation |
| [`suggestions.js`](./suggestions.js) | Balanced suggestion chips and typed action data |
| [`vehicle.js`](./vehicle.js) | Vehicle card, availability card, image/navigation behavior, comparison table |

## Adding a view

1. Define a versioned server payload with only public fields.
2. Add a small renderer or focused module.
3. Register the view type in `message.js`.
4. Add it to response renderability when provider-loop termination requires it.
5. Use DOM properties, validated IDs, and controlled navigation; never model HTML.
6. Add integration coverage and a manual accessibility/browser check.

`message.js` remains the largest renderer module because it owns many form/card families. Prefer a
new focused view module when adding another substantial family rather than expanding unrelated
conditionals.
