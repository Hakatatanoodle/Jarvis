# RFC-008 — Plan Contract

## Purpose
A **Plan** is the strategy for executing a Decision.
A Decision answers **what** should happen.
A Plan answers **how** it should happen.
A Plan is composed of ordered Actions.

---

## Owner
Planner

---

## Created By
- Planner

---

## Updated By
- Planner
- User (through edits)

Updates create a new version.

---

## Read By
- Orchestrator
- Action Engine
- UI
- Review System
- Learning Engine

---

## Lifecycle
```
Draft
    ↓
Approved
    ↓
Executing
    ↓
Completed

or

Executing
    ↓
Paused

or

Executing
    ↓
Cancelled
```

---

## Relationships
```
Decision
     ↓
    Plan
     ↓
 Actions
```
A Plan always references exactly one Decision.
A Plan owns one or more Actions.

---

## Versioned
✅ Yes

---

## Archived
✅ Yes

---

## Mutable
Yes (via versioning)

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| decision_id | Source Decision |
| title | Human-readable title |
| objective | Desired outcome |
| status | Draft / Approved / Executing / Completed / Paused / Cancelled |
| action_ids | Ordered list of Actions |
| estimated_duration | Expected completion time |
| dependencies | Other Plans (optional) |
| progress | 0–100% |
| created_at | Timestamp |
| updated_at | Timestamp |
| version | Version |

---

## Validation Rules
- Must reference one Decision.
- Must contain at least one Action.
- Progress is derived from Action completion.
- Completed Plans become immutable.

---

## Events Produced
```
PlanCreated
PlanApproved
PlanStarted
PlanPaused
PlanCompleted
PlanCancelled
```

---

## Events Consumed
```
DecisionApproved
ActionCompleted
ActionFailed
```

---

## Example
```
Decision:
Organize next week

↓

Plan:
Optimize Weekly Schedule

↓

1. Read Calendar
2. Read Task List
3. Schedule Deep Work
4. Move Low Priority Tasks
5. Notify User
```

---

# JSON Schema
```json
{
  "id":"plan-uuid",
  "decision_id":"decision-123",
  "title":"Optimize Weekly Schedule",
  "objective":"Create an effective weekly schedule.",
  "status":"Approved",
  "action_ids": ["action-1","action-2","action-3"
  ],
  "estimated_duration":"5m",
  "progress":0,
  "version":1
}
```
