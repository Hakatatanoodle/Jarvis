# Handover — Jarvis V1-M4: Natural-Language Capability Invocation (Foundation)

Paste this whole file as your first message in the new chat, and
upload the tar of the current repo alongside it.

---

## Where things actually stand

V1-M1, V1-M2, V1-M3 are done, tested, stable (read `ARCHITECTURE_ISSUES.md`
top to bottom, and each `V1_M*_IMPLEMENTATION_RECORD.md`). This
milestone was originally scoped as one big "wire up the whole Action
Engine" job, then split into three during planning discussion, once it
became clear Calendar (real Google integration, via MCP) and
Filesystem (real local navigation, bespoke) are different enough in
kind — different risk profile, different implementation approach, no
shared code below the Capability layer — that treating them as one
milestone was hiding two decisions as one. This is the split-off
shared piece: the plumbing both of them sit on top of. Read this
alongside `M5_HANDOVER_PROMPT.md` (Calendar/MCP) and
`M6_HANDOVER_PROMPT.md` (Filesystem) to see where each downstream piece
plugs in, but implement and ship this one first, on its own — M5 and
M6 both depend on it; it depends on neither.

**The subsystem this connects to already exists and is untouched by
this milestone.** `permission/api.py::check_permission()` does full
dynamic risk computation (GRANTED/CONFIRMATION_REQUIRED/DENIED),
already correct, already tested. `action_engine/api.py` has a real
Action lifecycle. `capabilities/registry.py` + `capabilities/primitives/*.py`
have real registered Capabilities — as of this writing, `file.read`,
`file.write` (sandboxed to one fixed folder — M6 replaces these),
`calendar.read_events`, `calendar.create_event` (local-only, a
`calendar_event` Postgres table — M5 replaces these), and
`goals.advance` (records a review-touch on a Goal — LOW risk, no
confirmation, no external dependency of any kind). None of this is
reachable via natural language, or any CLI command, today.
`conversation.api._capability_grounding()` explicitly tells the model
"None of these are available to you in this conversational reply."

---

## The job

Build the natural-language pipeline that takes a message like "touch
my Build Jarvis goal" or "mark that I reviewed the exam goal", resolves
it to a real registered Capability + validated parameters, and
dispatches it through the EXISTING `check_permission()` — executing
directly on GRANTED, confirming via y/N on CONFIRMATION_REQUIRED,
refusing on DENIED. Ship and validate this whole pipeline using ONLY
`goals.advance` — the one existing capability with no external
dependency, no confirmation requirement, and the lowest possible risk
if the extraction gets something wrong. Do not touch calendar or
filesystem capabilities in this milestone; that's M5 and M6.

---

## Do not design this from scratch — the shape has already been solved twice

Read `state_change/extraction.py` + `state_change/policy.py` (V1-M2)
before writing anything — direct template for structured extraction
(LLM proposes JSON, Python fully re-validates, fail-closed on anything
malformed) and the confirmation-UX shape
(`PendingStateChangeConfirmation` / `resolve_pending_state_change` /
cli.py's y/N loop — this milestone needs a `PendingCapabilityConfirmation`
sibling, not a third bespoke pattern).

**Unlike M2, you are not building the risk/policy layer** —
`check_permission()` already does that, correctly, tested. Your
dispatcher is closer to `orchestrator.api._handle_action()`'s existing
branching logic than to `state_change/policy.py::decide()` — read
`_handle_action()` as the direct template for what happens after you
have a validated Action, but build a NEW, thinner path to get there
that doesn't route through the old intent/Decision/Plan system
`_handle_action()`'s caller uses (that system is what V1-M3's grounded-
conversation redesign deliberately moved away from — reusing its outer
shape here would reintroduce exactly what M3 removed).

---

## The genuinely new part: parameter extraction, not just entity resolution

