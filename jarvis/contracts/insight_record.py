"""implements §6.12 InsightRecord

The permanent archive entry for one reflection or review cycle (decision
#3, §4 — one Insight Engine, two output modes, sharing this one record
shape rather than two separate systems). Weekly-only scope in V0 (§5.1);
InsightScope carries the other members for §5.3's future but nothing in
V0 may produce them.

Immutable once generated except `user_response` — that's a state-
dependent partial-mutability rule this single model can't self-enforce
(same situation as Plan's "Completed is immutable"). Real enforcement is
insight/api.py's job (M7): only a dedicated `record_user_response()` call
may touch this record after creation; every other field is write-once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from contracts.enums import InsightMode, InsightScope


class EvidenceClaim(BaseModel):
    """Every claim in an InsightRecord must trace to evidence — non-negotiable
    (§6.12, and RE-06 / RV-04's "evidence first" principle)."""

    claim: str
    supporting_ids: list[str] = Field(default_factory=list)

    @field_validator("supporting_ids")
    @classmethod
    def claim_needs_evidence(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError(
                "Every InsightRecord claim must cite at least one supporting_id "
                "(§6.12: 'every claim must trace to evidence — non-negotiable')."
            )
        return v


class InsightRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    mode: InsightMode
    scope: InsightScope = InsightScope.WEEKLY
    narrative: str
    evidence: list[EvidenceClaim] = Field(default_factory=list)
    wins: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    user_response: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("scope")
    @classmethod
    def v0_weekly_only(cls, v: InsightScope) -> InsightScope:
        if v != InsightScope.WEEKLY:
            raise ValueError(
                f"V0 only implements weekly-scope insights (§5.1). "
                f"'{v.value}' is deferred per §5.3."
            )
        return v
