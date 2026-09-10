# RFC-002 — Goal Contract

## Purpose
A Goal represents **something the user wants to achieve**.
It is the fundamental planning object of Jarvis.
Everything from a lifelong ambition to a 5-minute task is represented as a Goal.

Goals provide the bridge between:
- Mission
- Planning
- Execution
- Reviews
- Learning

---

## Owner
**Goal System**
Only the Goal System owns Goal objects.
Other systems request changes through Goal System APIs.

---

## Created By
Goals may be created by:
- User
- Planner
- Review System (suggestions)
- Learning Engine (recommendations)
- Imported external systems

Only **approved** goals become active.

---

## Updated By
- User
- Goal System
- Approved automation

Jarvis may recommend updates.
Major changes (priority, deletion, parent changes) require user approval unless automation rules allow them.

---

## Read By
- Context Engine
- Decision Engine
- Planner
- Insight Engine
- Learning Engine
- Orchestrator
- Capability System
- UI

Goals are one of the most frequently accessed contracts.

---

# Lifecycle
```
Draft
    ↓
Active
    ↓
Paused
    ↓
Completed
```
or
```
Active
    ↓
Archived
```
or
```
Active
    ↓
Cancelled
```
Nothing important is deleted.
History is preserved.

---

# Relationships
```
Mission
    ↓
Goal
    ↓
Goal
    ↓
Goal
```
Every Goal may have:
- one parent
- many children
- many dependencies

---

# Versioned
✅ Yes
Every meaningful change creates a new version.

---

# Archived
✅ Yes
Completed and cancelled goals remain searchable.

---

# Mutable
Yes.
Controlled through Goal System.

---

# Primary Fields

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Unique identifier |
| `type` | Enum | LifeGoal, Project, Task, Habit, etc. |
| `title` | String | Short human-readable title |
| `description` | String | Detailed objective |
| `status` | Enum | Draft, Active, Paused, Completed, Archived, Cancelled |
| `priority` | Enum | Low, Medium, High, Critical |
| `parent_goal_id` | UUID/null | Parent in the goal tree |
| `mission_id` | UUID | Root mission reference |
| `dependencies` | Array<UUID> | Goals that must finish first |
| `success_metrics` | Array | How completion is measured |
| `deadline` | Timestamp/null | Optional deadline |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update |
| `completed_at` | Timestamp/null | Completion time |
| `version` | Integer | Version number |

---

# Validation Rules
- Every Goal belongs to exactly one Mission.
- Circular parent relationships are forbidden.
- A Goal cannot depend on itself.
- Dependency cycles are invalid.
- Completed Goals cannot be modified except through versioning.
- Child goals inherit Mission from their ancestors.

---

# Events Produced
```
GoalCreated
GoalUpdated
GoalPaused
GoalCompleted
GoalCancelled
GoalArchived
GoalPriorityChanged
GoalDependencyAdded
GoalDependencyRemoved
```

---

# Events Consumed
```
MissionUpdated
ReviewRecommendationAccepted
LearningRecommendationAccepted
UserRequest
```

---

# Example
```
Mission
└── Become the Best Version of Myself

    └── Life Goal
        Become Financially Independent

            └── Project
                Build Jarvis

                    └── Milestone
                        Finish Architecture v1.0

                            └── Task
                                Write Goal Contract
```

---

# JSON Schema v1
```json
{
  "id":"goal-uuid",
  "type":"Project",
  "title":"Build Jarvis",
  "description":"Develop the Jarvis AI assistant.",
  "status":"Active",
  "priority":"High",
  "parent_goal_id":"life-goal-uuid",
  "mission_id":"mission-uuid",
  "dependencies": ["goal-123","goal-456"
  ],
  "success_metrics": ["Architecture completed","Version 0 functional"
  ],
  "deadline":"2027-06-01T00:00:00Z",
  "created_at":"2026-07-29T00:00:00Z",
  "updated_at":"2026-08-15T00:00:00Z",
  "completed_at":null,
  "version":3
}
```
