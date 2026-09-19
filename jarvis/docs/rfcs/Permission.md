# Permission

## PS-01 — Separate Permission System ✅ LOCKED

The Permission System is an independent subsystem.

Every action with side effects must pass through it before execution.

---

## PS-02 — Deny by Default ✅ LOCKED

Every newly introduced capability starts in **confirmation mode**.

No automatic execution is allowed until the user explicitly grants permission.

This ensures safety while still allowing future automation.

---

## PS-03 — Fine-Grained Permissions ✅ LOCKED

Permissions are granted per capability, not globally.

Examples:

- Automatically create calendar events ✅
- Automatically send emails ❌
- Automatically rename downloads ✅
- Automatically delete files ❌

This allows users to automate exactly what they trust.

---

## PS-04 — Revocable Permissions ✅ LOCKED

Every permission can be revoked at any time.

Changes take effect immediately.

---

## PS-05 — Auditable Permissions ✅ LOCKED

Every autonomous action is logged.

Each log includes:

- Timestamp
- Action performed
- Reason
- Permission used
- Outcome

Users should always be able to review what Jarvis did.

---

## PS-06 — Permission Profiles ✅ LOCKED

Permissions may be grouped into profiles.

Examples:

- Conservative
- Balanced
- Autonomous
- Custom profiles

Profiles allow quick switching between different autonomy levels.

---

## PS-07 — Sensitive Actions ✅ LOCKED

High-impact actions require explicit permission before they can ever become automatic.

Examples include:

- Financial transactions
- Deleting files
- Publishing content
- Sending emails
- Git pushes

Sensitive actions are treated with additional caution.

---

## PS-08 — Temporary Permissions ✅ LOCKED

Permissions may have an expiration.

Examples:

- "Allow automatic Git pushes for the next hour."
- "Allow automatic scheduling during this trip."

After expiration, the permission automatically returns to confirmation mode.

---

## PV-01 — Automatic Execution After Authorization ✅ LOCKED

Once permission has been granted for a capability:

- Jarvis performs the action automatically.
- No confirmation is requested.
- The action is logged.
- The user is notified when appropriate.

Example:

✅

> Calendar event created: "Basketball Practice – 6:00 PM"
> 

Not

> May I create a calendar event?
> 

This makes automation feel seamless while remaining transparent.

---

## PV-02 — Emergency Override ✅ LOCKED

Jarvis may perform narrowly defined protective actions without waiting for confirmation.

Requirements:

- Action must minimize potential harm.
- Action must be reversible whenever possible.
- User is informed immediately afterward.
- Every emergency action is logged.

Examples:

- Save unsaved work before shutdown.
- Pause heavy background tasks when battery is critically low.
- Retry a failing authentication token before a long-running task fails.

This is **not** a general override. It exists solely to protect the user's work and system.

---

## PV-03 — Permission Learning ✅ LOCKED

Jarvis observes repeated confirmation patterns.

If a user consistently approves the same action, Jarvis may ask:

> "You've approved automatic calendar creation 23 times. Would you like me to always do this automatically?"
> 

Jarvis **never** changes permissions silently.

Automation is always initiated by explicit user approval.
