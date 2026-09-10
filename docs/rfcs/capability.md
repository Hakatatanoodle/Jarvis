# RFC-010 — Capability Contract

## Purpose
A **Capability** describes something Jarvis knows how to do.
It is **not an execution**.
It is a reusable ability.
Think of Capabilities as verbs in Jarvis's vocabulary.

Examples:
- Calendar.CreateEvent
- Gmail.SendEmail
- Memory.Store
- Goal.Create
- Browser.Search
- LLM.Generate

---

## Owner
Capability Registry

---

## Created By
- Developer
- Plugin System
- User (approved custom capabilities)

---

## Updated By
Capability Registry

---

## Read By
- Planner
- Action Engine
- Orchestrator
- Permission System

---

## Lifecycle
```
Registered
      ↓
Enabled
      ↓
Deprecated
      ↓
Removed
```

---

## Relationships
```
Capability

↓

Permission Rules

↓

Actions
```
Many Actions may use the same Capability.

---

## Versioned
✅ Yes

---

## Archived
✅ Deprecated versions retained.

---

## Mutable
Yes (new versions)

---

## Primary Fields

| Field | Description |
|---|---|
| id | Unique identifier |
| name | Human-readable name |
| description | What it does |
| category | Calendar, Email, Memory, Browser, LLM, etc. |
| input_schema | Expected parameters |
| output_schema | Result format |
| risk_level | Dynamic baseline (Low/Medium/High/Critical) |
| required_permissions | Permissions required |
| supports_undo | Boolean |
| enabled | Boolean |
| version | Version |

---

## Validation Rules
- Every Capability has defined input/output schemas.
- Every Capability declares a baseline risk.
- Permission rules must exist before activation.
- Deprecated Capabilities remain executable only for historical replay if supported.

---

## Events Produced
```
CapabilityRegistered
CapabilityEnabled
CapabilityDisabled
CapabilityDeprecated
```

---

## Events Consumed
```
PluginInstalled
PluginUpdated
ConfigurationChanged
```

---

## Example
```
Capability

calendar.create_event

Category:
Calendar

Risk:
Medium

Permission:
calendar.write

Supports Undo:
Yes
```

---

# JSON Schema
```json
{
  "id":"calendar.create_event",
  "name":"Create Calendar Event",
  "description":"Creates a new event in the user's calendar.",
  "category":"Calendar",
  "input_schema": {
    "title":"string",
    "start":"datetime",
    "end":"datetime"
  },
  "output_schema": {
    "event_id":"string"
  },
  "risk_level":"Medium",
  "required_permissions": ["calendar.write"
  ],
  "supports_undo":true,
  "enabled":true,
  "version":1
}
```
