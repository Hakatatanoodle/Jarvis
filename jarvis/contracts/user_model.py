"""implements §6.13 UserModel

A derived, materialized projection — never authored directly (decision
#12, §4). One row per (attribute_category, attribute_key). The one
exception: a user correction sets `override_value`, which always wins
over `computed_value`, forever, until explicitly cleared. Nothing else
writes to it. Build note: this contract exists in M0 per §9, but the
subsystem that populates it is §5.2 scope (M8) — until then, Reasoning
reads Memory directly (§5.2 explicitly allows this).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class UserModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    attribute_category: str  # e.g. "preferences", "skills", "habits"
    attribute_key: str  # e.g. "preferred_ide"
    computed_value: Any = None  # written only by the Memory System, on Memory events
    override_value: Any = None  # written only by explicit user correction
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_memory_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def effective_value(self) -> Any:
        """override_value always wins when present (decision #12, §4)."""
        return self.override_value if self.override_value is not None else self.computed_value

    # NOTE — cross-record invariant "one row per (attribute_category,
    # attribute_key)" requires a uniqueness check across the table; not
    # enforceable on a single instance. Enforced by the user_model
    # subsystem's write path (M8), not here.
