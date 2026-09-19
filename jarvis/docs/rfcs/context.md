# Context Engine

## CE-01 — Definition ✅ LOCKED

The Context Engine is responsible for:

- Gathering
- Filtering
- Organizing

the **minimum relevant information** required to complete the current task.

It does **not** reason.

It prepares context.

---

## CE-02 — Intent-Based Retrieval ✅ LOCKED

Context retrieval always begins with **Intent Detection**.

Every user request is first classified into an intent.

Examples:

- Planning
- Programming
- Research
- Reflection
- Email
- Scheduling
- Learning
- Health

The detected intent determines which retrieval policy will be used.

---

## CE-03 — Retrieval Policies ✅ LOCKED

Every intent has a dedicated **Retrieval Policy**.

A Retrieval Policy defines **candidate sources**.

Example:

### Planning

Retrieve candidates from:

- Goals
- Calendar
- Tasks
- Current Mode
- User Model
- Energy

---

Programming

Retrieve candidates from:

- Current repository
- Open files
- Documentation
- Coding preferences
- Project knowledge

---

Reflection

Retrieve candidates from:

- Journal
- Memories
- Goal progress
- Mood history
- User Model

A Retrieval Policy **does not decide what is finally sent** to the LLM.

It only gathers candidates.

---

## CE-04 — Candidate Retrieval ✅ LOCKED

The Context Engine retrieves **candidate context**.

It never attempts to determine importance.

Its responsibility ends after collecting relevant candidates.

---

## CE-05 — Decision Engine Ranking ✅ LOCKED

The **Decision Engine** evaluates all retrieved candidates.

It ranks them based on factors such as:

- Relevance
- Goal alignment
- Current mode
- User state
- Freshness
- Confidence

The highest-ranked information is selected.

The Context Engine does **not** perform ranking.

---

## CE-06 — Minimal Context Principle ✅ LOCKED

Jarvis should always send the **minimum sufficient context** required to solve the task.

More context is **not** automatically better.

Adding unnecessary information should be avoided.

This becomes one of Jarvis's core architectural principles.

---

## CE-07 — Context Sources ✅ LOCKED

Context may only be retrieved from approved sources.

Current approved sources are:

- Memory
- Goals
- Knowledge
- User Model
- Current conversation
- Calendar
- Active applications
- Files
- Environment
- Current Mode

Future sources can be added through architecture updates.

---

## CE-08 — Freshness ✅ LOCKED

Every context item includes temporal information.

The Decision Engine should consider:

- Freshness
- Relevance

Recent information is generally preferred unless older information is explicitly required.

---

## CE-09 — Explainability ✅ LOCKED

Jarvis must always be able to explain why a context item was included.

Example:

> "I included your basketball goal because today's schedule contains basketball practice."
> 

Context selection must never be a black box.

# CE-10 — Context Budget ✅ LOCKED

### Decision

Every request has a **Context Budget**.

A Context Budget defines the maximum amount of context that should be provided to the LLM for a particular request.

The budget is **not fixed**.

It is determined dynamically based on:

- Intent
- Selected LLM
- Available context window
- Task complexity
- Performance requirements
- Cost considerations (API usage)

The Decision Engine selects the most valuable information that fits within the allocated budget.

The Orchestrator is responsible for assigning the budget.
