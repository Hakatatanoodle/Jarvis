# Jarvis Architecture v1.0 — Critical Architecture Review

**Reviewer stance:** Principal Software Architect, pre-implementation review. Assumes a solo/small-team, multi-year build. This review is intentionally adversarial. If a section doesn't hurt a little, it isn't doing its job.

---

## 1. Critical Issues

These are the problems that will actively hurt you if you start coding tomorrow.

### C1. Reflection and Review are the same subsystem wearing two name tags
RV-01 asserts "Reflection improves Jarvis, Reviews improve the partnership" — but look at the actual specs. RE-06 ("Evidence First") and RV-04 ("Evidence-Based Reviews") are the same rule. RE-10 and REV-01 are the same honesty policy, almost word-for-word. RV-03's inputs (Goals, Memory, Reflection logs, Learning Engine, Action history, Planner, Calendar, Knowledge graph) are a superset of what Reflection already consumes and produces. The only real difference is *cadence and audience framing* — Reflection is continuous/system-triggered, Review is scheduled/user-facing. That's a UI/scheduling distinction, not an architectural one.

**Fix:** Merge into one **Insight Engine** with two output modes (internal proposals vs. user-facing narrative reports) rather than two subsystems with duplicated evidence pipelines, duplicated honesty policies, and duplicated archives.

### C2. Permission checking happens in at least three places with no single source of truth
- Planner (PL-10): "if the necessary permissions exist, Jarvis executes the plan" — planner is checking permissions.
- Orchestrator workflow (OR-02): "Permission Check" is an explicit pipeline stage between Decision Engine and Tool Execution.
- Action Engine / Permission System itself (PS-01): "every action with side effects must pass through it before execution."

Three subsystems all believe they're the permission gate. In practice this means either (a) permission logic gets duplicated and drifts out of sync, or (b) two of the three checks are actually just optimistic guesses that get re-validated later, which contradicts PL-10's implication that the Planner *knows* whether it can execute.

**Fix:** Exactly one gate. Recommendation: Permission System is a **stateless query service** (`checkPermission(capability, params) → allow/deny/confirm`) called only by the Orchestrator's workflow stage, right before Action Engine dispatch. Planner should never claim to "know" permission state — it should mark a plan step as `requires_permission: <capability>` and defer the actual check.

### C3. No concrete data contracts anywhere
Every subsystem doc describes *responsibilities* in prose but none define an actual schema. What does a "Decision" object look like as data? What fields does a "Task" the Planner hands to the Orchestrator actually have? What does "Context" as passed from Context Engine to Decision Engine literally contain (list of objects? ranked? with what metadata)? Section 4 of the review brief asks for exactly this and the source documents don't answer it once.

This isn't a nitpick — it's the difference between an architecture and a mood board. Without contracts, every subsystem boundary in this document is aspirational, and a solo developer will end up improvising the interfaces at 11pm and never coming back to formalize them.

**Fix:** Before writing any subsystem code, write the JSON schemas for: `Memory`, `Goal`, `Decision`, `PlanStep`, `CapabilityResult`, `ContextItem`, `PermissionCheckResult`. This should be a single shared schema file every subsystem imports. Treat it as more locked than any of the "✅ LOCKED" prose decisions.

### C4. Two engines can write to the same user-facing state with no arbitration
Reflection "may propose updates to Memory, User Model, Recommendations, Habits, Workflows" (RE-04). Learning Engine independently updates confidence scores on behavioral patterns that live in largely the same space (productivity, habits, preferences — LE-07). Both can run asynchronously (RE-02), both write to overlapping targets, and nothing in either doc says what happens when Reflection concludes "user is becoming a night owl" the same week Learning concludes the opposite from a different signal. Memory versioning (M-03) preserves history but doesn't resolve *simultaneous conflicting proposals* — it just means you'll have two contradictory versions logged instead of one.

**Fix:** Learning Engine should be a **data source that Reflection/Insight Engine reads from**, not a peer writer to the same memory space. Only one subsystem should have write access to User Model / long-term Memory; everything else proposes, and the writer arbitrates.

### C5. The Orchestrator is a god object
Read OR-01 through OR-10 again as a single list: scheduling, workflow management, resource allocation, LLM selection + fallback + routing, failure handling/retry, parallel execution, being the root of the entire event-driven architecture, session state, and observability. Then INF-V1 adds "continuously evaluate LLM performance and route adaptively" onto the same component. This is not one subsystem's responsibility — it's the responsibility of the entire runtime, given a single name.

