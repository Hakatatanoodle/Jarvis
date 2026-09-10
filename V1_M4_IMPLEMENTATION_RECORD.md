# V1-M4 Implementation Record — Natural-Language Capability Invocation (Foundation)

## What shipped
A natural-language pipeline that resolves a message like "touch my
Build Jarvis goal" to a real registered Capability + validated
parameters, and dispatches it through the existing, untouched
`check_permission()` — GRANTED executes immediately, CONFIRMATION_REQUIRED
asks y/N, DENIED is relayed. Wired into `conversation.api.handle()` as a
fourth routing flag, same shape M2's `state_change_possible` and M3's
`memory_candidate_possible` already established. Validated end-to-end
against `goals.advance` only, per the milestone brief — Calendar (M5)
and Filesystem (M6) are not touched.

## New files
- `contracts/capability_invocation_candidate.py` — flat dataclass,
  same "optional fields grow per-shape" pattern as `StateChangeCandidate`.
  Only `goal_ref`/`resolved_goal_id`/`match_count`/`note` are populated
  today; M5 adds datetimes, M6 adds a path, to the SAME dataclass.
- `capability_invocation/extraction.py` — LLM proposes `capability_id`
  + params as JSON; Python re-validates `capability_id` against a live
  `list_capabilities()` snapshot (never trusted as given — a
  hallucinated id is silently dropped, not surfaced as an error).
  Goal-reference resolution reuses `state_change.extraction.resolve_goal_ref`
  (promoted from `_resolve_goal_ref` — the one cross-file touch outside
  new files this milestone made).
- `capability_invocation/dispatch.py` — turns a validated candidate
  into a real Action and reuses `orchestrator.api._handle_action`
  verbatim for the GRANTED/CONFIRMATION_REQUIRED/DENIED branch. See
  "Decision+Plan synthesis" below for the one real architectural
  decision this milestone made.

## Decision+Plan synthesis (read this before M5/M6 touch this pipeline)
`action` has a hard FK to `plan`, which has a hard FK to `decision`
(migrations 0008/0009), so an Action cannot exist without both. This is
NOT solved by routing through the old intent/Decision/Plan system
(`detect_intent`/`reason()`/`planner.api.create_plan_from_decision`) —
that system's `create_plan_from_decision` is hardcoded to one-action-
per-goal-id and the placeholder `goals.advance` id, wrong shape for a
general capability + validated-parameters dispatch, and reintroducing
it here is exactly what M3's grounded-conversation redesign moved away
from. Instead, `capability_invocation/dispatch.py` writes the two
minimal rows those FKs require via direct SQL — the same trick
`conversation.api._persist_grounded_decision` already uses to write a
Decision outside `reasoning.reason()`. `intent="CapabilityInvocation"`
is a new free-text value on the same column `"Conversation"` already
uses (no CHECK constraint). Plan is inserted directly as `Approved`
(skips a separate `approve_plan()` round-trip) since this Decision is a
direct consequence of an explicit instruction, never Draft-reviewable —
same reasoning M2's Goal/Mission writes already apply by skipping Plan
entirely.

**If M5/M6 need multi-action dispatch from one message**, this
single-Action shape will need revisiting — not designed for it, and
extraction already only proposes one invocation per message (same
single-instruction reasoning `state_change/extraction.py` uses).

## Confirmation UX
`PendingCapabilityConfirmation` (sibling of `PendingStateChangeConfirmation`/
`PendingMemoryConfirmation`, same y/N shape in `cli.py`) — but unlike
those two, resolution goes through `orchestrator.api.resume_action`
directly rather than a bespoke confirm/execute path, since this
milestone's whole point is routing through Permission/Action Engine
(M2/M3 deliberately stayed off it; this is the exception).

`goals.advance` never exercises CONFIRMATION_REQUIRED for real (LOW
baseline + `ActionType.COMPUTE` + no undo = MEDIUM = always GRANTED,
per `permission/risk_calculator.py`) — tested against a synthetic
test-double capability instead
(`tests/test_capability_invocation_dispatch.py::fake_high_risk_capability`
fixture, LOW baseline + `ActionType.WRITE` + no undo = HIGH). That
fixture pops itself from `capabilities.registry._REGISTRY` on teardown —
left registered, it silently broke `test_m5_capabilities.py`'s exact-set
assertion on `list_capabilities()` (found live during this milestone's
own test run, fixed before shipping). **If M5/M6 add their own
synthetic-capability tests, use the same pop-on-teardown fixture
pattern — the registry has no reset between tests otherwise.**

## `conversation.api` changes
- `RouteDecision` gained `capability_invocation_possible` (4th field,
  same one-routing-call discipline as 2026-08-15b — fails OPEN to True
  on a malformed/unavailable routing call, same reasoning as the other
  two: a false silently drops a real command with no second chance).
- **Breaking for any external mock of `complete_json`'s routing
  return**: the all-or-nothing validation in `_route()` requires all
  four bool fields present in the same dict, or the WHOLE decision
  fails open (including `needs_reasoning`) — same all-or-nothing shape
  the 3-field version already had. Every existing test mock in
  `tests/test_conversation_api.py` (55 call sites) was updated to add
  `"capability_invocation_possible": False`. **M5/M6 will hit this
  again if they add a 5th flag — update every mock, don't assume a
  missing key defaults quietly.**
- `combined_grounding`/relay are now 3-way merges
  (memory/state-change/capability). The relay-note wording in both
  `_conversational_reply` and `_grounded_reply` was generalized from
  "Goal/Mission command" to "requested command" so it doesn't misdescribe
  a future calendar/filesystem relay.
- Re-checked `_asserts_wrong_status`/`_asserts_wrong_mission`/
  `_asserts_unsupported_relationship`/`_looks_garbled` against realistic
  capability-executed and capability-relay replies (per the standing
  "whatever evidence changes, re-check the guards" rule) — clean, no
  new false positives.

## Test coverage
`tests/test_capability_invocation_extraction.py` (7, pure unit, no DB/LLM),
`tests/test_capability_invocation_dispatch.py` (6, DB-backed: real
`goals.advance` GRANTED path, both parameter-validity ASK_CLARIFY
branches, synthetic-HIGH-risk CONFIRMATION_REQUIRED both approve/decline),
2 new full-turn tests in `tests/test_conversation_api.py` (executed-note
correctly framed as "Action just taken this turn"; a clarify relay
correctly kept out of that framing — same regression class as the
2026-08-26 dogfooding bug). Full suite: 388 passed, 0 failed, order-
independent (checked explicitly given the registry-leak issue found
above).

## Known gaps / explicitly out of scope (left for M5/M6)
- `_ACTION_TYPE_BY_CAPABILITY` mapping has exactly one entry
  (`goals.advance` -> COMPUTE). M5/M6 add their own; no inference rule
  exists because there was no second data point to generalize from.
- Parameter-validity clarify logic (ambiguous/unresolved `goal_ref`) is
  inlined directly in `dispatch.py`, not a separate policy module — only
  `goals.advance`'s one parameter shape needed it. A real per-capability
  validation module is worth it once M5/M6 add genuinely different
  shapes (datetimes, paths).
- This milestone extracts and dispatches AT MOST ONE capability
  invocation per message, matching `state_change/extraction.py`'s same
  choice — revisit only if real usage demands otherwise.
