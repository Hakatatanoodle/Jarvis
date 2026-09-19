# RFC-011 — Event Contract

## Purpose
An Event represents **something that happened** inside Jarvis.
Events are immutable notifications.
They are not commands.
They are not requests.
They simply say
> "This happened."

Examples
```
GoalCompleted

MemoryCreated

ActionFailed

PermissionGranted

CalendarEventCreated
```

---

## Owner
No one.
Events are produced by systems and consumed by systems.

---

## Produced By
Any subsystem.

---

## Read By
Anyone subscribing.

---

## Lifecycle
```
Produced

↓

Published

↓

Consumed

↓

Archived
```

---

## Versioned
❌ No

---

## Archived
✅ Yes
Event history becomes the system audit log.

---

## Mutable
❌ Never.

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| event_type | GoalCompleted |
| producer | Planner |
| payload | Event data |
| timestamp | Produced time |
| correlation_id | Workflow trace |
| causation_id | Parent event |
| severity | Info / Warning / Error |

---

## Example
```
ActionCompleted

↓

Action ID

↓

Calendar Event Created

↓

Workflow 204
```

---

# JSON
```json
{
  "id":"event-001",
  "event_type":"ActionCompleted",
  "producer":"ActionEngine",
  "payload":{
      "action_id":"123"
  },
  "timestamp":"...",
  "correlation_id":"workflow-22",
  "causation_id":"event-19",
  "severity":"Info"
}
```

---

# RFC-012 — CapabilityResult

## Purpose
Represents the output returned after a Capability executes.
Separating Action from Result keeps execution history clean.

---

## Owner
Action Engine

---

## Lifecycle
```
Action Executes

↓

CapabilityResult Created

↓

Stored

↓

Referenced
```

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| action_id | Parent Action |
| success | Boolean |
| output | Returned data |
| execution_time | Duration |
| tokens_used | LLM usage |
| credits_used | API cost |
| error | Optional |
| created_at | Timestamp |

---

## Example
```
Action

↓

Create Calendar Event

↓

CapabilityResult

↓

Event ID:
78291
```

---

# RFC-013 — Workflow Contract

## Purpose
A Workflow groups multiple Decisions, Plans, Actions and Events into one logical execution.

Without Workflows:
```
100 Actions

↓

Chaos
```

With Workflows:
```
Organize My Week

↓

Workflow

↓

Decision

↓

Plan

↓

12 Actions

↓

Done
```

---

## Owner
Orchestrator

---

## Lifecycle
```
Created

↓

Running

↓

Waiting

↓

Completed

↓

Archived
```

---

## Relationships
```
Workflow

├── Decision

├── Plan

├── Actions

├── Events

└── CapabilityResults
```

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| title | Human-readable |
| objective | Overall purpose |
| status | Running / Waiting / Completed |
| decision_ids | Decisions |
| plan_ids | Plans |
| action_ids | Actions |
| event_ids | Events |
| started_at | Timestamp |
| finished_at | Timestamp |

---

# Example
```
Workflow

Weekly Planning

↓

Decision

↓

Plan

↓

Read Calendar

↓

Move Tasks

↓

Create Events

↓

Notify User
```

---

# RFC-014 — System Interface Contracts

Instead of every subsystem talking directly to everyone else...
Every subsystem exposes an interface.
```
Goal System

↓

Goal API
```
```
Memory System

↓

Memory API
```
```
Knowledge System

↓

Knowledge API
```
etc.

---

## Interface Rules
Every subsystem exposes only four operations:
```
Read

Create

Update

Query
```
No subsystem reaches into another subsystem's database.
Everything goes through contracts.

---

Example

Instead of
```
Planner

↓

Reads Memory Database
```
It becomes
```
Planner

↓

Memory Interface

↓

Memory System

↓

Response
```
Huge difference.

---

# Cross-System Communication Rules

These become global architecture rules.

### Rule 1
Subsystems never modify another subsystem's data directly.

---

### Rule 2
Everything important produces an Event.

---

### Rule 3
Execution always follows
```
Decision

↓

Plan

↓

Action

↓

Capability

↓

CapabilityResult
```

---

### Rule 4
Everything is traceable through Workflow IDs.

---

### Rule 5
Every operation is explainable.
Jarvis must always be able to answer
> "Why did you do that?"

by reconstructing:
```
Mission

↓

Goal

↓

Decision

↓

Plan

↓

Action

↓

Capability

↓

Result
```
