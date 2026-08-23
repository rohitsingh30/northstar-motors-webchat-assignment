# Architecture diagrams

These diagrams describe the current `webchat-service` implementation. Each image has an editable
Mermaid source beside it.

| Diagram | Image | Editable source | Purpose |
| --- | --- | --- | --- |
| System architecture | [`webchat-system-architecture.svg`](./webchat-system-architecture.svg) | [`webchat-system-architecture.mmd`](./webchat-system-architecture.mmd) | Containers, service modules, external dependencies, and trust boundaries |
| AI turn orchestration | [`ai-turn-orchestration.svg`](./ai-turn-orchestration.svg) | [`ai-turn-orchestration.mmd`](./ai-turn-orchestration.mmd) | Linear provider-first flow from V2 domain-goal plan through semantic policy, exact tools, and persistence |
| Write-workflow safety | [`write-workflow-lifecycle.svg`](./write-workflow-lifecycle.svg) | [`write-workflow-lifecycle.mmd`](./write-workflow-lifecycle.mmd) | Draft, confirmation, idempotency, receipts, and verified booking management |

The SVG files are the documentation assets embedded by the HLD and LLD. Update both the Mermaid
source and SVG whenever a boundary or flow changes.

## Diagram design language

| Visual treatment | Meaning |
| --- | --- |
| Blue | Browser/API request and coordination boundary |
| Purple | Semantic or probabilistic understanding |
| Teal | Validated deterministic application execution |
| Amber | Workflow mutation or authoritative business boundary |
| Green | Persisted successful outcome |
| Red | Cancelled/final failure path |
| Dashed container | Trust or deployment boundary |
| Dashed arrow | Optional, feedback, or non-terminal loop |

## Readability rules

- Give each diagram one question: system ownership, turn execution, or write safety.
- Read primary flow left-to-right or top-to-bottom; use labeled return/exception arrows sparingly.
- Put trust-boundary labels on the container, not on every internal box.
- Use component names from the code so readers can navigate directly from diagram to package.
- Distinguish model decisions from deterministic application decisions with color and wording.
- Show limits and known gaps as footnotes rather than hiding them.
- Prefer two or three short lines per node; move detailed rules into HLD/LLD prose.
- Keep the SVG accessible with `<title>`, `<desc>`, strong contrast, and text that remains legible
  when the image is scaled to documentation width.

## Updating a diagram

1. Change the adjacent `.mmd` source first so the logical graph remains easy to review.
2. Update the SVG using the same node names and semantic colors.
3. Validate SVG XML and visually check the full aspect ratio.
4. Check every HLD/LLD image link.
5. Update the relevant folder README when ownership or a module name changed.
