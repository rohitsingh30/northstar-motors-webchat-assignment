# Proposal and review contracts

This package contains only hosted semantic policies and the independent review contract. Concrete
tool definitions belong to `../catalogue/`; workflow state belongs to `../state.py`.

| File | Responsibility |
| --- | --- |
| `__init__.py` | Public exports |
| `prompt.py` | Planner and reviewer system policies; inject retrieved evidence |
| `review.py` | Outcome-specific review functions, citations, deterministic revalidation, safe fallback |

The planner must call a retrieved business tool, `answer_from_knowledge`, `respond_socially`, or a
pending-interaction accept/decline capability exposed only when an actionable interaction exists.
Knowledge answers are materialized from selected customer evidence, while social replies come from
fixed application copy; the planner cannot author arbitrary customer text or clarification. The
reviewer is a separate stateless provider request with a different prompt, envelope, and four small
outcome-specific functions. It sees the complete executable catalogue and may correct a missed
first-pass candidate.

Rules:

- No hosted proposal executes without `ProposalReview` provenance.
- Corrected calls pass through catalogue schema and risk validation again.
- Text citations must refer to retrieved customer-audience evidence; the application owns the text.
- Reviewer clarification must identify exact required schema fields or declared preconditions.
- Finite clarification choices are explicit structured options; selecting one starts another fully
  reviewed turn and never bypasses tool policy.
- Interaction decisions require persisted metadata from the immediately preceding assistant
  response. They contain no action arguments; the server resolves only the stored typed action.
- The reviewer cannot execute tools or confirm operations.
- Invalid review output receives precise deterministic feedback and one repair attempt. When a
  clarification cites optional input or an undeclared precondition, the repair request exposes only
  the correction branch, preventing the reviewer from repeating or relabelling the invalid outcome.
- Failure to obtain a valid review fails the turn with retryable `LLM_REVIEW_FAILED`; it is never
  persisted as a successful customer clarification.
