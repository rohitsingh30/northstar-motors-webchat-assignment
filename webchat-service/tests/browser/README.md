# Browser-module tests

These lightweight Node tests verify isolated ES-module behavior without requiring a full browser
automation dependency.

## Files

| File | Coverage |
| --- | --- |
| [`test_business_information_card.mjs`](./test_business_information_card.mjs) | Scoped fact-only business cards and restored version 1 payload compatibility |
| [`test_confirmation_card.mjs`](./test_confirmation_card.mjs) | Sales-enquiry confirmation copy, safe display summary, and confirm/cancel action data |
| [`test_form_profile.mjs`](./test_form_profile.mjs) | Ordinary form values persist/prefill while private booking lookup proof is excluded |
| [`test_recovery.mjs`](./test_recovery.mjs) | Slot/vehicle/field recovery classification and definitive versus indeterminate turn retries |

Run from the repository root:

```bash
node --test webchat-service/tests/browser/*.mjs
```

These tests complement, but do not replace, manual accessibility, layout, and end-to-end browser
journey verification.