M2 only ever resolved one kind of reference — a Goal title — to an id.
A general Capability-invocation extractor needs to, per message:
1. Determine WHICH registered Capability is meant. Feed it the real
   `list_capabilities()` output (id/name/description) as context, same
   "give it the real current options" lesson from M2's known-goals
   list — never let it invent a capability id.
2. Extract that Capability's specific parameters. For `goals.advance`
   in this milestone, that's just a goal reference (reuse M2's exact
   entity-resolution approach unchanged) plus an optional free-text
   note. Design the extraction contract to be extensible per-capability
   from the start — M5 and M6 will each add a Capability with a
   completely different parameter shape (calendar needs datetimes;
   filesystem needs a path) — but implement and test against
   `goals.advance` ONLY here.
3. Fail closed / ask-clarify on anything missing or malformed, same
   discipline as M2's `_validate()`.

## Confirmation — mostly a non-question for this milestone specifically

`goals.advance` is LOW risk with no confirmation requirement, so
`check_permission()` will return GRANTED for it every time — this
milestone's dispatcher should still implement the full
GRANTED/CONFIRMATION_REQUIRED/DENIED branch, and should be tested
against a synthetic HIGH-risk fake capability too (a test double, not a
real one) to prove the confirmation path works, since neither of this
milestone's real capabilities will ever exercise it. Do not ship this
milestone having only ever tested the GRANTED branch for real.

---

## Guardrails already established in this codebase — carry these forward, learned the hard way across V1-M2's whole dogfooding run

- **Explicit only** — never infer "advance that goal" from ambient
  conversation, only from an actual instruction.
- **One routing call, not a new one.** Add a fourth flag
  (`capability_invocation_possible`) to the SAME `_route()` call that
  already returns three. This exact latency-regression mistake
  (ARCHITECTURE_ISSUES.md, 2026-08-15b) should not need discovering a
  fourth time.
- **Give the reply model the RIGHT labeled context, never a generic
  one.** The single most expensive lesson from V1-M2's dogfooding
  (five separate ARCHITECTURE_ISSUES.md entries, 2026-08-26 through
  2026-08-29): a completed action, a pending question, and a pending
  confirmation are three DIFFERENT things and must never share one
  framing. Read `_apply_state_change_policy`'s full docstring and
  `_conversational_reply`'s `relay_note` handling before building the
  equivalent here — this mistake was made three times in slightly
  different shapes in one week; a fourth repetition here would be a
  process failure, not a new discovery.
- **Whatever evidence the reasoning path gains, re-check the existing
  guards against it** (ARCHITECTURE_ISSUES.md, 2026-08-27 through
  2026-08-29 — three separate guard false-positives, each caused by an
  evidence fix elsewhere making a richer, more correct reply possible
  that an old guard wasn't built to expect). If this milestone adds
  anything to conversational evidence, re-test `_asserts_wrong_status`
  and `_asserts_wrong_mission` against realistic replies before
  assuming they're unaffected.
- **Audit trail** — Actions already have full lifecycle logging;
  verify NL-triggered ones are covered rather than assuming.

---

## Files to read, in order

`ARCHITECTURE_ISSUES.md` in full (2026-08-25 through 2026-08-29
especially), `V1_M2_IMPLEMENTATION_RECORD.md`, then code:
`state_change/extraction.py` + `state_change/policy.py` (direct
template), `permission/api.py` (reused untouched),
`action_engine/api.py`, `capabilities/registry.py` +
`capabilities/primitives/goal_ops.py` (the one real capability this
milestone touches), `orchestrator/api.py::_handle_action` (branching
template only, not the outer system), `conversation/api.py` (where the
new routing flag and wiring go).

## Before writing any code

Same discipline every milestone here has followed: write a concrete
plan — the extraction contract's exact shape (and how it stays
extensible for M5/M6's different parameter needs without them having
to redesign it), the new Pending-confirmation dataclass, and how the
synthetic-HIGH-risk test double will be built — and get it reviewed
before implementing.
