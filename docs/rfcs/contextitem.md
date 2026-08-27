# RFC-006 — ContextItem Contract

## Purpose
A ContextItem represents **one piece of information selected by the Context Engine for the current reasoning process**.
It is **not** storage.
It is **working memory**.

Every Decision, Plan, Reflection, Review, or LLM request receives a collection of ContextItems.

---

## Owner
**Context Engine**
Only the Context Engine creates ContextItems.
Nothing stores ContextItems permanently.
They exist only for the duration of a reasoning cycle.

---

## Created By
- Context Engine

---

## Updated By
Nobody.
A ContextItem is immutable.
If context changes, a new ContextItem is created.

---

## Read By
- Decision Engine
- Planner
- Insight Engine
- Orchestrator
- LLM Router

---

# Lifecycle
```
Memory / Goal / Knowledge / Events
            ↓
     Context Engine
            ↓
      ContextItem(s)
            ↓
    Decision / Planning
            ↓
        Destroyed
```
ContextItems are **ephemeral**.

---

# Relationships
A ContextItem always originates from another contract.
Examples:
- Goal
- Memory
- KnowledgeNode
- Event
- Review
- Decision
- Action

It never owns information.
It references it.

---

# Versioned
❌ No

---

# Archived
❌ No

---

# Mutable
❌ No
Immutable by design.

---

# Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| source_type | Goal, Memory, KnowledgeNode, Event, etc. |
| source_id | ID of originating contract |
| payload | Snapshot of relevant data |
| relevance_score | Importance for current reasoning (0–1) |
| confidence | Confidence inherited from source |
| freshness | How recent the information is |
| priority | Importance determined by Context Engine |
| reasoning | Why this item was selected |
| created_at | Timestamp |

---

# Validation Rules
- Every ContextItem must reference an existing source.
- ContextItems are immutable.
- Payload is a **snapshot**, not a live object.
- ContextItems expire after the reasoning cycle.
- ContextItems are never manually edited.

---

# Context Budget
The Context Engine operates under a limited budget.
Instead of asking:
> "What information exists?"

it asks:
> "What information is worth spending context on?"

Each reasoning cycle has a configurable context budget.
The Context Engine selects the highest-value ContextItems that fit within that budget.

Selection considers:
- Relevance
- Confidence
- Priority
- Freshness
- Mission alignment
- User request
- Active goals

---

# Ranking Pipeline
```
Candidate Objects
        ↓
Retrieve
        ↓
Score
        ↓
Rank
        ↓
Budget Cutoff
        ↓
ContextItems
```
This is where our earlier **Context Budget** decision becomes concrete.

---

# Events Produced
None.
ContextItems are temporary.

---

# Events Consumed
```
ReasoningRequested
PlanningRequested
ReviewRequested
ReflectionRequested
```

---

# Example
Suppose you ask:
> "Help me plan this week."

The Context Engine might create:
```
ContextItem #1
Source: Goal
Build Jarvis
Relevance: 0.98
Reason:
Active high-priority project.

ContextItem #2
Source: Memory
User prefers deep work in mornings.
Relevance: 0.95

ContextItem #3
Source: Calendar Event
Basketball match Thursday.
Relevance: 0.91

ContextItem #4
Source: Review
Last week overloaded schedule.
Relevance: 0.88
```
The Decision Engine never queries Memory directly.
It only receives these ContextItems.

---

# JSON Schema v1
```json
{
  "id":"context-item-uuid",
  "source_type":"Goal",
  "source_id":"goal-build-jarvis",
  "payload": {
    "title":"Build Jarvis",
    "status":"Active",
    "priority":"High"
  },
  "relevance_score":0.98,
  "confidence":1.0,
  "freshness":0.95,
  "priority":"High",
  "reasoning":"Active high-priority goal related to user request.",
  "created_at":"2026-07-29T10:15:00Z"
}
```
