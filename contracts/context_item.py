"""implements §6.4 ContextItem

Ephemeral — never persisted, never versioned. Exists only for one reasoning
cycle (owned by the Reasoning module in V0, per RFC-006's ranking-pipeline
resolution referenced in §6.4). No status field, no update path: if
context changes, a new ContextItem is created, the old one is discarded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import ContextSourceType, Priority


class ContextItem(BaseModel):
    model_config = ConfigDict(frozen=True)  # immutable by design (§6.4)

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: ContextSourceType
    source_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    priority: Priority = Priority.MEDIUM
    reasoning: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