For a solo developer, this means the single hardest, most central piece of code in the whole system is also the least scoped. Every other subsystem is comfortably small and single-purpose (Permission, Memory, Capability) except the one that everything else depends on.

**Fix:** Split into: (1) **Workflow Orchestrator** (sequencing, retries, parallelism — the actual "coordinator"), and (2) **Model Router** (LLM selection, fallback, cost/performance tracking) as a separate service the Orchestrator calls through, matching what INF-05's `Reason()` abstraction already implies. Session/observability can be a thin cross-cutting layer, not Orchestrator-owned logic.

---

## 2. Major Improvements

### M1. Context Engine / Decision Engine split is real but under-specified at the boundary
CE-04 says the Context Engine "never attempts to determine importance," and CE-05 says the Decision Engine ranks candidates. Fine in principle. But CE-03's Retrieval Policies already encode a lot of implicit importance judgment (why does "Programming" intent pull "coding preferences" but not "calendar"? that's a ranking decision disguised as a retrieval policy). The boundary is fuzzier than the document admits. In practice you'll end up putting relevance heuristics in the retrieval policy anyway, which quietly violates CE-04.

**Fix:** Accept that retrieval policies *are* a coarse first-pass ranking, and say so explicitly, or fold Context Engine into Decision Engine's input stage for V0 and only split them out once you have enough retrieval policies that the separation earns its complexity (see MVP section).

### M2. Capability Composition (CAP-08) duplicates Orchestrator's job
"Capabilities can call other capabilities" and the example (`Organize Week → Read Calendar → Read Tasks → Create Events → Notify User`) is literally a workflow — the exact thing the Orchestrator exists to sequence. If composite capabilities can internally orchestrate, you now have two orchestration engines: the real one, and whatever ad hoc control flow lives inside composite capabilities. This will cause confusing failure handling (which layer retries? which layer reports progress?) and makes AE-03's clean action lifecycle harder to guarantee, since a "capability" might silently be five actions deep.

**Fix:** Composite capabilities should compile down to a **plan** that the Orchestrator/Action Engine executes as normal steps, not to code that calls other capabilities directly. Keeps AE-03/AE-07/AE-10 (lifecycle, progress, standardized results) true for every action regardless of whether it originated from a primitive or composite capability.

### M3. "Risk level" is a load-bearing concept with no owner
AV-01's confirmation philosophy ("confirmation is determined by risk level, not workflow size") is the best design decision in the whole document — genuinely good UX thinking. But *who computes risk*? CAP-07 says capabilities store a "Risk level" as metadata, which is a start, but real risk is often *contextual* (sending an email to your professor is low risk; sending an email to your entire mailing list is high risk, same capability). Static per-capability risk metadata won't capture that.

**Fix:** Risk level should be a function of `(capability risk tier, action parameters, reversibility, blast radius)` computed at Permission-check time, not a static field. Document this as part of the Permission System's contract (C3 above), not Capability's.

### M4. Knowledge → Memory pipeline (KS-05) has no trigger definition
"Reading dozens of AI papers over months → Jarvis infers 'AI Systems has become one of Yochan's primary interests.'" Lovely example, zero mechanism. What triggers this inference — a scheduled Reflection pass over Knowledge? A count threshold? Who owns this: Knowledge subsystem, Reflection, or Learning ("Strategy Learning"/"User Learning" categories overlap here too)? Right now three different subsystems could plausibly claim this job and none of them explicitly do.

**Fix:** Assign explicitly: Knowledge → Memory promotion happens *only* inside the merged Insight Engine (see C1), triggered on the same schedule as reflections, so there's one place that reads Knowledge access patterns and proposes Memory items.

