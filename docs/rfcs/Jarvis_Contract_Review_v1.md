# Jarvis Contract Review — Round 2

**Scope:** RFC-001 through RFC-025 (Mission, Goal, Memory, Knowledge/Edge, ContextItem, Decision, Plan, Capability, Action, Event, CapabilityResult, Workflow, Interface Contracts, Runtime Infrastructure).

**Verdict up front: not signed off yet.** This is genuinely strong work — the contract format itself (Owner / Created By / Lifecycle / Events Produced-Consumed / JSON Schema) is exactly the discipline I was asking for in Round 1, and a few things here are *better* than what I originally proposed. But there are real inconsistencies, one missing piece that's structurally load-bearing, and one omission from the "official" execution pipeline that directly contradicts a decision we just locked. None of these are redesigns — they're fixes to what's already written. Once addressed, I'd sign off.

---

## What's genuinely good (confirming this isn't just fault-finding)

- **RFC-014 (Interface Contracts)** is the single best addition in this batch. "Every subsystem exposes only four operations, no subsystem reaches into another's database" is exactly the enforcement mechanism the shared-contracts decision needed. This didn't exist in Round 1.
- **RFC-006 (ContextItem)** resolves the Context/Decision ranking ambiguity I flagged as M1 — cleanly, with a real Retrieve → Score → Rank → Budget Cutoff pipeline. (Flagged below as needing a decision update, not because it's wrong — because it now contradicts an older locked line that needs to be formally superseded.)
- **RFC-013 (Workflow) + RFC-019 (LLM Router)** as separate contracts is the Orchestrator split done correctly — Workflow Orchestrator owns sequencing, Model Router owns model selection, matching Decision #2 from Round 1.
- **RFC-024 (Persistence)** is a good, sharp line — persistent vs. temporary, and it correctly matches ContextItem's "never persisted" status. Cross-document consistency, done right.
- Immutability choices (Decision, ContextItem, Action-fields-other-than-status) are the right call and consistently applied.

---

## Blocking Issue 1 — The "official" execution pipeline drops the permission gate

RFC-016 defines itself as **"the only official execution pipeline"**:

```
User Request → Orchestrator → Context Engine → Decision Engine → Planner → Plan → Action Engine → Capability → Result → Events → Learning
```

There is no Permission Check step in it. None. Compare this to the original `orchestrator.md` workflow (OR-02), which explicitly had `Permission Check` as its own named stage between Decision Engine and Tool Execution — and compare it to Decision #3 from Round 1, which we both locked: **single permission gate, called from the Orchestrator's workflow stage, right before dispatch.**

This matters more than a typo because RFC-016 explicitly claims sole authority over the execution flow. If a solo developer implements against this diagram literally, the permission gate doesn't exist in code, because it isn't in the one document that says it's the complete picture.

**Fix:** Add `Permission Check` back into RFC-016 explicitly, between Planner/Plan and Action Engine (matching where Action objects get authorized before moving Pending → Running). This is a five-minute fix, but it's non-negotiable before implementation starts.

## Blocking Issue 2 — No PermissionCheckResult contract exists

Capability (RFC-010) has a `risk_level` field, labeled in its own description as a **"Dynamic baseline"** — but the JSON example shows it as a static string (`"risk_level": "Medium"`) stored once, on the Capability itself, not computed per-action. That's exactly the flaw Decision #6 was written to fix: risk was supposed to become `f(capability, parameters, impact, reversibility)`, computed fresh at check-time, not cached on the capability.

Right now, "dynamic" is a word in a field description, not a mechanism. There's no contract anywhere in this batch — no `PermissionCheckResult`, no risk-computation object — that actually implements the formula. Action (RFC-009) doesn't even reference a permission check: no `permission_check_id`, no `computed_risk`, nothing linking an Action back to *why* it was allowed to run. That's also a direct gap against the original Permission System requirement that every autonomous action's log include "the permission used" (PS-05).

**Fix:** This needs to be RFC-026 before anything else gets built. Minimum shape:
```json
{
  "id": "permcheck-uuid",
  "action_id": "action-123",
  "capability_id": "calendar.create_event",
  "baseline_risk": "Medium",
  "computed_risk": "Low",
  "risk_factors": { "impact": "single_event", "reversible": true, "scope": "self" },
  "permission_status": "granted",
  "confirmation_required": false,
  "decided_at": "..."
}
```
And rename Capability's `risk_level` to `baseline_risk_level` so it's honest about being one input to the formula, not the formula's output.

## Blocking Issue 3 — Round-1 decisions aren't propagated through the contract text

Two of our locked decisions are only *partially* reflected:

**(a) Insight Engine merge (Decision #7).** `goals_contract.md`, `decision.md`, `plan.md`, and `action.md` still list **"Review System"** as an independent reader/creator, separately from "Insight Engine" — sometimes in the same document (`goals_contract.md`'s Created By says "Review System," its Read By says "Insight Engine"). If the merge is real, every one of these references should say `Insight Engine (review mode)`, consistently, everywhere. Right now the documents read as if the merge didn't fully happen.

**(b) Goal graph → tree (silent, not decided).** Original `Goals.md` (G-02, ✅ LOCKED) explicitly allowed a goal to "support multiple parent goals" — the Exercise-supports-Health/Basketball/Wellbeing example. The new `goals_contract.md` changed this to `parent_goal_id: UUID | null` (single parent) plus a separate `dependencies` array, which is a materially different data model (a tree with cross-links, not a graph with true multi-parent support). I think this is *the right call for V0* — it matches my own MVP recommendation to simplify the goal graph. But it was never actually re-negotiated as a decision; it just quietly appeared in the contract. Given it overturns something we marked ✅ LOCKED, it should get one sentence of explicit sign-off from both of you, not silent implementation.

## Blocking Issue 4 — Composite Capabilities aren't represented in the Capability contract

Decision #4 said composite capabilities compile down to Plans instead of calling other capabilities directly — specifically to prevent a second orchestration engine hiding inside the Capability layer. `capability.md` as written has no `type: primitive | composite` field and no way to express "this capability expands into a plan." As written, there's nothing in the schema that would *prevent* someone from building a composite capability that calls other capabilities directly — the fix isn't encoded, just the intent.

**Fix:** Add `capability_type: "primitive" | "composite"` and, for composite, a `compiles_to_plan_template: <plan_template_id>` field, so the Planner — not the Capability itself — is what expands it at execution time.

---

## Notable inconsistency: state machines don't match their own source of truth

RFC-015 exists specifically to be the canonical state list ("every subsystem has explicit states... every transition is validated"). It's already out of sync with the individual contracts that were built after it — in every single case, the individual contract is *richer* than RFC-015, which means RFC-015 is stale, not wrong exactly, but no longer authoritative:

| Entity | RFC-015 says | Individual contract says |
|---|---|---|
| Goal | Draft → Active → Paused → Completed → Archived (linear) | Same, **plus** a separate Active → Cancelled branch, and `status` enum includes `Cancelled` |
| Plan | Draft → Approved → Executing → Completed | Same, **plus** Executing → Paused, Executing → Cancelled |
| Action | Pending → Running → Completed/Failed | Same, **plus** a `Ready` state before Running, **plus** Cancelled |
| Memory | Extracted → Verified → Active → Archived | Adds `Observed` before Extracted and `Updated` after Active — **and** its own `status` field only defines `Active/Archived/Forgotten`, which doesn't even cover the lifecycle states in the same document |
| Capability | Registered → Enabled → Deprecated | Adds a fourth state, `Removed` |

This isn't a huge deal individually, but it's a preview of exactly the failure mode Decision #1 (shared contracts) was meant to prevent — two documents, both claiming to define the same thing, already drifting apart in the very first implementation pass. Memory's internal contradiction (lifecycle states vs. its own status enum not matching) is the sharpest version of this and should be fixed first.

**Fix:** RFC-015 should stop listing state machines itself and instead say "see each contract's Lifecycle section" — one source of truth per entity, not two.

---

## Smaller things worth a look, not blockers

- **Decision's `requires_confirmation` is frozen at Decision-creation time**, but Decision is immutable and created *before* Planning/Action finalize concrete parameters (e.g., which email, which recipient). Risk — and therefore confirmation — should be evaluated against final parameters at Action-time via the Permission Gate, not guessed early and locked into an object that can never be updated. Recommend Decision keeps `requires_confirmation` as an *early estimate* only, and the authoritative answer lives on the (still-missing) PermissionCheckResult.
- **RFC-021 Configuration** introduces "profiles" (Locked In / Creative / Sports / Study / Travel) that look like context/mode profiles, while `Permission.md`'s PS-06 already defined "profiles" (Conservative / Balanced / Autonomous) for permission posture. Same word, two different concepts, no stated relationship. Worth one clarifying sentence: are these the same axis or orthogonal?
- **RFC-019 LLM Router's `Max Tokens` output** and Context Engine's Context Budget (CE-10 / the ContextItem ranking pipeline) aren't explicitly linked. Right now nothing says the budget Context Engine computed is what constrains the Router's `max_tokens` — worth one line making that dependency explicit so they can't silently disagree.
- **Learning Engine's write path into Memory is still contradictory.** `memory_contract.md` lists "Learning Engine" directly under **Created By** — but separately lists `LearningAccepted` under **Events Consumed**, which implies Memory System creates the memory only *after* something accepts a Learning proposal. Those two statements disagree with each other in the same document, and the first one (direct creation) is also a straight violation of Decision #4/#5 (Learning proposes, doesn't write). Fix: remove "Learning Engine" from Created By entirely; Memory System is the only creator, triggered by the `LearningAccepted` event.

---

## Missing contracts (expected, since you said "not all" — listing so nothing gets forgotten)

- **PermissionCheckResult** — blocking (see Issue 2)
- **User Model** — even as a derived/materialized view (Decision #5), it needs its own short contract stating it has no independent write path and is recomputed from Memory events
- **Learning record / proposal schema** — referenced constantly (`LearningAccepted`, `LearningRecommendationAccepted`) but never defined
- **Insight Engine output schema** (Reflection-mode and Review-mode payloads) — needed to actually implement Decision #7

---

## Sign-off status

**Not yet — four blocking items, all fixable without touching the design you and your architect actually agreed on:**

1. Put Permission Check back into RFC-016's pipeline.
2. Write the PermissionCheckResult contract; rename Capability's `risk_level` → `baseline_risk_level`.
3. Propagate the Insight Engine merge and the goal-tree simplification consistently through every contract (or explicitly re-open the goal-tree one if your architect wants the multi-parent graph back for V0).
4. Add `capability_type` (primitive/composite) so composite capabilities can't quietly become a second orchestrator.

None of these require new design meetings — they're consistency and completeness fixes on decisions you've already made. Once those land (plus the missing PermissionCheckResult/User Model/Learning contracts, at least as stubs), send it back and I'll do a fast pass — I'd expect to sign off on that round.
