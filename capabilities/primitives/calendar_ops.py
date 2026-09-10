"""calendar.read_events / calendar.create_event — M5: real Google
Calendar, via the calendar-mcp server (infra/mcp_client.py), replacing
the old local `calendar_event` Postgres table entirely. Same two
capability ids as before (test_m5_capabilities.py's
test_five_primitives_registered pins this exact set) — this is a
same-shape replacement, not a new/additional capability, per the M5
handover's framing.

Tool names and argument shapes below (`list-events`, `create-event`,
`timeMin`/`timeMax`, `account`, `calendarId`) are taken from upstream's
own docs (docs/advanced-usage.md, docs/multi-account-updates.md), NOT
observed against a live server — this sandbox has no path to a running
calendar-mcp instance or real Google OAuth (see infra/mcp_client.py's
own docstring). Re-check both tool names and the response shape this
module assumes (`{"events": [...]}` for list-events; an object with an
`id` field for create-event) against `list_tools()` on an actual
running server before this ships — the M5 handover's own instruction
("check its actual tool list, don't assume it matches the old local
capability's exact shape") applies here more than anywhere else in this
file.

Risk levels re-derived for M5, NOT inherited from the old local-table
version (per the handover's explicit instruction):
- calendar.read_events: unchanged at LOW / `calendar.read` — reading
  someone's real calendar is no more dangerous than reading the old
  fake one; risk_calculator.py's ActionType.READ never bumps.
- calendar.create_event: baseline **LOW** / `calendar.write` /
  `supports_undo=False`. permission/risk_calculator.py's scale is
  additive-ordinal (LOW=0, MEDIUM=1, HIGH=2, CRITICAL=3, clamped to 3):
  WRITE contributes +1 (impact_bump), no-undo contributes +1
  (reversibility_bump). LOW(0) + 1 + 1 = 2 = HIGH ->
  `CONFIRMATION_REQUIRED` — the intended behavior (friction before a
  write lands on a calendar the person actually looks at, but not an
  outright block). **A baseline of MEDIUM(1), which an earlier pass of
  this record incorrectly documented, computes to 1+1+1=3=CRITICAL ->
  `DENIED` instead — permission/api.py maps CRITICAL to an outright
  deny, not a confirmation prompt (verified directly against
  permission/api.py's computed_risk branch, not assumed).** This was
  caught live (2026-09-01) via a real `risk=Critical`/denied Action in
  the person's own session — see the post-ship fix entry below for the
  full story; captured correctly here now, not just fixed and left
  undocumented in the diff.
- Deliberately scoped to events with NO attendees for M5 (`attendees`
  is not an accepted parameter here). Inviting real people is a
  materially different action — visible to others, not undoable by
  deleting the event afterward — and folding it into this same
  MEDIUM/HIGH tier without its own parameter-level risk logic
  (risk_calculator.py's still-unused `parameter_bump` hook) would
  under-represent it. Left for a later milestone to add deliberately,
  not silently supported here.
"""
from __future__ import annotations

from typing import Any, Optional

from contracts.capability import Capability, ParamSpec
from contracts.enums import CapabilityType, ParamType, RiskLevel
from capabilities.registry import register
from infra.mcp_client import call_calendar_tool

_DEFAULT_CALENDAR_ID = "primary"

# 2026-09-07: first real use of the generic extra_parameters mechanism
# (contracts/capability.py's ParamSpec) — recurrence didn't exist at
# all before this; adding it as a declared field instead of new
# hardcoded extraction/dispatch code is exactly the scaling problem
# this mechanism was built to solve (see V1_M5_IMPLEMENTATION_RECORD.md's
# 2026-09-07 entry). Value is one of Google Calendar's own RRULE FREQ
# values, kept to the two most common cases plus explicit "none" —
# monthly/yearly/custom intervals are a straightforward follow-up (just
# more `choices` + one more _RRULE_BY_RECURRENCE entry), not a new
# mechanism.
_RRULE_BY_RECURRENCE = {"daily": "RRULE:FREQ=DAILY", "weekly": "RRULE:FREQ=WEEKLY"}


def _account_kwargs(parameters: dict) -> dict[str, Any]:
    """account is always optional and passed straight through verbatim
    (never invented/validated here — capability_invocation/extraction.py
    is where an account_ref gets checked against config/default.yaml's
    known nicknames before it ever reaches an executor). Omitting it
    lets calendar-mcp itself decide: merge all accounts for a read,
    auto-select the writable one for a write (both documented upstream
    behaviors) — that's a reasonable default while only one account is
    connected, and a natural fit once a second one is.
    """
    account = parameters.get("account")
    return {"account": account} if account else {}


async def _read_events(parameters: dict) -> dict:
    args = {
        "timeMin": parameters["start"],
        "timeMax": parameters["end"],
        "calendarId": parameters.get("calendar_id", _DEFAULT_CALENDAR_ID),
        **_account_kwargs(parameters),
    }
    result = await call_calendar_tool("list-events", args)
    events = result.get("events", result.get("result", []))
    return {"events": events}


def _recurrence_kwargs(parameters: dict) -> dict[str, Any]:
    """recurrence is optional and one of "daily"/"weekly"/"none" (see
    this Capability's registration below) — "none" and omitted are
    both "not recurring," matching account_ref's "omission is a valid
    choice" precedent right above. Passed to calendar-mcp as an RRULE
    array, the shape upstream's own create-event tool documents."""
    recurrence = parameters.get("recurrence")
    if not recurrence or recurrence == "none":
        return {}
    return {"recurrence": [_RRULE_BY_RECURRENCE[recurrence]]}


async def _create_event(parameters: dict) -> dict:
    args = {
        "summary": parameters["title"],
        "start": parameters["start"],
        "end": parameters["end"],
        "calendarId": parameters.get("calendar_id", _DEFAULT_CALENDAR_ID),
        **_account_kwargs(parameters),
        **_recurrence_kwargs(parameters),
    }
    result = await call_calendar_tool("create-event", args)
    # Confirmed against a real live create-event response (2026-09-06):
    # the actual event object is nested under an "event" key
    # ({"event": {"id": ..., "htmlLink": ..., ...}}), not flat at the
    # top level as this module originally assumed (that assumption was
    # explicitly flagged as unverified from launch — see this file's
    # module docstring). Both shapes checked, nested-first, so a future
    # server version returning the flatter shape still works too.
    event = result.get("event", result)
    event_id = event.get("id") or event.get("eventId")
    return {"event_id": event_id, "html_link": event.get("htmlLink"), "raw": result}


register(
    Capability(
        id="calendar.read_events", name="Read Calendar Events",
        description="Lists events in a date range from the person's real Google Calendar.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["calendar.read"], supports_undo=True,
    ),
    _read_events,
)
register(
    Capability(
        id="calendar.create_event", name="Create Calendar Event",
        description="Creates a new event on the person's real Google Calendar. No attendees/invites in M5.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["calendar.write"], supports_undo=False,
        extra_parameters={
            "recurrence": ParamSpec(
                type=ParamType.ENUM, required=False, choices=["daily", "weekly", "none"],
                description="How often the event repeats — \"daily\", \"weekly\", or \"none\" for a one-off event.",
            ),
        },
    ),
    _create_event,
)
