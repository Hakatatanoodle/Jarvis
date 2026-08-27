# RFC-003 — Memory Contract

## Purpose
A Memory represents a **single long-term fact** that Jarvis believes is useful for helping the user.
Memory is **not** a conversation.
Conversation is evidence.

---

## Owner
**Memory System**

---

## Created By
- Memory Extractor
- User
- Learning Engine
- Manual Import

---

## Updated By
- Memory System
- User

(New version created, never overwrite.)

---

## Read By
- Context Engine
- Knowledge System
- Learning Engine
- Decision Engine
- Review System
- User Model

---

## Lifecycle
```
Observed
    ↓
Extracted
    ↓
Verified
    ↓
Active
    ↓
Updated
    ↓
Archived / Forgotten
```

---

## Relationships
A Memory may reference:
- User
- Goals
- Knowledge Nodes
- Other Memories
- Source Conversations
- Events

---

## Versioned
✅ Yes

---

## Archived
✅ Yes
Old versions are preserved.

---

## Mutable
Yes.
Only through the Memory System.

---

# Primary Fields

| Field | Description |
|---|---|
| id | Unique ID |
| type | Preference, Fact, Skill, Relationship, Constraint, etc. |
| title | Short summary |
| value | Actual remembered information |
| confidence | 0–1 confidence score |
| importance | Retrieval priority |
| status | Active / Archived / Forgotten |
| source_ids | Evidence supporting this memory |
| related_goal_ids | Linked goals |
| related_memory_ids | Linked memories |
| created_at | Timestamp |
| updated_at | Timestamp |
| version | Version |

---

# Validation Rules
- Every memory must have at least one source.
- Confidence ∈ [0,1].
- Archived memories cannot be modified.
- Updates create new versions.
- Duplicate memories should merge rather than multiply.

---

# Events Produced
```
MemoryCreated
MemoryUpdated
MemoryArchived
MemoryForgotten
MemoryMerged
```

---

# Events Consumed
```
ConversationProcessed
UserCreatedMemory
LearningAccepted
MemoryEdited
```

---

# Example
```
Type:
Preference

Title:
Preferred IDE

Value:
VS Code

Confidence:
0.98

Importance:
High

Sources:
Conversation #124
Conversation #209

Related Goal:
Build Jarvis
```

---

# JSON Schema v1
```json
{
  "id":"memory-uuid",
  "type":"Preference",
  "title":"Preferred IDE",
  "value":"VS Code",
  "confidence":0.98,
  "importance":"High",
  "status":"Active",
  "source_ids": ["conversation-124","conversation-209"
  ],
  "related_goal_ids": ["goal-build-jarvis"
  ],
  "related_memory_ids": [],
  "created_at":"2026-07-29T00:00:00Z",
  "updated_at":"2026-08-01T00:00:00Z",
  "version":2
}
```
