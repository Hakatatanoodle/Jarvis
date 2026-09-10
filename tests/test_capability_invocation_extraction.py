"""Unit tests for capability_invocation/extraction.py. complete_json() is
mocked — this tests the deterministic re-validation + entity-resolution
layer, not any real model, same approach as
tests/test_state_change_extraction.py."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel
from capability_invocation.extraction import extract_candidate

_CAPS = [
    Capability(
        id="goals.advance", name="Advance Goal", description="Records a review/touch on a Goal.",
        category="Productivity", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["goals.write"], supports_undo=False,
    ),
]
_KNOWN_GOALS = [
    ("Build Jarvis", "goal-1", "Active"),
    ("Run a marathon", "goal-2", "Draft"),
]


async def test_valid_capability_and_goal_resolved():
    raw = {"capability_id": "goals.advance", "goal_ref": "Build Jarvis", "note": "reviewed today", "confidence": 0.9}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("touch my build jarvis goal", capabilities=_CAPS, known_goals=_KNOWN_GOALS)
    assert c is not None
    assert c.capability_id == "goals.advance"
    assert c.match_count == 1
    assert c.resolved_goal_id == "goal-1"
    assert c.note == "reviewed today"


async def test_hallucinated_capability_id_dropped():
    # Never trust the LLM's id — must be re-checked against the live
    # registry snapshot passed in, same discipline as state_change's
    # goal_ref verbatim-match requirement.
    raw = {"capability_id": "calendar.create_event", "goal_ref": None, "note": None, "confidence": 0.8}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("schedule a meeting", capabilities=_CAPS, known_goals=_KNOWN_GOALS)
    assert c is None


async def test_null_capability_id_is_not_a_candidate():
    raw = {"capability_id": None, "goal_ref": None, "note": None, "confidence": 0.5}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("I don't feel like working on Jarvis today", capabilities=_CAPS)
    assert c is None


async def test_no_goal_match_recorded_as_zero() :
    raw = {"capability_id": "goals.advance", "goal_ref": "Nonexistent Goal", "note": None, "confidence": 0.7}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("touch my nonexistent goal", capabilities=_CAPS, known_goals=_KNOWN_GOALS)
    assert c is not None
    assert c.match_count == 0
    assert c.resolved_goal_id is None


async def test_ambiguous_goal_match_recorded_as_multiple():
    dup_goals = _KNOWN_GOALS + [("Build Jarvis", "goal-3", "Draft")]
    raw = {"capability_id": "goals.advance", "goal_ref": "Build Jarvis", "note": None, "confidence": 0.7}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("touch my build jarvis goal", capabilities=_CAPS, known_goals=dup_goals)
    assert c.match_count == 2
    assert c.resolved_goal_id is None


async def test_no_registered_capabilities_never_calls_llm():
    mock = AsyncMock(return_value={"capability_id": None})
    with patch("capability_invocation.extraction.complete_json", new=mock):
        c = await extract_candidate("touch my goal", capabilities=[])
    assert c is None
    mock.assert_not_called()


async def test_malformed_llm_response_fails_closed():
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=None)):
        c = await extract_candidate("touch my build jarvis goal", capabilities=_CAPS, known_goals=_KNOWN_GOALS)
    assert c is None


# --- M5: calendar.read_events / calendar.create_event's own fields ---

_CALENDAR_CAPS = _CAPS + [
    Capability(
        id="calendar.create_event", name="Create Calendar Event", description="Creates a real Google Calendar event.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["calendar.write"], supports_undo=False,
    ),
    Capability(
        id="calendar.read_events", name="Read Calendar Events", description="Reads real Google Calendar events.",
        category="Calendar", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["calendar.read"], supports_undo=True,
    ),
]
_KNOWN_ACCOUNTS = ["work", "personal"]


async def test_calendar_create_event_fields_resolved():
    raw = {
        "capability_id": "calendar.create_event", "goal_ref": None, "note": None,
        "title": "Lunch with Sam", "start": "2026-09-01T12:00:00+00:00", "end": "2026-09-01T13:00:00+00:00",
        "account_ref": "personal", "confidence": 0.9,
    }
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate(
            "add lunch with sam tomorrow at noon on my personal calendar",
            capabilities=_CALENDAR_CAPS, known_accounts=_KNOWN_ACCOUNTS,
        )
    assert c.capability_id == "calendar.create_event"
    assert c.title == "Lunch with Sam"
    assert c.start == "2026-09-01T12:00:00+00:00"
    assert c.end == "2026-09-01T13:00:00+00:00"
    assert c.account_match_count == 1
    assert c.resolved_account == "personal"


async def test_prompt_includes_both_utc_and_user_local_time():
    # Dogfooding fix, 2026-09-08: the prompt must show the user's LOCAL
    # time (and its offset), not just UTC — this is the actual fix for
    # the "scheduled 12pm as 17:45" bug (see V1_M5_IMPLEMENTATION_RECORD.md's
    # 2026-09-08 entry). We can't unit-test that a live LLM does the
    # conversion correctly, but we CAN pin that it's given what it needs to.
    fixed_now = datetime(2026, 9, 10, 6, 15, 0, tzinfo=timezone.utc)  # 12:00 at UTC+05:45
    captured_prompt = {}

    async def _capture(system, prompt, **kw):
        captured_prompt["value"] = prompt
        return {"capability_id": None, "goal_ref": None, "note": None, "confidence": 0.1}

    with patch("capability_invocation.extraction.complete_json", new=_capture):
        await extract_candidate(
            "schedule a studying session for 12 pm", capabilities=_CALENDAR_CAPS,
            now=fixed_now, user_timezone="Asia/Kathmandu",
        )
    prompt = captured_prompt["value"]
    assert "UTC: 2026-09-10T06:15:00+00:00" in prompt
    assert "Asia/Kathmandu" in prompt
    assert "2026-09-10T12:00:00+05:45" in prompt  # the actual local instant


async def test_unknown_user_timezone_falls_back_to_utc_without_crashing():
    captured_prompt = {}

    async def _capture(system, prompt, **kw):
        captured_prompt["value"] = prompt
        return {"capability_id": None, "goal_ref": None, "note": None, "confidence": 0.1}

    with patch("capability_invocation.extraction.complete_json", new=_capture):
        c = await extract_candidate(
            "schedule a studying session for 12 pm", capabilities=_CALENDAR_CAPS,
            user_timezone="Not/ARealZone",
        )
    assert c is None or c.capability_id is None  # doesn't crash the whole call
    assert "UTC" in captured_prompt["value"]
    assert "Not/ARealZone" not in captured_prompt["value"]  # fell back, didn't parrot the bad name


async def test_default_user_timezone_is_utc_when_not_passed():
    # Callers that don't pass user_timezone (e.g. any pre-existing test
    # or caller) keep the exact old UTC-only-anchor behavior — additive
    # fix, no forced migration.
    captured_prompt = {}

    async def _capture(system, prompt, **kw):
        captured_prompt["value"] = prompt
        return {"capability_id": None, "goal_ref": None, "note": None, "confidence": 0.1}

    with patch("capability_invocation.extraction.complete_json", new=_capture):
        await extract_candidate("hi", capabilities=_CALENDAR_CAPS)
    assert "user's LOCAL date/time (UTC)" in captured_prompt["value"]


async def test_calendar_no_account_specified_is_not_an_error():
    # Omitting account is a normal, meaningful case (see calendar_ops.py)
    # — not a 0-match/ambiguous situation.
    raw = {
        "capability_id": "calendar.read_events", "goal_ref": None, "note": None,
        "title": None, "start": "2026-09-01T00:00:00+00:00", "end": "2026-09-08T00:00:00+00:00",
        "account_ref": None, "confidence": 0.8,
    }
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("what's on my calendar this week", capabilities=_CALENDAR_CAPS, known_accounts=_KNOWN_ACCOUNTS)
    assert c.account_ref is None
    assert c.account_match_count == 0
    assert c.resolved_account is None


async def test_calendar_unknown_account_ref_recorded_as_zero_match():
    raw = {
        "capability_id": "calendar.read_events", "goal_ref": None, "note": None,
        "title": None, "start": "2026-09-01T00:00:00+00:00", "end": "2026-09-08T00:00:00+00:00",
        "account_ref": "side-hustle", "confidence": 0.7,
    }
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate(
            "what's on my side-hustle calendar this week", capabilities=_CALENDAR_CAPS, known_accounts=_KNOWN_ACCOUNTS,
        )
    assert c.account_match_count == 0
    assert c.resolved_account is None


async def test_non_iso_start_end_dropped_not_passed_through():
    # A model hallucinating "tomorrow" verbatim into start/end (instead
    # of resolving it) must not reach calendar_ops.py's executors as a
    # bogus MCP argument — dispatch.py's clarify gate is what catches a
    # None here, not calendar_ops.py silently sending garbage upstream.
    raw = {
        "capability_id": "calendar.create_event", "goal_ref": None, "note": None,
        "title": "Standup", "start": "tomorrow at noon", "end": "tomorrow at 1pm",
        "account_ref": None, "confidence": 0.6,
    }
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("add standup tomorrow at noon", capabilities=_CALENDAR_CAPS)
    assert c.start is None
    assert c.end is None


async def test_calendar_fields_absent_for_non_calendar_capability():
    raw = {"capability_id": "goals.advance", "goal_ref": "Build Jarvis", "note": None, "confidence": 0.9}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("touch my build jarvis goal", capabilities=_CALENDAR_CAPS, known_goals=_KNOWN_GOALS)
    assert c.title is None
    assert c.start is None
    assert c.end is None
    assert c.account_ref is None


# --- generic-parameter mechanism (2026-09-07) ---
#
# Uses a throwaway capability declaring extra_parameters rather than a
# real one, same isolation principle _CAPS/_CALENDAR_CAPS already use —
# these tests are about extraction.py's generic validator, not about
# any specific capability's fields.
from contracts.capability import ParamSpec  # noqa: E402
from contracts.enums import ParamType  # noqa: E402

_EXTRA_PARAM_CAPS = [
    Capability(
        id="test.fake_extra_param", name="Fake Extra Param Op", description="Test double only.",
        category="Test", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=[], supports_undo=True,
        extra_parameters={
            "flavor": ParamSpec(type=ParamType.ENUM, required=True, choices=["vanilla", "chocolate"], description="flavor"),
        },
    ),
]


async def test_valid_extra_enum_value_kept():
    raw = {"capability_id": "test.fake_extra_param", "goal_ref": None, "note": None, "confidence": 0.9,
           "extra": {"flavor": "chocolate"}}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("do the fake thing, chocolate", capabilities=_EXTRA_PARAM_CAPS)
    assert c.extra == {"flavor": "chocolate"}


async def test_extra_value_outside_declared_choices_dropped():
    # A model hallucinating an out-of-enum value must not reach
    # dispatch.py/the executor as a bogus parameter — same
    # fail-closed discipline as non_iso_start_end above.
    raw = {"capability_id": "test.fake_extra_param", "goal_ref": None, "note": None, "confidence": 0.6,
           "extra": {"flavor": "strawberry"}}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("do the fake thing, strawberry", capabilities=_EXTRA_PARAM_CAPS)
    assert c.extra == {}


async def test_extra_field_belonging_to_other_capability_dropped():
    # A field only declared under a different capability_id than the
    # one actually selected must be filtered out, not carried through.
    raw = {"capability_id": "goals.advance", "goal_ref": "Build Jarvis", "note": None, "confidence": 0.9,
           "extra": {"flavor": "chocolate"}}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate(
            "touch my build jarvis goal", capabilities=_CAPS + _EXTRA_PARAM_CAPS, known_goals=_KNOWN_GOALS,
        )
    assert c.extra == {}


async def test_missing_extra_object_defaults_to_empty_dict():
    raw = {"capability_id": "test.fake_extra_param", "goal_ref": None, "note": None, "confidence": 0.7}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("do the fake thing", capabilities=_EXTRA_PARAM_CAPS)
    assert c.extra == {}


async def test_recurrence_extra_param_resolved_for_real_calendar_capability():
    # First real user of the mechanism — exercises it against the
    # actual registered calendar.create_event Capability, not a fake.
    import capabilities.primitives.calendar_ops  # noqa: F401 — registers calendar.create_event
    from capabilities.registry import get as get_capability

    real_capability, _ = get_capability("calendar.create_event")
    raw = {
        "capability_id": "calendar.create_event", "goal_ref": None, "note": None,
        "title": "Standup", "start": "2026-09-01T09:00:00+00:00", "end": "2026-09-01T09:15:00+00:00",
        "account_ref": None, "confidence": 0.9, "extra": {"recurrence": "daily"},
    }
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("add a daily standup at 9am", capabilities=[real_capability])
    assert c.extra == {"recurrence": "daily"}


# --- _resolve_local_now unit coverage (2026-09-08 timezone fix) ---
from capability_invocation.extraction import _resolve_local_now  # noqa: E402


def test_resolve_local_now_converts_to_named_zone():
    utc_now = datetime(2026, 9, 10, 6, 15, 0, tzinfo=timezone.utc)
    local, effective = _resolve_local_now(utc_now, "Asia/Kathmandu")
    assert effective == "Asia/Kathmandu"
    assert local.isoformat() == "2026-09-10T12:00:00+05:45"


def test_resolve_local_now_falls_back_to_utc_on_bad_name():
    utc_now = datetime(2026, 9, 10, 6, 15, 0, tzinfo=timezone.utc)
    local, effective = _resolve_local_now(utc_now, "Definitely/Not_A_Zone")
    assert effective == "UTC"
    assert local == utc_now
