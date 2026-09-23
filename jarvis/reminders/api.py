"""Reminder repository — the only writer of the `reminder` table (same
boundary rule as goals/api.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from contracts.reminder import Reminder, ReminderStatus
from infra.storage import connection


def _row(r) -> Reminder:
    return Reminder(
        id=str(r["id"]), text=r["text"], due_at=r["due_at"], status=ReminderStatus(r["status"]),
        created_at=r["created_at"], delivered_at=r["delivered_at"],
    )


async def create_reminder(text: str, due_at: datetime) -> Reminder:
    rem = Reminder(text=text, due_at=due_at)  # validates blank text / naive datetime
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO reminder (id, text, due_at, status, created_at) VALUES ($1,$2,$3,$4,$5);",
            rem.id, rem.text, rem.due_at, rem.status.value, rem.created_at,
        )
    return rem


async def list_reminders(status: Optional[ReminderStatus] = ReminderStatus.PENDING, limit: int = 20) -> list[Reminder]:
    async with connection() as conn:
        if status is None:
            rows = await conn.fetch("SELECT * FROM reminder ORDER BY due_at LIMIT $1;", limit)
        else:
            rows = await conn.fetch(
                "SELECT * FROM reminder WHERE status = $1 ORDER BY due_at LIMIT $2;", status.value, limit
            )
    return [_row(r) for r in rows]


async def pop_due_reminders(now: Optional[datetime] = None) -> list[Reminder]:
    """Atomically mark every pending reminder with due_at <= now as
    delivered and return them (oldest first). Single UPDATE...RETURNING,
    so concurrent polls (focus + interval firing together) cannot both
    receive the same reminder."""
    now = now or datetime.now(timezone.utc)
    async with connection() as conn:
        rows = await conn.fetch(
            "UPDATE reminder SET status = 'delivered', delivered_at = $1 "
            "WHERE status = 'pending' AND due_at <= $1 RETURNING *;",
            now,
        )
    return sorted((_row(r) for r in rows), key=lambda r: r.due_at)


async def cancel_reminder(reminder_id: str) -> bool:
    """True if a pending reminder was cancelled, False if it was
    missing or already delivered/cancelled."""
    async with connection() as conn:
        res = await conn.execute(
            "UPDATE reminder SET status = 'cancelled' WHERE id = $1 AND status = 'pending';", reminder_id
        )
    return res.endswith(" 1")
