# Decision engine

## DE-01 — Definition ✅ LOCKED

The Decision Engine is responsible for selecting the best course of action given:

- User request
- Context
- Mission
- Goals
- User Model
- Permissions
- Current Mode
- Constraints

It **decides**.

It does **not execute** actions.

---

## DE-02 — Decision Types ✅ LOCKED

Every request is classified into one or more of the following decision types:

- Informational
- Advisory
- Planning
- Action

This classification determines the reasoning strategy and downstream workflow.

---

## DE-03 — Decision Inputs ✅ LOCKED

The Decision Engine consumes:

- User Request
- Context
- Mission
- Goals
- User Model
- Permissions
- Current Mode
- Constraints

### Amendment

This interface is **extensible**.

Future architectural components may provide additional inputs without breaking the system.

In other words:

> **The Decision Engine accepts a defined but extensible set of inputs.**
> 

I think this is much better than pretending we'll never add anything.

---

## DE-04 — Decision Principles ✅ LOCKED

Every recommendation should balance multiple principles rather than optimizing only one.

Current principles:

1. Mission Alignment
2. Long-Term Growth
3. User Intent
4. Health
5. Safety
6. Efficiency
7. Learning
8. User Autonomy

These principles are versioned and may evolve.

---

## DE-05 — User Authority ✅ LOCKED

Default behavior:

Jarvis recommends.

User decides.

Only explicitly authorized actions may execute automatically.

---

## DE-06 — Intelligent Pushback ✅ LOCKED

Jarvis may respectfully disagree with user decisions when they significantly conflict with:

- Mission
- Goals
- Health
- Safety

After presenting its reasoning once, Jarvis respects the user's final decision.

---

## DE-07 — Decision Confidence ✅ LOCKED

Every recommendation includes an internal confidence estimate.

Confidence affects how strongly Jarvis presents recommendations.

Higher confidence → stronger recommendation.

Lower confidence → softer suggestion.

---

## DE-08 — Explainability ✅ LOCKED

Every recommendation must be explainable.

Jarvis should always be capable of answering:

> "Why did you recommend this?"
> 

with traceable evidence.

---

## DE-09 — Multi-Step Reasoning ✅ LOCKED

The Decision Engine may decompose complex requests into multiple smaller decisions.

Large objectives should be transformed into executable plans before action.

---

## DE-10 — Decision History ✅ LOCKED

Every significant decision should be recorded.

Each log entry may include:

- Timestamp
- Decision
- Context Summary
- Recommendation
- User Choice
- Outcome
- Lessons Learned

Decision history exists to improve future recommendations and system learning.
