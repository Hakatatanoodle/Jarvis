# RFC-009 — Action Contract

## Purpose
An **Action** is the smallest executable unit of work.
Everything Jarvis actually does is an Action.

Examples:
- Read Calendar
- Create Calendar Event
- Move Task
- Send Email
- Call LLM
- Save Memory
- Generate Insight

---

## Owner
Action Engine

---

## Created By
- Planner

---

## Updated By
Action Engine

---

## Read By
- Orchestrator
- Capability System
- UI
- Review System

---

## Lifecycle
```
Pending
    ↓
Ready
    ↓
Running
    ↓
Completed

or

Running
    ↓
Failed

or

Pending
    ↓
Cancelled
```

---

## Relationships
```
Plan
    ↓
 Action
    ↓
Capability
```
One Action requires exactly one Capability.

---

## Versioned
❌ No
Actions are execution records.
If something changes, create a new Action.

---

## Archived
✅ Yes
Execution history is valuable.

---

## Mutable
Only status.
Everything else is immutable.

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| plan_id | Parent Plan |
| capability_id | Capability used |
| type | Read / Write / Compute / Communicate |
| title | Human-readable action |
| parameters | Execution parameters |
| status | Pending / Running / Completed / Failed |
| retry_count | Number of retries |
| result_id | Reference to Action Result |
| created_at | Timestamp |
| started_at | Timestamp |
| completed_at | Timestamp |

---

## Validation Rules
- One Capability per Action.
- Parameters validated before execution.
- Failed Actions may retry according to policy.
- Dangerous Actions require permission unless pre-authorized.

---

## Events Produced
```
ActionStarted
ActionCompleted
ActionFailed
ActionRetried
```

---

## Events Consumed
```
PlanStarted
CapabilityAvailable
PermissionGranted
```

---

## Example
```
Action

Create Calendar Event

Capability:
Calendar.CreateEvent

Parameters:

Date
Time
Duration
Title
```

---

# JSON Schema
```json
{
  "id":"action-123",
  "plan_id":"plan-456",
  "capability_id":"calendar.create_event",
  "type":"Write",
  "title":"Create Calendar Event",
  "parameters": {
    "title":"Deep Work",
    "start":"...",
    "end":"..."
  },
  "status":"Pending",
  "retry_count":0
}
```
