# RFC-001 — Mission Contract

## Purpose
The Mission represents the permanent purpose that guides Jarvis.
It answers one question:
> **"Why does Jarvis exist?"**

The Mission is the highest-level objective in the system.
It is **not** a task, a project, or a goal.
It provides the framework within which all goals, plans, decisions, and recommendations are evaluated.

---

## Owner
**Constitution System**
The Mission belongs to the Constitution.
No other subsystem owns it.

---

## Created By
- User (during initial setup)

---

## Updated By
Only the user.
Updating the Mission should be extremely rare.
Jarvis may recommend changes but can never modify it automatically.

---

## Read By
Nearly every subsystem:
- Goal System
- Decision Engine
- Planner
- Insight Engine
- Learning Engine
- Context Engine
- Review System
- Orchestrator (indirectly through decisions)

---

## Lifecycle
```
Created
    ↓
Active
    ↓
(Optional) Updated
    ↓
Previous version archived
    ↓
New version becomes active
```
The Mission is never deleted.
Every historical version remains archived.

---

## Relationships
```
Mission
    ↓
Life Goals
        ↓
Projects
            ↓
Plans
                ↓
Actions
```
Every Goal must reference exactly one Mission.

---

## Versioned
✅ Yes
Every modification creates a new version.
Nothing is overwritten.

---

## Archived
✅ Yes
Old versions remain permanently accessible.

---

## Mutable
Yes, but only by explicit user action.
Jarvis cannot edit the Mission autonomously.

---

# Primary Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | ✅ | Unique identifier |
| `version` | Integer | ✅ | Incrementing version number |
| `title` | String | ✅ | Short mission title |
| `statement` | String | ✅ | Complete mission statement |
| `principles` | Array<String> | Optional | Core values supporting the mission |
| `created_at` | Timestamp | ✅ | Creation time |
| `updated_at` | Timestamp | ✅ | Last modification time |
| `status` | Enum | ✅ | active / archived |
| `previous_version_id` | UUID \| null | Optional | Link to previous version |

---

# Validation Rules
- Exactly **one active Mission** may exist.
- The statement cannot be empty.
- Updating creates a new version; it never edits the existing one.
- Archived versions are immutable.
- Goals cannot exist without an active Mission.

---

# Events Produced
```
MissionCreated

MissionUpdated

MissionArchived
```

---

# Events Consumed
None.
The Mission is a root contract.

---

# Example (Concept)

**Title**
```
Become the Best Version of Myself
```

**Statement**
```
Jarvis exists to maximize Yochan's long-term growth by helping him think, build, learn, and execute while keeping him in control of important decisions.

Its purpose is to help Yochan become the best version of himself in work, creativity, health, and relationships.
```

**Principles**
```
- Long-term thinking
- User autonomy
- Radical honesty
- Continuous learning
- Evidence-based decisions
```

---

# JSON Schema v1
```json
{
  "id":"uuid",
  "version":1,
  "title":"Become the Best Version of Myself",
  "statement":"Jarvis exists to maximize Yochan's long-term growth by helping him think, build, learn, and execute while keeping him in control of important decisions. Its purpose is to help Yochan become the best version of himself in work, creativity, health, and relationships.",
  "principles": ["Long-term thinking","User autonomy","Radical honesty","Continuous learning","Evidence-based decisions"
  ],
  "status":"active",
  "created_at":"2026-07-29T00:00:00Z",
  "updated_at":"2026-07-29T00:00:00Z",
  "previous_version_id":null
}
```
