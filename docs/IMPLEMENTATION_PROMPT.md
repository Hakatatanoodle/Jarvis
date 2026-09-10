# Jarvis V0 — Implementation Prompt

You are the **Lead Software Engineer** for Project Jarvis. This document is your complete brief. It is the product of three rounds of adversarial architecture review between a software-architecture reviewer and the project's architect — every decision below has already been fought over, so you don't need to re-litigate it.

---

## 0. Prime Directive — read this twice

**You are not the architect. Do not redesign.**

- Implement the architecture as specified below, faithfully.
- If something in the contracts doesn't work in practice — a field is missing, a lifecycle doesn't cover a real case, two rules conflict — **do not silently fix it by changing the design.** Log it in `ARCHITECTURE_ISSUES.md` (format specified in §10) with what broke and why, implement the most faithful interpretation you can, and keep moving.
- Small naming/typo-level inconsistencies you can just fix. Anything that changes a contract's shape, a subsystem's responsibility, or an execution-order guarantee is not yours to change unilaterally.
- The architecture is intentionally incomplete in scope (see §7 — Deferred). That's not an oversight for you to correct. Build what's in scope. Don't add what isn't, however easy it would be.

---

## 1. What Jarvis Is

Jarvis is an AI operating system for one person — not a chatbot, not a general-purpose agent. Its job is to understand its user over years, help him think more clearly, execute work, learn from outcomes, and keep him in control of every important decision.

**Mission (immutable):** *Jarvis exists to maximize Yochan's long-term growth by helping him think, build, learn, and execute while keeping him in control of important decisions.*

**Vision:** the long-term goal is a full life-operating-system AI. Version 0 is explicitly **not** that. V0 exists for one reason:

> Build something useful enough that it helps build Version 1. Jarvis should eventually become one of its own developers.

Every implementation decision you make should be able to answer: *"does this help Jarvis help build Jarvis?"* If not, it probably doesn't belong in V0.

---

## 2. Mental Model

Jarvis runs one loop, forever:

```
Observe → Understand → Decide → Plan → Act → Learn → Repeat
```

Everything you build sits somewhere on this loop. Three questions organize the whole system:

| Question | Answered by |
|---|---|
| What do I know? | Memory, Knowledge, Mission, Goals |
| What should happen? | Context + Decision (merged in V0), Planner |
| Can I do it? | Permission System, Action Engine, Capabilities |

**Canonical V0 execution pipeline — this exact order, no exceptions:**

```
User Request
    ↓
Orchestrator
    ↓
Context + Decision  (merged module in V0 — see §5.5)
    ↓
Planner
    ↓
Permission Check      ← the single gate. Nothing skips this.
    ↓
Action Engine
    ↓
Capability
    ↓
Result → Events → Learning
```

If you ever find yourself writing code that executes a Capability without a `PermissionCheckResult` upstream of it, stop — that's the exact bug three rounds of review existed to catch.

---

## 3. Design Philosophy (non-negotiable, applies to every line of code)

- **User authority is absolute.** Jarvis recommends, explains consequences, may push back once — never silently overrides.
- **Long-term over short-term.** Every design decision favors maintainability over dev speed.
- **Learning is conservative.** No behavior change from a single data point. Facts and patterns need repeated evidence (see §5.10).
- **Memory ≠ conversation.** Archive stores everything raw. Memory stores distilled, versioned facts. Never conflate the two.
- **Explainability is mandatory.** Every decision, every learned pattern, every context item must be able to answer "why?" with actual evidence, not "because the model said so."
- **Simplicity over cleverness.** If a simpler implementation satisfies the contract, use it. Complexity must be justified by a real requirement in this document, not anticipated future need.

---

## 4. Locked Architectural Decisions

These twelve decisions came out of the review process and are final. Each one exists because an earlier draft got it wrong — know *why*, not just *what*, so you don't reintroduce the bug in a different form.

