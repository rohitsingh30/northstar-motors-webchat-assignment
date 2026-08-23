# Browser-module tests

These lightweight Node tests verify isolated ES-module behavior without requiring a full browser
automation dependency.

## Files

| File | Coverage |
| --- | --- |
| [`test_business_information_card.mjs`](./test_business_information_card.mjs) | Scoped fact-only business cards and restored version 1 payload compatibility |
| [`test_confirmation_card.mjs`](./test_confirmation_card.mjs) | Sales-enquiry confirmation copy, safe display summary, and confirm/cancel action data |
| [`test_form_profile.mjs`](./test_form_profile.mjs) | Ordinary form values persist/prefill while private booking lookup proof is excluded |
| [`test_offer_card.mjs`](./test_offer_card.mjs) | Offer actions, inline enquiry Cancel control, and open/collapsed Send/View enquiry state transitions |
| [`test_opening_hours_card.mjs`](./test_opening_hours_card.mjs) | Holiday-only opening-hours cards without unrelated weekday schedules |
| [`test_progress_indicator.mjs`](./test_progress_indicator.mjs) | Honest indeterminate request progress without fabricated percentages |
| [`test_recovery.mjs`](./test_recovery.mjs) | Slot/vehicle/field recovery classification and definitive versus indeterminate turn retries |
| [`test_suggestions.mjs`](./test_suggestions.mjs) | Two-or-four follow-up rendering, complete service choices, active filters, and typed actions |
| [`test_test_drive_toggle.mjs`](./test_test_drive_toggle.mjs) | Booked test-drive disclosure labelling, controls, and expanded/collapsed toggle states |
| [`test_workshop_booking_card.mjs`](./test_workshop_booking_card.mjs) | Confirmed workshop booking hierarchy, structured details, and retained actions |

Run from the repository root:

```bash
node --test webchat-service/tests/browser/*.mjs
```

These tests complement, but do not replace, manual accessibility, layout, and end-to-end browser
journey verification.