### M5. Undo (AV-02) assumes semantic action resolution that isn't designed anywhere
"Undo the calendar change," "revert the last file rename," "roll back yesterday's schedule" — this requires natural-language reference resolution against the Action Log, plus a defined reversal operation *per capability*, plus handling partial reversibility (what if three of five actions in a batch are undoable and two aren't?). CAP-03 has a "Reversible? (Yes/No)" field, which is good, but binary reversibility can't express "reversible for 10 minutes, then not" (e.g., a git push before someone else pulls). This is a meaningfully hard NLP + state-machine problem being described in five bullet points.

**Fix:** For V0, undo should be explicit and scoped — "undo my last action" referencing Action Engine's own IDs (AE-04), not fuzzy natural-language resolution across arbitrary time windows. Defer conversational undo resolution to a later version.

---

## 3. Minor Improvements

- **UM (User Model) has no owning subsystem.** It's referenced constantly (DE-03, CE-07) but no document says who computes/updates it, only that it's "generated from Memory, Goals, Knowledge, Observations, User feedback" and "updates automatically." Give it an explicit owner (recommend: folded into Memory subsystem as a derived/materialized view, not a peer subsystem).
- **Goals.md is duplicated in full inside the same file** (G-01 through G-06 appears twice, verbatim). Harmless as a document, but worth flagging since it suggests the source docs were pasted together without a final consistency pass — a preview of what will happen to code comments and specs over a multi-year solo project without discipline.
- **Confidence score scale is never defined.** LE-03 shows "93%" — is this consistent across Learning, User Model (UM-05), and Decision confidence (DE-07)? If three subsystems each invent their own confidence semantics, "why do you think this" answers (LE-08, UM-08, DE-08) will be incomparable across subsystems.
- **INF-08 "Local + Cloud Models"** and INF-V1 "Adaptive Model Routing" imply Jarvis benchmarks local models against cloud models on the same tasks to build routing confidence — that's a nontrivial eval harness, understated as a bullet point.
- **CE-10 Context Budget** assigned by the Orchestrator, but consumed/enforced by Decision Engine — another cross-boundary responsibility that should be in the shared contract file (C3), not implied by two separate docs.

---

## 4. Suggested Architecture Changes

1. **Merge Reflection + Review → Insight Engine** (internal proposal mode + user-facing narrative mode). Removes C1.
2. **Single Permission gate**, called only from the Orchestrator's workflow stage. Planner marks steps as requiring permission; it does not check permission itself. Removes C2.
3. **Formal shared schema file** for Memory, Goal, Decision, PlanStep, CapabilityResult, ContextItem, PermissionCheckResult — versioned like everything else in Infrastructure. Removes C3.
4. **Single writer to User Model / long-term Memory.** Learning Engine becomes a read/propose-only signal source into the Insight Engine's write path. Removes C4.
5. **Split Orchestrator into Workflow Orchestrator + Model Router.** Removes C5.
6. **Composite Capabilities compile to Plans**, executed by the same Action Engine pipeline as everything else — no capability-to-capability direct calls. Removes M2.
7. **Risk scoring moves into the Permission System as a computed function**, not static Capability metadata. Removes M3.
8. **Explicitly own the Knowledge→Memory promotion step inside Insight Engine.** Removes M4.
9. **User Model becomes a derived view inside Memory**, not an independent subsystem with its own doc-level authority.
10. **Context Engine and Decision Engine merge for V0**, split later only if retrieval policies outgrow a single module (see MVP).

---

## 5. Recommended Version 0 Plan

### Dependency reality check
The stated chain (Constitution → Goals → Memory/Knowledge → Context → Decision → Planner → Orchestrator → Capabilities → Actions) is directionally right but omits User Model (used by Decision/Context but architecturally homeless) and treats Permission/Reflection/Learning as loosely-coupled "supporting systems" when in practice Permission is *in* the critical execution path (every action passes through it) and Reflection/Learning both read from and write back into Memory — making the "clean top-down chain" actually a graph with a feedback loop from Reflection/Learning back up into Memory/Goals. That feedback loop should be drawn explicitly, not hidden as a footnote — it's one of the most important properties of the system (Jarvis literally rewrites its own inputs over time) and the diagram currently makes it look acyclic.

### MVP — Jarvis Version 0

**Must have:**
- Constitution (static config file, not a "subsystem")
- Goals (hierarchical, versioned — no graph-of-multiple-parents yet, just tree + tags for cross-cutting support)
- Memory (Archive = flat append-only log; Memory = rule-based creation only, AI-based memory proposal deferred)
- Context + Decision merged into a single **Reasoning Engine**: intent classification → retrieval → LLM call → recommendation, with confidence and one-sentence evidence attached
- Planner: flat task lists + one level of decomposition (no dependency graph yet)
- Single Workflow Orchestrator (sequencing + retry only — no adaptive multi-LLM routing, one provider + one fallback)
- Permission System: binary per-capability allow/deny/confirm, no profiles, no temporary permissions, no risk scoring function yet — just a static risk tag per capability
- Action Engine: lifecycle states, logging, no composite capabilities yet
- 3–5 real capabilities (calendar, file ops, one messaging channel) with the CAP-03 contract enforced from day one
- Unified logging (INF-21)

**Should have (fast follow, still V0.x):**
- AI-based memory proposals with confidence scores
- Reflection (merged Insight Engine, single scheduled pass — weekly only, no full multi-timescale system yet)
- Permission profiles + temporary permissions
- Capability composition (compiled to plans, per M2 fix)
- Basic undo, scoped to Action Engine IDs only (no NL resolution)

**Future (explicitly do not build in V0):**
- Knowledge graph / vector search over knowledge (start with tagged flat storage; add pgvector once retrieval quality actually becomes a bottleneck, not preemptively)
- Adaptive multi-LLM performance routing (INF-V1) — hardcode routing rules first, add metrics-driven routing once you have months of logs to route on
- Mobile companion app (INF-V3)
- Public API (INF-V4)
- Capability marketplace (CV-03)
- Relationship Learning (humor tolerance, familiarity modeling) — technically the fuzziest, lowest-evidence category in the whole doc; revisit after User/Strategy learning are validated
- Event-driven multi-source ingestion (email/filesystem/sensors) — start with calendar + manual triggers only; each new event source is a real integration project, not a bullet point

---

## Final Verdict

**Architecture quality: 6/10.** The philosophy is genuinely good — user authority, evidence-based honesty, deny-by-default permissions, risk-based confirmation, non-destructive memory versioning. These are well-reasoned product principles. But the document confuses *naming a lot of subsystems* with *having a decomposed architecture*. Several "separate subsystems" (Reflection/Review, parts of Context/Decision) are the same responsibility split for narrative reasons, and the most critical component (Orchestrator) is the least scoped.

**Buildability: 3/10 as written, 7/10 if the V0 plan above is followed.** As specified, this is a 15+ subsystem distributed system with a knowledge graph, adaptive multi-model routing, mobile companion, public API, and NL-driven undo — a multi-year team project being scoped as a solo-developer V0. Nothing here is impossible; it's just all being attempted at once.

**Biggest strengths:**
- The permission/confirmation philosophy (deny-by-default, risk-based confirmation, revocable, auditable) is close to production-grade thinking already.
- The insistence on evidence-based, non-shaming honesty in Reflection/Review is a genuinely differentiated design choice, not boilerplate.
- Memory-never-overwritten + versioning is the right call for a system meant to build long-term trust.

**Biggest weaknesses:**
- No data contracts anywhere — the whole system is described in responsibilities, never in schemas.
- Duplicated responsibility between Reflection/Review, and between the three places permission gets "checked."
- Orchestrator scope is unbounded.
- Scale/forgetting question (review objective 6) is essentially unanswered — "archive everything forever" with no compaction strategy will become a real problem in year two, not year ten.

### Top 10 changes required before implementation
1. Write the shared data schema file (Memory, Goal, Decision, PlanStep, CapabilityResult, ContextItem, PermissionCheckResult).
2. Merge Reflection + Review into one Insight Engine.
3. Make Permission System the single gate, called from one place only.
4. Split Orchestrator into Workflow Orchestrator + Model Router.
5. Assign a single writer for User Model / long-term Memory; make Learning Engine read-only into it.
6. Fold User Model into Memory as a derived view instead of a floating subsystem.
7. Compile composite capabilities to plans instead of letting them call each other directly.
8. Define a memory/archive compaction or summarization strategy before Archive grows unbounded.
9. Cut scope to the V0 "must have" list above — explicitly write down what's deferred so it doesn't creep back in silently.
10. Define confidence-score semantics once, shared across Learning/User Model/Decision Engine.

**Would I approve starting development? Conditional yes.** Not on the document as written — building this literally as fifteen separate subsystems will produce a solo developer stuck in integration hell within a few months. But the underlying philosophy is sound enough, and the fixes above are consolidation, not redesign. Cut it down to the V0 plan, write the schema file first, and this is buildable. Skip that step and it isn't.