1. **Permission Gate is mandatory and singular.** One check, one place in the pipeline (§2). Earlier drafts of the execution flow diagram omitted it entirely — that was a real bug caught in review, not a hypothetical.
2. **Risk is computed dynamically, never cached.** `Capability.baseline_risk_level` is one input, not the answer. Actual risk = `baseline_risk + parameter_risk + impact + reversibility + scope`, computed fresh per `PermissionCheckResult` (§6.9). A capability's risk field is a starting point, not a verdict.
3. **Insight Engine, not Reflection + Review.** One engine, two output modes (`reflection` = internal, system-facing; `review` = user-facing narrative). Shared evidence pipeline, shared archive. Don't build two systems.
4. **Goals are a single-parent tree + dependency array**, not a multi-parent DAG. This was a deliberate simplification for V0 buildability, documented as ADR-018. Don't "fix" it back to a graph.
5. **Composite Capabilities compile into Plans.** They never call other capabilities directly. The Planner is the only orchestration component in the system. If you write code where one capability invokes another capability's logic directly, you've built a second orchestrator — undo it.
6. **Each contract owns its own lifecycle.** There is no separate canonical state-machine document to keep in sync — the state machine lives in the entity's own schema/module. One source of truth per entity.
7. **Decision estimates confirmation; Permission decides it.** `Decision.estimated_confirmation_needed` is a guess made before parameters are final. The authoritative answer is `PermissionCheckResult.confirmation_status`, computed later against real parameters. Never treat the Decision-level field as final.
8. **Work Modes ≠ Permission Profiles.** Work Modes (`Locked In`, `Creative`, `Study`, `Travel`, ...) are user-facing context/focus settings. Permission Profiles (`Conservative`, `Balanced`, `Autonomous`) are autonomy posture. Same word "profile" was used for both at one point — they are unrelated axes. Keep them in separate config namespaces.
9. **Context Budget flows one direction.** Context Engine (part of the merged Reasoning module in V0) computes the budget; the LLM Router consumes it as `max_tokens`. The Router does not compute its own independent budget.
10. **Memory has exactly one writer: the Memory System.** Learning Engine, Reflection, and every other subsystem may only *propose*. A proposal becomes a Memory record only after acceptance (user or high-confidence auto-accept per rules), and only the Memory System performs that write. If you find yourself giving Learning Engine a direct `create_memory()` call, that's the single-writer rule broken.
11. **Insight Records are permanently persisted**, not transient responses. Reflection and review history must remain queryable indefinitely — "revisit a review from six months ago" is a real, locked requirement, not a nice-to-have.
12. **User Model is a derived, materialized projection — never authored directly.** It's computed from Memory. The one exception: a user correction sets `override_value`, which always wins over the computed value, forever, until explicitly cleared. Nothing else writes to it.

---

## 5. Version 0 Scope

V0's job, per the project charter: *remember important information, manage goals and projects, plan work, perform safe actions, organize information, reflect on progress, maintain knowledge about itself. Nothing else is required.*

### 5.1 — Must build (V0 cannot ship without these)

