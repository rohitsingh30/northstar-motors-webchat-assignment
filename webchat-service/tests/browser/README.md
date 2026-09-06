# Browser-module tests

These Node ES-module tests verify widget utilities/renderers without a running browser or Compose.

| File | Coverage |
| --- | --- |
| `test_conversational_workflows.mjs` | protected-only collector, session isolation, public-routing invariants |
| `test_read_only_cards.mjs` | no anchors/buttons/forms/embedded actions in cards |
| `test_widget_state.mjs` | explicit lifecycle transitions |
| `test_business_information_card.mjs` | scoped fact views and compatibility payloads |
| `test_opening_hours_card.mjs` | regular/holiday hours rendering |
| `test_suggestions.mjs` | external quick replies and trusted typed actions |
| `test_progress_indicator.mjs` | honest indeterminate request progress |
| `test_recovery.mjs` | retry/failure classification |
| `test_scroll_isolation.mjs` | transcript scrolling without host-page movement |
| `test_workshop_booking_confirmation.mjs` | workshop review labels, local time, and hidden operational selectors |

Run all module tests with:

```bash
node --test webchat-service/tests/browser/*.mjs
```

They do not replace live-provider or real-browser release acceptance.
