# RFC-004 — Knowledge Node

## Purpose
Represents a piece of structured knowledge that Jarvis understands and can connect to other knowledge.
Unlike Memory (personal facts), Knowledge represents concepts.

Examples:
- C++
- PostgreSQL
- Jarvis
- Basketball
- Nepal
- Linear Algebra

---

## Owner
Knowledge System

---

## Created By
- Knowledge Extractor
- User
- Learning Engine
- External Imports

---

## Updated By
Knowledge System

---

## Read By
- Context Engine
- Decision Engine
- Learning Engine
- Planner
- Insight Engine

---

## Lifecycle
```
Created
    ↓
Verified
    ↓
Active
    ↓
Updated
    ↓
Archived
```

---

## Relationships
Connected through `KnowledgeEdge`.
A node itself contains **no relationship information**.

---

## Versioned
✅ Yes

---

## Archived
✅ Yes

---

## Mutable
Yes

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| type | Concept, Person, Project, Technology, Skill, etc. |
| name | Display name |
| description | Short description |
| aliases | Alternative names |
| confidence | Confidence score |
| source_ids | Supporting evidence |
| version | Version |
| created_at | Timestamp |
| updated_at | Timestamp |

---

## Events Produced
```
KnowledgeCreated
KnowledgeUpdated
KnowledgeArchived
```

---

## Events Consumed
```
MemoryAccepted
LearningAccepted
KnowledgeImported
```

---

# JSON Schema v1
```json
{
  "id":"knowledge-node-uuid",
  "type":"Technology",
  "name":"PostgreSQL",
  "description":"Relational database used by Jarvis.",
  "aliases": ["Postgres"],
  "confidence":0.99,
  "source_ids": ["doc-12"],
  "version":1,
  "created_at":"...",
  "updated_at":"..."
}
```

---

# RFC-005 — KnowledgeEdge Contract

## Purpose
Represents a relationship between two KnowledgeNodes.
This is what makes the knowledge graph.

---

## Owner
Knowledge System

---

## Primary Fields

| Field | Description |
|---|---|
| id | UUID |
| source_node_id | Source node |
| target_node_id | Target node |
| relationship_type | DependsOn, Uses, ParentOf, SimilarTo, etc. |
| confidence | Confidence |
| source_ids | Evidence |
| version | Version |

---

## Example
```
Jarvis

USES

PostgreSQL
```
or
```
C++

IS_A

Programming Language
```

---

# JSON Schema
```json
{
  "id":"edge-uuid",
  "source_node_id":"jarvis",
  "target_node_id":"postgresql",
  "relationship_type":"USES",
  "confidence":0.96,
  "source_ids": ["architecture-doc"],
  "version":1
}
```
