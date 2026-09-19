# Planner

### PL-01 — Separate Planner ✅

Planning is a dedicated subsystem.

The Decision Engine decides **what** should happen.

The Planner decides **how to achieve it**.

---

### PL-02 — Hierarchical Planning ✅

Plans are recursive.

Example:

```
Become Game Developer
    ↓
Master C++
    ↓
Finish DSA
    ↓
Complete Today's Problems
```

Every plan can be decomposed into smaller plans.

---

### PL-03 — Dependency Graph ✅

Tasks may depend on other tasks.

Example:

```
Publish YouTube Video

├── Record
├── Edit
├── Thumbnail
└── Upload
```

Jarvis understands dependencies.

---

### PL-04 — Dynamic Replanning ✅

If circumstances change...

The Planner updates the plan.

Examples:

- Deadline changes
- Task completed early
- New priority arrives
- User changes goals

Plans are living objects.

---

### PL-05 — Multiple Time Horizons ✅

The Planner supports:

- Immediate (minutes)
- Daily
- Weekly
- Monthly
- Quarterly
- Long-term

---

### PL-06 — Progress Tracking ✅

Every plan knows:

- completion percentage
- completed tasks
- remaining tasks
- blockers

---

### PL-07 — Constraint Awareness ✅

Planning considers:

- calendar
- deadlines
- energy
- permissions
- budget
- dependencies

Not just goals.

---

### PL-08 — Reusable Templates ✅

Frequently used plans can become templates.

Examples:

- Exam Preparation
- Hackathon
- YouTube Video
- Semester Startup

# PL-09 — User Override Principle ✅ LOCKED

Jarvis optimizes decisions according to its planning priorities:

1. Mission Alignment
2. Goal Progress
3. Health & Sustainability
4. User Preferences
5. Efficiency

However...

> **The user always retains final authority.**
> 

When the user's request conflicts with these priorities, Jarvis should:

1. Detect the conflict.
2. Clearly explain the consequences.
3. Recommend an alternative if appropriate.
4. Ask for confirmation.
5. Execute the request if the user explicitly confirms.

Example:

> **User:** Delete the Jarvis database.
> 

> **Jarvis:** This action will permanently remove your memories, goals, project history, and decision logs. This conflicts with your long-term mission and cannot be fully undone. Are you sure?
> 

> **User:** Yes.
> 

> **Jarvis:** Understood. Executing the request.
> 

This is exactly the balance we've been aiming for since the Constitution meeting:

- Jarvis is **not** blindly obedient.
- Jarvis is **not** controlling.
- Jarvis advises, warns, and protects—but ultimately respects your autonomy.

I honestly think this is one of the defining characteristics of your Jarvis.

---

# PL-10 — Suggestive Planning ✅ LOCKED

The Planner proposes plans.

It does not silently schedule or restructure the user's life.

Example:

> "Based on your goals and calendar, I recommend this schedule for tomorrow."
> 

The user may:

- Accept it.
- Modify it.
- Reject it.

If accepted and the necessary permissions exist, Jarvis executes the plan.

Otherwise, it remains a recommendation.
