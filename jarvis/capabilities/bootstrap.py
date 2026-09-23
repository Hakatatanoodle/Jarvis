"""Single import point that registers every primitive capability. Callers
(tests, action_engine) import this once before touching the registry."""
import capabilities.primitives.calendar_ops  # noqa: F401
import capabilities.primitives.fs_ops  # noqa: F401 — V1-M6, replaces file_ops.py's fixed sandbox
import capabilities.primitives.goal_ops  # noqa: F401
import capabilities.primitives.reminder_ops  # noqa: F401 — Capabilities V2
import capabilities.primitives.web_ops  # noqa: F401 — Capabilities V2