- **Mission** — single active record, versioned, loaded at startup, editable only through an explicit rare user action.
- **Goals** — single-parent tree + dependencies, full lifecycle, versioned.
- **Memory** — Archive (flat, append-only) + Memory (rule-based creation only for V0 — the deterministic triggers listed in the Constitution, §5.10 has the full list). AI-based/probabilistic memory creation is §5.2, not §5.1.
- **Reasoning Engine** — Context gathering + Decision Engine, implemented as one module for V0 (architecturally still two contracts — see §5.5). Produces real `Decision` objects with evidence and confidence, for at least 2–3 intents to start (Planning, Programming are good first targets).
- **Planner** — flat task lists plus one level of decomposition. No dependency-graph solver required yet.
- **Permission System** — binary allow/deny/confirm per capability, full dynamic risk computation (§6.9), single gate wired into the pipeline exactly as shown in §2.
- **Action Engine** — full lifecycle, one capability per action, logging.
- **3–5 real Capabilities** — primitive only. Pick low-risk-first: e.g. local file read/write, calendar read, one write capability (calendar.create_event is a good final one — it proves the confirmation path).
- **Orchestrator** — single workflow orchestrator: sequencing and retries only. One LLM provider + one hardcoded fallback. No adaptive routing yet (that's §5.3/§7).
- **Insight Engine, weekly only** — evidence-first reflection, persisted as an `InsightRecord`. Both modes exist (§4.3) but only the weekly cadence — daily/monthly/quarterly/yearly come later.
- **Unified logging.**

### 5.2 — Should have (build immediately after 5.1 works end-to-end; still "V0," not deferred)

- AI-based (probabilistic) memory proposals.
- Learning Engine + `LearningProposal` — implement **User Learning** category first, end-to-end, before adding Strategy or Relationship Learning categories. Relationship Learning was explicitly kept in V0 scope by the architect, on the condition that it uses the exact same proposal/confidence/rejection pipeline as everything else — no bespoke subsystem, no standalone "familiarity dial." Build it as the second category through the proven pipeline, not the first thing you build.
- Permission Profiles (Conservative/Balanced/Autonomous) + temporary/expiring permissions.
- Composite Capabilities compiling to Plans (§4.5).
- Undo, scoped to Action Engine IDs only — no natural-language "undo the thing from yesterday" resolution yet.
- User Model materialized view (§4.12) — until this exists, Reasoning can read Memory directly; this is an optimization/correctness layer, not a blocker for 5.1.
- Knowledge as a graph — but as an edge table in the same Postgres instance (`knowledge_nodes` + `knowledge_edges`), not a dedicated graph database. Graph *modeling* is in scope; graph *infrastructure* is not.

### 5.3 — Explicitly deferred (do not build, do not scaffold "for later")

- Vector search / pgvector-backed semantic retrieval (start with structured/tagged lookup; add this only once retrieval quality is a proven, measured bottleneck).
- Adaptive multi-LLM performance routing (Model Router with historical metrics). Hardcode routing rules for V0.
- Mobile companion app.
- Public Jarvis API.
- Capability marketplace / third-party capability installation.
- Multi-timescale Insight beyond weekly (daily/monthly/quarterly/yearly/project-completion/major-life-event).
- Emergency Override actions (PV-02) — real, but small and risky enough to defer past first ship.

---

## 6. Canonical Data Contracts (V0)

These are the actual schemas. They are the result of the contract review round — several fields below were renamed or added specifically to close gaps that review found. Where a field changed from an earlier draft, the note says so; don't revert it.

> **Stack assumption:** no source document specifies a language. This prompt assumes **Python 3.12+, Pydantic v2 models mirroring these schemas 1:1, PostgreSQL + pgvector for storage** (pgvector unused until §5.3 is unlocked, but the extension should be enabled from the start per infrastructure spec). If your architect wants a different stack, only §9–§10 need to change — the contracts and milestones don't.

### 6.1 Mission
*One active record, versioned, never overwritten.*
```json
{
  "id": "uuid", "version": 1,
  "title": "string", "statement": "string",
  "principles": ["string"],
  "status": "active | archived",
  "created_at": "ts", "updated_at": "ts",
  "previous_version_id": "uuid | null"
}
```
Invariant: exactly one `active` Mission may exist at any time.

### 6.2 Goal
```json
{
  "id": "uuid", "type": "LifeGoal | Project | Task | Habit",
  "title": "string", "description": "string",
  "status": "Draft | Active | Paused | Completed | Archived | Cancelled",
  "priority": "Low | Medium | High | Critical",
  "parent_goal_id": "uuid | null",
  "mission_id": "uuid",
  "dependencies": ["goal_id"],
  "success_metrics": ["string"],
  "deadline": "ts | null",
  "created_at": "ts", "updated_at": "ts", "completed_at": "ts | null",
  "version": 1
}
```
Invariants: no dependency cycles; every Goal traces to exactly one Mission; single parent only (§4.4).

### 6.3 Memory
```json
{
  "id": "uuid", "type": "Preference | Fact | Skill | Relationship | Constraint",
  "title": "string", "value": "string",
  "confidence": 0.0, "importance": "Low | Medium | High",
  "status": "Active | Archived | Forgotten",
  "source_ids": ["id"], "related_goal_ids": ["id"], "related_memory_ids": ["id"],
  "created_at": "ts", "updated_at": "ts", "version": 1
}
```
Invariant: **created only by the Memory System.** Learning Engine, Reflection, and Insight Engine may propose (via `LearningProposal`, §6.11) — they never call a memory-create path directly. This is decision #10 (§4). Confidence ∈ [0,1]. At least one `source_id` required.

### 6.4 ContextItem
*Ephemeral — never persisted, never versioned. Exists only for one reasoning cycle.*
```json
{
  "id": "uuid", "source_type": "Goal | Memory | KnowledgeNode | Event",
  "source_id": "id", "payload": {},
  "relevance_score": 0.0, "confidence": 0.0, "freshness": 0.0,
  "priority": "Low | Medium | High",
  "reasoning": "string", "created_at": "ts"
}
```
Ranking pipeline (owned by the Reasoning module, per the ContextItem contract resolution — supersedes any older doc claiming Context Engine performs no ranking): `Retrieve → Score → Rank → Budget Cutoff → ContextItems`.

### 6.5 Decision
*Immutable once created.*
```json
{
  "id": "uuid", "objective": "string", "summary": "string", "reasoning": "string",
  "confidence": 0.0, "alternatives": ["string"], "selected_option": "string",
  "expected_outcome": "string", "risks": ["string"], "mission_alignment": 0.0,
  "goal_ids": ["id"], "context_item_ids": ["id"],
  "requires_plan": true,
  "estimated_confirmation_needed": false,
  "created_at": "ts", "version": 1
}
```
**Renamed field:** `requires_confirmation` → `estimated_confirmation_needed` (decision #7, §4). This is a guess, not an authorization. The real answer lives on `PermissionCheckResult`.

### 6.6 Plan
```json
{
  "id": "uuid", "decision_id": "id", "title": "string", "objective": "string",
  "status": "Draft | Approved | Executing | Completed | Paused | Cancelled",
  "action_ids": ["id"], "estimated_duration": "string", "progress": 0,
  "version": 1
}
```
Invariants: references exactly one Decision; ≥1 Action; Completed Plans are immutable.

### 6.7 Action
```json
{
  "id": "uuid", "plan_id": "id", "capability_id": "id",
  "type": "Read | Write | Compute | Communicate",
  "title": "string", "parameters": {},
  "status": "Pending | Ready | Running | Completed | Failed | Cancelled",
  "permission_check_id": "id",
  "retry_count": 0
}
```
**Added field:** `permission_check_id` — every Action must reference the `PermissionCheckResult` that authorized it. This closes the audit gap flagged in review (an Action with no traceable authorization is unauditable, which directly violates the permission-logging requirement in the Constitution).

### 6.8 Capability
```json
{
  "id": "string", "name": "string", "description": "string", "category": "string",
  "capability_type": "primitive | composite",
  "input_schema": {}, "output_schema": {},
  "baseline_risk_level": "Low | Medium | High | Critical",
  "required_permissions": ["string"], "supports_undo": true,
  "enabled": true, "version": 1
}
```
**Renamed field:** `risk_level` → `baseline_risk_level` — it is one input to the dynamic risk formula, not the risk itself (decision #2). **Added field:** `capability_type` — required so composite capabilities can never silently become a second orchestrator (decision #5). Composite capabilities additionally carry a `compiles_to_plan_template` reference; the Planner expands it, the Capability layer never executes it directly.

### 6.9 PermissionCheckResult
*The authoritative risk-and-authorization record. This did not exist before the contract review — build it exactly as specified, it's load-bearing.*
```json
{
  "id": "uuid", "capability_id": "id", "plan_id": "id | null",
  "proposed_parameters": {},
  "baseline_risk": "Low | Medium | High | Critical",
  "computed_risk": "Low | Medium | High | Critical",
  "risk_factors": { "parameter_risk": "string", "impact": "string", "reversibility": "string", "scope": "string" },
  "permission_status": "granted | denied | confirmation_required",
  "confirmation_status": "not_applicable | pending | confirmed | rejected",
  "permission_rule_used": "string", "action_id": "id | null",
  "decided_at": "ts", "resolved_at": "ts | null"
}
```
Invariant: `computed_risk` is always derived from `baseline_risk + risk_factors` — never set directly by any caller. If `permission_status = confirmation_required`, the Action Engine may not transition the Action past `Ready` until `confirmation_status = confirmed`.

### 6.10 CapabilityResult
```json
{
  "id": "uuid", "action_id": "id", "success": true, "output": {},
  "execution_time": "string", "tokens_used": 0, "credits_used": 0,
  "error": "string | null", "created_at": "ts"
}
```

### 6.11 LearningProposal (§5.2)
Purpose: a candidate pattern, never a write. `category: user | strategy | relationship`. Key fields: `evidence_ids` (minimum 2 — no single-event learning, per §5.10), `confidence`, `status: proposed | accepted | rejected | revoked`, `resulting_memory_id` (nullable, set only by Memory System after acceptance). Full shape available on request from the architect if not reconstructable from this summary.

### 6.12 InsightRecord (§5.1, weekly only for V0)
Purpose: the permanent archive entry for one reflection or review cycle. Key fields: `mode: reflection | review`, `scope` (weekly only in V0), `narrative`, `evidence: [{claim, supporting_ids}]` (every claim must trace to evidence — non-negotiable), `wins`, `problems`, `user_response`. Immutable once generated except `user_response`.

### 6.13 UserModel (§5.2, materialized view)
Purpose: derived projection, one row per `(attribute_category, attribute_key)`. Key fields: `computed_value` (Memory System only, regenerated on Memory events), `override_value` (user only, always wins when present), `confidence`, `source_memory_ids`. No independent `Create` — this entity cannot be authored, only derived and overridden.

---

## 7. Repository Structure

```
jarvis/
  contracts/            # Pydantic models for every entity in §6 — single source of truth,
                         # every other package imports FROM here, never redefines a shape
  mission/
  goals/
  memory/
  knowledge/             # V0: nodes + edges table, no vector search yet
  reasoning/              # merged Context + Decision module (architecturally 2 contracts, 1 package)
  planner/
  permission/            # includes the dynamic risk calculator
  action_engine/
  capabilities/
    registry.py
    primitives/           # one file per primitive capability
  orchestrator/           # sequencing + retries only, no LLM selection logic here
  insight/                # weekly reflection/review
  learning/               # §5.2 — build after core loop works
  infra/
    storage.py            # Postgres + pgvector connection, migrations
    logging.py             # unified logger, one instance, every subsystem gets a named child logger
    llm_router.py          # V0: hardcoded provider + one fallback
  config/                 # YAML, versioned
  tests/                 # mirrors package structure 1:1
  docs/                  # copies of the RFCs / this prompt, for reference during build
  ARCHITECTURE_ISSUES.md  # see §10
```

Enforce RFC-014's interface rule structurally: a package may only import another package's public interface module (e.g. `goals/api.py`), never reach into another package's internal storage or models directly. If your language/tooling can enforce this at import time, do so; if not, treat it as a hard review-time check (§10).

---

## 8. Coding Standards

- Every Pydantic model in `contracts/` carries a docstring stating which entity in §6 it implements (e.g. `# implements §6.9 PermissionCheckResult`).
- Type-annotate everything. No `Any` except at genuine external-data boundaries (raw LLM output before parsing, raw API responses before validation).
- One test file per contract, and the tests assert the actual invariants listed in §6 (e.g. "no dependency cycles," "computed_risk always derived, never set directly") — not just "the model instantiates."
- No `print()` — use the unified logger (`infra/logging.py`) everywhere, always.
- Config is YAML, human-readable, versioned. No binary or code-embedded config.
- Async-first for anything that touches the database, an LLM call, or an external capability — don't block the event loop on I/O.
- Formatting/linting: use whatever your toolchain defaults to consistently (black + ruff is a reasonable default for Python) — consistency matters more than the specific tool.

---

## 9. Implementation Milestones

Build in this order. Each milestone should be independently runnable and tested before starting the next — don't parallelize across milestones on a solo build.

| # | Milestone | Ships |
|---|---|---|
| M0 | Scaffolding | Repo structure, config loader, unified logging, Postgres+pgvector connection, `contracts/` package with every §6 model, `ARCHITECTURE_ISSUES.md` created |
| M1 | Static foundation | Mission loader, Goal CRUD via a Goal API module, tests against §6.2 invariants |
| M2 | Memory (rule-based) | Deterministic memory creation per the Constitution's always-store list, versioning, tests |
| M3 | Reasoning Engine | Merged Context+Decision module; 2–3 hardcoded retrieval policies; real `Decision` objects with evidence and confidence |
| M4 | Planner + Permission | Plans generated from Decisions; full `PermissionCheckResult` with the dynamic risk formula; single gate wired exactly per §2 |
| M5 | Action Engine + first capabilities | 3–5 primitive capabilities (low-risk-first), full Action lifecycle, `permission_check_id` enforced on every Action |
| M6 | Orchestrator wiring | One full, real, end-to-end request → result loop working (e.g. "what should I work on today," or "add this to my goals") |
| M7 | Insight Engine (weekly) | Evidence-first reflection, `InsightRecord` persisted, basic user-facing narrative |
| M8 (stretch, §5.2) | Fast-follow | AI-based memory proposals, Learning Engine (User Learning category first), Composite Capabilities, Undo, User Model materialized view, Permission Profiles |

M0–M7 is V0. M8 is "V0 continues" — genuinely good to have, not required to call V0 shipped.

---

## 10. Review Expectations & Escalation Protocol

At the end of every milestone, check against — in this order:

1. **Contract compliance** — does every entity you touched match its §6 schema exactly, including the renamed/added fields? Do your tests assert the actual invariants, not just "it runs"?
2. **No second orchestrator** — did any composite capability, any "helper," end up calling another capability's execution logic directly instead of compiling to a Plan? This is the single most likely regression given how the architecture got here.
3. **No boundary violations** — does any package reach into another package's storage or internal models instead of going through its public interface (§7)?
4. **Single-writer rule intact** — for Memory specifically: is there exactly one code path that creates a Memory record, and does it live in the Memory System?

**When a contract genuinely doesn't work in practice:** append an entry to `ARCHITECTURE_ISSUES.md`:
```markdown
## [YYYY-MM-DD] <short title>
**Contract/section:** §6.x <Entity>
**What broke:** <concrete description, ideally with the failing case>
**What I did instead:** <the most faithful interpretation you implemented, and why>
**Needs architect decision:** yes/no
```
Then keep building on your best-faith interpretation. Don't block waiting for a response, and don't quietly redesign around the problem — both are worse than logging it and moving forward.

---

## 11. Definition of Done for V0

V0 is shippable when:

- M0 through M7 are complete, tested, and none of them required an undocumented deviation from §6.
- At least three distinct real end-to-end request types work through the full pipeline in §2, including at least one that hits the Permission Check confirmation path (not just the auto-approved path).
- Every autonomous action is traceable: given an Action ID, you can reconstruct which Decision, Plan, PermissionCheckResult, and Capability produced it.
- `ARCHITECTURE_ISSUES.md` exists, is non-hypothetical (reflects real issues hit during the build, if any), and has been reviewed by the architect before V1 planning starts.
- It is stable, understandable by someone reading the repo cold, and — per the project's own success criteria — it genuinely saves the user time and is useful enough to help design V1.

That last one is the real test. Everything above it is just how you get there.
