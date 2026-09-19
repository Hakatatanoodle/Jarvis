# Reflection engine

## RE-01 — Definition ✅ LOCKED

The Reflection Engine is an independent subsystem responsible for reviewing past actions, decisions, behaviors, and outcomes to improve Jarvis over time.

It does **not** respond directly to user requests.

It performs meta-analysis on the system itself and the user's progress.

---

## RE-02 — Independent Execution ✅ LOCKED

The Reflection Engine runs asynchronously.

It never blocks conversations or task execution.

Reflections may occur:

- On a schedule
- After major events
- When manually requested

---

## RE-03 — Reflection Logging ✅ LOCKED

Every reflection is recorded.

Each log contains:

- Timestamp
- Trigger
- Evidence
- Findings
- Proposed improvements
- User response (if applicable)

Reflection history should always remain available.

---

## RE-04 — Scope of Influence ✅ LOCKED

The Reflection Engine may propose updates to:

- Memory
- User Model
- Recommendations
- Habits
- Workflows

It **cannot** directly modify:

- Mission
- Constitution
- Core architectural rules

Those require explicit user approval.

---

## RE-05 — Safe Updates ✅ LOCKED

Reflection never silently changes important long-term information.

Changes require either:

- High confidence according to configurable rules, or
- Explicit user approval

---

## RE-06 — Evidence First ✅ LOCKED

Every recommendation produced during reflection must include evidence.

Example:

> "You've completed 17 coding sessions in the last 21 days."
> 

Not:

> "You seem interested in programming."
> 

Evidence always comes first.

---

## RE-07 — Reflection Levels ✅ LOCKED

Reflection supports multiple timescales.

Current levels:

- Daily
- Weekly
- Monthly
- Quarterly
- Project Completion
- Major Life Event

Additional reflection types may be added later.

---

## RE-08 — Pattern Detection ✅ LOCKED

Reflection focuses on recurring patterns rather than isolated events.

Examples:

Good pattern:

- Consistent sleep improvement
- Increasing coding time
- Frequent basketball practice

Bad pattern:

- Repeated procrastination
- Missing deadlines
- Declining study consistency

Single isolated events generally do not trigger reflection.

---

## RE-09 — Proactive Reflection ✅ LOCKED

Jarvis may proactively initiate reflections.

Examples:

- Weekly review
- End-of-project review
- Monthly life review
- Habit review

Jarvis should invite the user to reflect when meaningful patterns are detected.

---

## RE-10 — Reflection Style ✅ LOCKED

Jarvis should be **very honest** during reflections.

Principles:

- Prioritize truth over comfort.
- Base every criticism on evidence.
- Never exaggerate.
- Never shame the user.
- Be direct, respectful, and actionable.

Example:

> "You planned to study 20 hours this week but completed 8. This isn't a motivation issue based on the data—you spent 11 hours on YouTube during your planned study blocks. If your goal is to become a game developer, this pattern is working against that goal."
> 

Not:

> "You're lazy."
> 

The criticism targets **behaviors**, not **identity**.
