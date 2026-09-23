"""Reminder contract (Capabilities V2). Shape mirrors contracts/goal.py.
ReminderStatus lives here, not contracts/enums.py, so no existing
contract file is touched."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ReminderStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Reminder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    text: str
    due_at: datetime  # timezone-aware, UTC
    status: ReminderStatus = ReminderStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivered_at: Optional[datetime] = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Reminder text cannot be blank")
        return v.strip()

    @field_validator("due_at")
    @classmethod
    def due_at_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Reminder.due_at must be timezone-aware")
        return v.astimezone(timezone.utc)
