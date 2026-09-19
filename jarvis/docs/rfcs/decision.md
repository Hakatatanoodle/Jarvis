# RFC-007 — Decision Contract

This is, in my opinion, the most important contract in the system.
Everything before this prepares information.
Everything after this executes information.

---

## Purpose
A **Decision** represents the conclusion reached by the Decision Engine after evaluating the current context.
It answers:
> **"What should Jarvis do next?"**

A Decision is **not execution**.
It is **intent plus reasoning**.
Execution comes later through Plans and Actions.

---

## Owner
**Decision Engine**
Only the Decision Engine creates Decisions.

---

## Created By
- Decision Engine

---

## Updated By
Nobody.
Immutable.
A new Decision is created whenever reasoning changes.

---

## Read By
- Planner
- Orchestrator
- Insight Engine
- Learning Engine
- UI
- Review System

---

## Lifecycle
```
Reasoning Requested
        ↓
Decision Generated
        ↓
Approved / Rejected
        ↓
Executed
        ↓
Archived
```

---

## Relationships
Every Decision references:
- Mission
- Active Goals
- ContextItems
- Previous Decision (optional)
- Generated Plan (optional)

---

## Versioned
✅ Yes

---

## Archived
✅ Yes
Every decision is part of Jarvis's reasoning history.

---

## Mutable
❌ No

---

# Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| objective | What question was being answered? |
| summary | One-line decision |
| reasoning | Human-readable explanation |
| confidence | 0–1 confidence |
| alternatives | Other considered options |
| selected_option | Chosen alternative |
| expected_outcome | Predicted result |
| risks | Identified risks |
| mission_alignment | Alignment score |
| goal_ids | Supporting goals |
| context_item_ids | Context used |
| requires_plan | Boolean |
| requires_confirmation | Boolean |
| created_at | Timestamp |
| version | Version |

---

# Validation Rules
- Every Decision references at least one ContextItem.
- Confidence ∈ [0,1].
- Mission alignment must be computed.
- A Decision never executes anything directly.
- Decisions cannot be edited after creation.

---

# Events Produced
```
DecisionCreated
DecisionApproved
DecisionRejected
DecisionArchived
```

---

# Events Consumed
```
ReasoningRequested
ContextReady
```

---

# Example

**Objective**
> Organize next week's schedule.

**Decision**
```
Summary:
Prioritize Jarvis architecture work during morning deep-work sessions.

Reasoning:
Morning focus aligns with user preference and current high-priority project.

Confidence:
0.94

Expected Outcome:
Higher weekly progress with lower cognitive load.

Requires Plan:
Yes

Requires Confirmation:
No
```

---

# JSON Schema v1
```json
{
  "id":"decision-uuid",
  "objective":"Plan next week",
  "summary":"Schedule deep work in the mornings.",
  "reasoning":"Matches user preferences and active goals.",
  "confidence":0.94,
  "alternatives": ["Evening work","Split sessions"
  ],
  "selected_option":"Morning work",
  "expected_outcome":"Higher productivity.",
  "risks": ["Unexpected meetings"
  ],
  "mission_alignment":0.96,
  "goal_ids": ["goal-build-jarvis"
  ],
  "context_item_ids": ["ctx-1","ctx-2"
  ],
  "requires_plan":true,
  "requires_confirmation":false,
  "created_at":"...",
  "version":1
}
```
