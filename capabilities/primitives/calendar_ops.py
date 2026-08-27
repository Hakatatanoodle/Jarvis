"""calendar.read_events / calendar.create_event — local calendar store
(0011_calendar_event.sql). create_event is the deliberate proof of the
confirmation path (§5.1): Medium baseline + Write type + no live undo
wiring yet pushes computed_risk to High -> confirmation_required."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel
from capabilities.registry import register
from infra.storage import connection


async def _read_events(parameters: dict) -> dict:
    start = datetime.fromisoformat(parameters["start"])
    end = datetime.fromisoformat(parameters["end"])
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT id, title, start_at, end_at FROM calendar_event "
            "WHERE start_at >= $1 AND start_at <= $2 ORDER BY start_at ASC;",
            start, end,
        )
    return {"events": [
        {"id": str(r["id"]), "title": r["title"], "start": r["start_at"].isoformat(), "end": r["end_at"].isoformat()}
        for r in rows
    ]}


async def _create_event(parameters: dict) -> dict:
    event_id = str(uuid4())
    now = datetime.now(timezone.utc)
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO calendar_event (id, title, start_at, end_at, created_at) VALUES ($1,$2,$3,$4,$5);",
            event_id, parameters["title"],
            datetime.fromisoformat(parameters["start"]), datetime.fromisoformat(parameters["end"]), now,
        )
    return {"event_id": event_id}


register(
    Capability(
        id="calendar.read_events", name="Read Calendar Events", description="Lists events in a date range.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["calendar.read"], supports_undo=True,
    ),
    _read_events,
)
register(
    Capability(
        id="calendar.create_event", name="Create Calendar Event", description="Creates a new calendar event.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.MEDIUM, required_permissions=["calendar.write"], supports_undo=True,
    ),
    _create_event,
)
