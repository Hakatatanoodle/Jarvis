"""Single import point that registers every primitive capability. Callers
(tests, action_engine) import this once before touching the registry."""
import capabilities.primitives.calendar_ops  # noqa: F401
import capabilities.primitives.file_ops  # noqa: F401
import capabilities.primitives.goal_ops  # noqa: F401
