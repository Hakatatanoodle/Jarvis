# Capability

## CAP-01 — Capability Abstraction ✅

A Capability represents **what Jarvis can accomplish**, independent of implementation.

Example:

```
Capability

Send Email

↓

Could use:

- Gmail API
- Outlook API
- SMTP
- Future Provider
```

The rest of Jarvis never cares which provider is used.

---

## CAP-02 — Tool Independence ✅

Capabilities never directly expose tools.

Tools are implementation details.

This allows replacing providers without changing the rest of the architecture.

---

## CAP-03 — Capability Contract ✅

Every capability has a standardized contract.

Minimum fields:

- Name
- Description
- Required Inputs
- Optional Inputs
- Required Permissions
- Expected Outputs
- Failure Modes
- Reversible? (Yes/No)

Every capability follows this structure.

---

## CAP-04 — Multiple Implementations ✅

One capability may have multiple implementations.

Example:

Capability:

> Search Repository
> 

Implementations:

- GitHub MCP
- Local Git Repository
- Git CLI
- Future Provider

The Orchestrator chooses the implementation.

---

## CAP-05 — Capability Discovery ✅

Jarvis always knows what capabilities currently exist.

New capabilities can be added without changing the architecture.

Capabilities become plugins rather than hardcoded features.

---

## CAP-06 — Capability Versioning ✅

Capabilities are versioned.

Example:

```
Create Calendar Event

v1

↓

v2

↓

v3
```

Older workflows remain compatible whenever possible.

---

## CAP-07 — Capability Metadata ✅

Every capability stores metadata.

Examples:

- Risk level
- Average latency
- Cost
- Required internet
- Local/Cloud
- Typical reliability

This helps the Orchestrator make smarter choices.

---

## CAP-08 — Capability Composition ✅

Capabilities can call other capabilities.

Example:

```
Organize Week

↓

Read Calendar

↓

Read Tasks

↓

Create Events

↓

Notify User
```

Complex abilities are built from smaller capabilities.

---

## CAP-09 — Capability Health ✅

Each capability has a health status.

Examples:

- Healthy
- Degraded
- Offline
- Updating

The Orchestrator avoids unhealthy capabilities when alternatives exist.

---

## CAP-10 — Capability Registry ✅

All capabilities are stored in a central registry.

The registry becomes Jarvis's catalogue of available abilities.

### CAP-11 — Capability Types

Jarvis supports two capability types:

- **Primitive Capabilities** — implemented by code and tools.
- **Composite Capabilities** — user-approved workflows composed of existing capabilities.

## CV-01 — Capability Categories ✅ LOCKED

Capabilities are grouped into categories.

Examples:

- Communication
- Productivity
- Development
- Learning
- Health
- Finance
- System
- Media

Categories improve organization and discovery.

---

## CV-02 — User-Taught Capabilities ✅ LOCKED

I **love** your refinement. It's much smarter than my original proposal.

### Final Decision

Jarvis does **not** automatically create new capabilities.

Instead, it observes repeated workflows.

Example:

Over two months, it notices you repeatedly do:

1. Export notes.
2. Summarize notes.
3. Email yourself.
4. Archive the folder.

Jarvis says:

> "I've noticed you've performed this workflow 14 times. Would you like me to save it as a reusable capability called 'Weekly Review'?"
> 

Only after your approval does it become a new capability.

That keeps the capability library intentional instead of filling it with one-off automations.

---

## CV-03 — Capability Marketplace ✅ LOCKED

The architecture supports installing third-party capabilities.

Examples:

- Obsidian
- Blender
- Spotify
- Notion
- Home Assistant

Marketplace support is **not required for Version 0**, but the architecture should not prevent it.
