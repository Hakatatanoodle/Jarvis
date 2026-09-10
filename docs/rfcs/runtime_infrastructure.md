## RFC-015 — State Machines

Every subsystem has explicit states.

Examples:
```
Goal:
Draft → Active → Paused → Completed → Archived

Plan:
Draft → Approved → Executing → Completed

Action:
Pending → Running → Completed / Failed

Workflow:
Created → Running → Waiting → Completed

Memory:
Extracted → Verified → Active → Archived

Capability:
Registered → Enabled → Deprecated
```
Rule:
- Every transition is validated.
- Invalid transitions are rejected.

---

# RFC-016 — Execution Flow

Standard execution pipeline.
```
User Request
      ↓
Orchestrator
      ↓
Context Engine
      ↓
Decision Engine
      ↓
Planner
      ↓
Plan
      ↓
Action Engine
      ↓
Capability
      ↓
Capability Result
      ↓
Events
      ↓
Learning
```
This is **the only official execution pipeline**.

---

# RFC-017 — Scheduler

Background execution.
Responsible for:
- reflections
- reviews
- memory consolidation
- goal review
- backups
- retries
- reminders

Supports
```
Immediate

Scheduled

Recurring

Event Triggered
```

---

# RFC-018 — Error & Recovery

Every failure has a strategy.
```
Retry

↓

Fallback Model

↓

Alternative Capability

↓

Ask User

↓

Abort
```
Nothing silently fails.
Everything gets logged.

---

# RFC-019 — LLM Router

Chooses the model.

Inputs:
- task complexity
- cost
- latency
- context size
- historical success
- rate limits

Outputs:
```
Model

Temperature

Max Tokens
```
Supports:
- multiple providers
- fallback models
- offline models
- model performance history

Exactly what we designed months ago.

---

# RFC-020 — Plugin System

Plugins provide capabilities.

Every plugin declares
```
Capabilities

Permissions

Configuration

Version

Dependencies
```
Plugins never bypass contracts.

---

# RFC-021 — Configuration

Everything configurable lives here.

Examples:
- default model
- fallback models
- context budget
- reflection schedule
- review schedule
- permissions
- API keys
- UI preferences

Supports profiles.

Example:
```
Locked In

Creative

Sports

Study

Travel
```

---

# RFC-022 — Observability

Jarvis observes itself.

Stores
- logs
- metrics
- traces
- token usage
- API cost
- latency
- failures
- success rate

Every workflow gets a trace.

---

# RFC-023 — Security

Defines
- secrets
- encryption
- authentication
- authorization
- sandbox
- local/cloud boundaries

Passwords are never stored in plain text.
Sensitive capabilities require elevated permission.

---

# RFC-024 — Persistence

Defines what survives restart.

Persistent
- Mission
- Goals
- Memory
- Knowledge
- Settings
- Permissions
- Learning
- Logs

Temporary
- ContextItems
- Running prompts
- LLM responses
- Cache

---

# RFC-025 — Startup / Shutdown

Startup
```
Load Configuration

↓

Connect Database

↓

Load Plugins

↓

Restore Scheduler

↓

Start Event Bus

↓

Ready
```

Shutdown
```
Finish Running Actions

↓

Flush Logs

↓

Save State

↓

Close Connections
```
Graceful by default.
