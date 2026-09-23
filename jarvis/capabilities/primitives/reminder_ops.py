"""reminders.create / reminders.list / reminders.cancel — local Postgres
(reminders/api.py). Delivery is NOT here: see server/app.py's
GET /reminders/due and electron-app/src/main.js's poller.

Risk math (permission/risk_calculator.py: LOW=0, MEDIUM=1, HIGH=2,
CRITICAL=3, summed then clamped; permission/api.py: LOW/MEDIUM -> GRANTED,
HIGH -> CONFIRMATION_REQUIRED, CRITICAL -> DENIED — checked against the
branches, not assumed):
- reminders.create: baseline LOW (0) + WRITE (+1) + supports_undo=True (0,
  reminders.cancel below is the undo) = 1 = MEDIUM -> GRANTED. No
  confirmation for "remind me at 3pm" — cancellable and only ever pings
  the user themself.
- reminders.list: baseline LOW (0) + READ (0) + supports_undo=True (0) = 0
  = LOW -> GRANTED.
- reminders.cancel: baseline LOW (0) + WRITE (+1) + supports_undo=False
  (+1; a cancelled reminder isn't restorable, only re-creatable) = 2 =
  HIGH -> CONFIRMATION_REQUIRED. Deliberate: it deletes something.
  (A MEDIUM baseline here would be 3 = CRITICAL = DENIED — the calendar_ops
  bug — so LOW is the correct baseline for both write capabilities.)

due_at arrives as ISO 8601 from capability_invocation extraction. If the
offset is missing it is read as the user's local time (`user.timezone`),
never silently UTC. Past times are rejected — a past reminder would just
fire on the next poll and look like a bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from capabilities.registry import register
from config.loader import load_config
from contracts.capability import Capability, ParamSpec
from contracts.enums import CapabilityType, ParamType, RiskLevel
from reminders.api import cancel_reminder, create_reminder, list_reminders

_PAST_GRACE = timedelta(seconds=60)


def _parse_due(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(load_config().get("user.timezone", "UTC"))
        except Exception:
            tz = timezone.utc
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


async def _create(parameters: dict) -> dict:
    due = _parse_due(parameters["due_at"])
    if due < datetime.now(timezone.utc) - _PAST_GRACE:
        raise ValueError("That time has already passed — give me a future time for the reminder")
    rem = await create_reminder(parameters["reminder_text"], due)
    return {"reminder_id": rem.id, "text": rem.text, "due_at": rem.due_at.isoformat()}


async def _list(parameters: dict) -> dict:
    rems = await list_reminders()
    return {"reminders": [{"id": r.id, "text": r.text, "due_at": r.due_at.isoformat()} for r in rems]}


async def _cancel(parameters: dict) -> dict:
    needle = parameters["cancel_target"].strip().lower()
    pending = await list_reminders(limit=200)
    matches = [r for r in pending if needle in r.text.lower()]
    if not matches:
        raise ValueError(f"No pending reminder matches \"{parameters['cancel_target']}\"")
    if len(matches) > 1:
        names = "; ".join(f"\"{r.text}\"" for r in matches[:5])
        raise ValueError(f"\"{parameters['cancel_target']}\" matches {len(matches)} reminders ({names}) — be more specific")
    ok = await cancel_reminder(matches[0].id)
    if not ok:
        raise ValueError("That reminder was already delivered or cancelled")
    return {"reminder_id": matches[0].id, "text": matches[0].text}


register(
    Capability(
        id="reminders.create", name="Create Reminder",
        description="Sets a reminder that will notify the user with the given text at the given time.",
        category="Productivity", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["reminders.write"], supports_undo=True,
        extra_parameters={
            "reminder_text": ParamSpec(
                type=ParamType.STRING, required=True,
                description="What should I remind you about? (the reminder's own words, e.g. 'call mom')",
            ),
            "due_at": ParamSpec(
                type=ParamType.DATETIME, required=True,
                description=(
                    "When should I remind you? (full ISO 8601 datetime WITH UTC offset, converted from the "
                    "user's local time exactly as for calendar start/end; resolve relative times like "
                    "'in 20 minutes' or 'tomorrow at 9' against the current local time)"
                ),
            ),
        },
    ),
    _create,
)
register(
    Capability(
        id="reminders.list", name="List Reminders",
        description="Lists the user's pending (not yet delivered) reminders.",
        category="Productivity", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["reminders.read"], supports_undo=True,
    ),
    _list,
)
register(
    Capability(
        id="reminders.cancel", name="Cancel Reminder",
        description="Cancels one pending reminder, identified by words from its text.",
        category="Productivity", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["reminders.write"], supports_undo=False,
        extra_parameters={
            "cancel_target": ParamSpec(
                type=ParamType.STRING, required=True,
                description="Which reminder should I cancel? (a word or phrase from its text)",
            ),
        },
    ),
    _cancel,
)
