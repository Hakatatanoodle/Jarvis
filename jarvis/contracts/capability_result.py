"""implements §6.10 CapabilityResult

The standardized output of any Capability execution. Kept deliberately
plain — separating Action (the request) from CapabilityResult (the
outcome) is what keeps execution history clean per RFC-012.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CapabilityResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    action_id: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    execution_time: str = ""
    tokens_used: int = Field(default=0, ge=0)
    credits_used: int = Field(default=0, ge=0)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
