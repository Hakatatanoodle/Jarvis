# Action engine

## AE-01 — Separate Subsystem ✅

The Action Engine is its own subsystem.

It receives approved actions and coordinates execution.

---

## AE-02 — Tool Abstraction ✅

The Action Engine never directly knows whether it's calling:

- Gmail
- Outlook
- GitHub
- Calendar
- Browser
- Local Python
- Shell

It only executes a **Capability**.

Capabilities decide which tools are used.

This future-proofs the architecture.

---

## AE-03 — Execution States ✅

Every action has a lifecycle.

```
Pending
↓
Running
↓
Completed

or

Failed

or

Cancelled

or

Timed Out
```

Every action is always in exactly one state.

---

## AE-04 — Action IDs ✅

Every action receives a unique ID.

This allows:

- tracking
- cancellation
- retries
- logs
- debugging

---

## AE-05 — Retry Policy ✅

Retryable failures are automatically retried according to configurable policies.

Examples:

- network timeout
- temporary API failure
- rate limit

Permanent failures are **not** retried endlessly.

---

## AE-06 — Cancellation ✅

Every long-running action should be cancellable whenever technically possible.

Example:

> Stop repository indexing.
> 

---

## AE-07 — Progress Reporting ✅

Long actions report progress.

Example:

```
Uploading...
42%

Indexing Repository...
312 / 1200 files

Summarizing Book...
```

---

## AE-08 — Action Logging ✅

Every execution is logged.

Include:

- start time
- end time
- duration
- capability
- status
- result
- error (if any)

---

## AE-09 — Parallel Actions ✅

Independent actions may execute simultaneously.

Example:

- Read calendar
- Read email
- Scan repository

No need to serialize independent work.

---

## AE-10 — Result Standardization ✅

Every capability returns a standardized result.

Example:

```
Success

Message

Data

Warnings

Errors

Metadata
```

This means every subsystem talks the same language.

## AV-01 — Intelligent Confirmation ✅ LOCKED

This is **better** than "ask once."

Jarvis should not ask based on the **number of actions**.

It should ask based on the **risk level** of the actions.

### Principle

> **Confirmation is determined by permission and risk, not by workflow size.**
> 

Example:

User:

> "Organize my week."
> 

Jarvis internally performs:

- Read calendar ✅
- Read tasks ✅
- Analyze goals ✅
- Create calendar events ✅
- Reschedule tasks ✅

No confirmation.

Why?

Because every action has already been authorized.

Now imagine:

> "Organize my week and email my professor explaining why I'll miss class."
> 

Jarvis performs:

- Organize calendar ✅
- Update tasks ✅
- Draft email ✅
- **STOP**

Then asks:

> "This will send an email to your professor. Do you want me to send it?"
> 

This is a much more intelligent model.

The user shouldn't even notice the safe internal actions.

They should only be interrupted when something genuinely important is about to happen.

**This is now our official confirmation philosophy.**

---

## AV-02 — Undo System ✅ LOCKED

Undo is a first-class architectural concept.

However...

Undo is **not** a UI button.

Undo is an **action**.

Examples:

> Undo that.
> 

> Undo the calendar change.
> 

> Revert the last file rename.
> 

> Roll back yesterday's schedule.
> 

Jarvis should resolve which action is being referred to.

If an action is reversible:

- Reverse it.
- Log the reversal.
- Update the action history.

If it is not reversible:

Jarvis explains why.

Example:

> "That email has already been delivered, so I can't undo it. I can draft a follow-up email if you'd like."
> 

I think this fits Jarvis much better than a GUI-centric "Undo" button.
