# nfrastructure

# Section A — Storage

## INF-01 — Hybrid Storage ✅ AUTO-LOCK

Jarvis uses a hybrid storage architecture.

- Structured Data → PostgreSQL (or equivalent SQL database)
- Semantic Memory → Vector Database (pgvector is enough for V0)
- Files → Local file system
- Configuration → Versioned config files

No single database tries to do everything.

---

## INF-02 — Local First ✅ AUTO-LOCK

Jarvis should be **local-first**.

Cloud is optional.

If cloud disappears, Jarvis should still work (with reduced capabilities).

---

## INF-03 — Cloud Sync ✅

Cloud exists only for:

- backup
- syncing devices
- API services

Not because Jarvis depends on it.

---

## INF-04 — Versioned Storage ✅

Important data is versioned.

Goals.

Knowledge.

User Model.

Memory summaries.

Configuration.

Nothing important is silently overwritten.

---

# Section B — Multi-LLM

## INF-05 — LLM Abstraction Layer ✅

No subsystem ever talks directly to GPT, Claude, Gemini, etc.

Everything goes through:

```
Reason()
```

The Orchestrator chooses the implementation.

---

## INF-06 — Provider Agnostic ✅

Changing from Claude to another model should require almost no architectural changes.

---

## INF-07 — Automatic Fallback ✅

If:

- rate limit
- outage
- quota exhausted

Automatically switch providers.

---

## INF-08 — Local + Cloud Models ✅

Support both.

Examples:

Cloud:

- GPT
- Claude

Local:

- small offline models

The Orchestrator chooses.

# Section C — MCP / APIs

## INF-09 — Everything is a Capability ✅

GitHub.

Calendar.

Browser.

Email.

Spotify.

Everything exposes Capabilities.

Never raw APIs.

---

## INF-10 — Plugin Architecture ✅

MCPs become plugins.

Easy install.

Easy removal.

---

## INF-11 — Capability Registry ✅

Already decided.

Lock.

---

## INF-12 — API Isolation ✅

If Gmail changes,

Only Gmail plugin changes.

Nothing else.

---

# Section D — Security

## INF-13 — Secrets Management ✅

API keys are never hardcoded.

Stored securely.

---

## INF-14 — Principle of Least Privilege ✅

Plugins receive only the permissions they need.

---

## INF-15 — Encryption ✅

Sensitive local data is encrypted at rest whenever practical.

---

## INF-16 — Authentication Layer ✅

Every external service authenticates independently.

No shared credentials.

# Section E — Deployment

## INF-17 — Desktop First ✅

Version 0 targets desktop.

Linux.

Windows.

(macOS later.)

---

## INF-18 — Server Optional ✅

Running a server is optional.

Needed for:

- remote access
- phone
- cloud sync

---

## INF-19 — Modular Services ✅

Subsystems communicate through interfaces.

Not tightly coupled code.

---

## INF-20 — Offline Mode ✅

Jarvis should degrade gracefully.

Offline:

- memory
- planning
- local capabilities

Unavailable:

- cloud reasoning
- cloud APIs

# Section F — Logging

## INF-21 — Unified Logs ✅

Everything logs through one logging system.

---

## INF-22 — Log Levels ✅

Debug

Info

Warning

Error

Critical

---

## INF-23 — Observability Dashboard ✅

Future feature.

View:

- active tasks
- running workflows
- failures
- reasoning summary

---

# Section G — Backup

## INF-24 — Automatic Backups ✅

Configurable.

---

## INF-25 — Restore Support ✅

One-click restore.

---

## INF-26 — Snapshot System ✅

Before major updates,

Create snapshot.

Rollback possible.

---

# Section H — Configuration

## INF-27 — Everything Configurable ✅

Models.

Providers.

Permissions.

Memory.

Logging.

Automation.

---

## INF-28 — Human Readable Config ✅

YAML / JSON.

No binary configs.

---

## INF-29 — Versioned Configs ✅

Configs evolve.

Migration supported.

---

# Section I — Performance

## INF-30 — Lazy Loading ✅

Load only what's needed.

---

## INF-31 — Caching ✅

Cache expensive operations.

---

## INF-32 — Async First ✅

Background work should not freeze the UI.

## INF-V1 — Adaptive Model Routing ✅ LOCKED

Jarvis continuously evaluates the performance of available LLMs for different types of work.

It records metrics such as:

- Success rate
- Latency
- Cost
- Reliability
- User satisfaction (when available)

The Orchestrator uses this historical performance to dynamically select the most appropriate model for each task.

Example:

```
Coding
→ Claude

Research
→ GPT

Translation
→ Gemini

Quick offline summaries
→ Local model
```

Model routing is **adaptive**, not hardcoded. As model performance changes over time, Jarvis updates its routing decisions automatically.

---

## INF-V2 — Hybrid Authentication & Encryption ✅ LOCKED

Jarvis uses a hybrid security model.

### Primary Security

Whenever possible, Jarvis relies on the operating system's secure credential storage.

Examples:

- Windows Credential Manager
- macOS Keychain
- Linux Secret Service / GNOME Keyring

### Optional Master Password

Jarvis supports an optional Master Password for:

- Importing an existing Jarvis instance onto a new device
- Restoring encrypted backups
- Encrypted cloud synchronization
- Exporting the complete Jarvis brain
- Recovery when OS credential storage is unavailable

The Master Password is **not** required for normal day-to-day usage.

Its purpose is protecting exported and synchronized data while maintaining portability across devices.

---

## INF-V3 — Companion Mobile Application ✅ LOCKED

Desktop remains the primary execution environment for Jarvis Version 0.

A companion mobile application may be developed to provide remote access.

The companion app can:

- View tasks
- Receive notifications
- Trigger capabilities
- Approve permission requests
- Review reflections
- Monitor long-running workflows

Heavy reasoning and orchestration remain on the desktop or server when available.

The phone serves as another interface to Jarvis rather than an independent instance.

---

## INF-V4 — Open Jarvis API ✅ LOCKED

Jarvis exposes its own public API.

External applications may interact with Jarvis through well-defined interfaces rather than directly accessing internal subsystems.

Example capabilities exposed through the API include:

- Create or update goals
- Store memories
- Query knowledge
- Trigger capabilities
- Request plans
- Start workflows
- Retrieve reflections
- Monitor action status

Internal architecture remains encapsulated.

External software communicates only with the public API, preserving modularity and allowing Jarvis to evolve internally without breaking integrations.
