"""V1-M3 follow-up (2026-08-15b): a persisted record of memory-worthy
content that didn't fit the existing MemoryType taxonomy. See
infra/migrations/0017_taxonomy_gap_proposal.sql for why this exists —
architect review required the gap to leave a durable trail instead of
just a log line. Deliberately not a queue/workflow object (no status
field): promoting one of these to an actual taxonomy category is a
human/architect decision, not something this record drives on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class TaxonomyGapProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    value: str
    raw_user_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
