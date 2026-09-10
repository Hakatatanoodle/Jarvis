"""
Shared enumerations used across every §6 contract in the Jarvis V0 implementation prompt.
Kept in one place so no two contracts silently define competing string sets for the
same concept (this is exactly the drift Jarvis_Contract_Review_v1.md flagged in RFC-015).
"""
from enum import Enum


class MissionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class GoalType(str, Enum):
    LIFE_GOAL = "LifeGoal"
    PROJECT = "Project"
    TASK = "Task"
    HABIT = "Habit"


class GoalStatus(str, Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    ARCHIVED = "Archived"
    CANCELLED = "Cancelled"


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class MemoryType(str, Enum):
    PREFERENCE = "Preference"
    FACT = "Fact"
    SKILL = "Skill"
    RELATIONSHIP = "Relationship"
    CONSTRAINT = "Constraint"


class ParamType(str, Enum):
    """V1 generic-parameter mechanism (2026-09-07): the shape a
    Capability.extra_parameters entry can declare. See
    contracts/capability.py's ParamSpec docstring for the full
    design rationale."""
    STRING = "string"
    ENUM = "enum"
    DATETIME = "datetime"
    BOOL = "bool"


class Importance(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class MemoryStatus(str, Enum):
    ACTIVE = "Active"
    ARCHIVED = "Archived"
    FORGOTTEN = "Forgotten"


class ContextSourceType(str, Enum):
    GOAL = "Goal"
    MEMORY = "Memory"
    KNOWLEDGE_NODE = "KnowledgeNode"
    EVENT = "Event"


class PlanStatus(str, Enum):
    DRAFT = "Draft"
    APPROVED = "Approved"
    EXECUTING = "Executing"
    COMPLETED = "Completed"
    PAUSED = "Paused"
    CANCELLED = "Cancelled"


class ActionType(str, Enum):
    READ = "Read"
    WRITE = "Write"
    COMPUTE = "Compute"
    COMMUNICATE = "Communicate"


class ActionStatus(str, Enum):
    PENDING = "Pending"
    READY = "Ready"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class CapabilityType(str, Enum):
    PRIMITIVE = "primitive"
    COMPOSITE = "composite"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"


class ConfirmationStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class LearningCategory(str, Enum):
    USER = "user"
    STRATEGY = "strategy"
    RELATIONSHIP = "relationship"


class LearningStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVOKED = "revoked"


class InsightMode(str, Enum):
    REFLECTION = "reflection"
    REVIEW = "review"


class InsightScope(str, Enum):
    # V0 (§5.1) only implements weekly. The other members exist so §5.3's
    # future scopes don't require an enum migration later, but nothing in
    # V0 code may produce anything except WEEKLY.
    WEEKLY = "weekly"
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    PROJECT_COMPLETION = "project_completion"
    MAJOR_LIFE_EVENT = "major_life_event"
