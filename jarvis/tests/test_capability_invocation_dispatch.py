"""Integration tests for capability_invocation/dispatch.py — the real
goals.advance path end-to-end (GRANTED, per the milestone brief: this
is the only real capability this milestone touches), plus a synthetic
HIGH-risk test double (never a real capability) to prove the
CONFIRMATION_REQUIRED branch, since goals.advance itself never
exercises it (LOW baseline + ActionType.COMPUTE + no undo = MEDIUM,
always GRANTED — see permission/risk_calculator.py)."""
import pytest

import capabilities.bootstrap  # noqa: F401 — registers goals.advance
from action_engine.api import get_action
from capabilities.registry import register as register_capability
from capability_invocation.dispatch import resolve_and_dispatch, resolve_pending_capability_invocation
from contracts.capability import Capability
from contracts.capability_invocation_candidate import CapabilityInvocationCandidate
from contracts.enums import ActionStatus, CapabilityType, GoalStatus, GoalType, RiskLevel
from goals.api import create_goal, get_goal
from mission.api import set_mission

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seed_goal(title="Build Jarvis"):
    await set_mission(title="Grow", statement="Statement.")
    return await create_goal(type=GoalType.PROJECT, title=title)


async def test_goals_advance_executes_immediately_granted():
    goal = await _seed_goal()
    candidate = CapabilityInvocationCandidate(
        capability_id="goals.advance", raw_user_text="touch my build jarvis goal",
        goal_ref="Build Jarvis", resolved_goal_id=goal.id, match_count=1, note="reviewed today",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)

    assert relay is None
    assert pending == []
    assert executed is not None and "Advance Goal" in executed
    updated = await get_goal(goal.id)
    assert updated.version == goal.version + 1  # goals.advance's touch bumped the version


async def test_no_goal_match_asks_clarify_without_dispatching():
    candidate = CapabilityInvocationCandidate(
        capability_id="goals.advance", raw_user_text="touch my nonexistent goal",
        goal_ref="Nonexistent Goal", resolved_goal_id=None, match_count=0,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "couldn't find a goal" in relay


async def test_ambiguous_goal_match_asks_clarify_without_dispatching():
    candidate = CapabilityInvocationCandidate(
        capability_id="goals.advance", raw_user_text="touch my build jarvis goal",
        goal_ref="Build Jarvis", resolved_goal_id=None, match_count=2,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "more than one goal" in relay


async def test_none_candidate_is_a_no_op():
    executed, relay, pending = await resolve_and_dispatch(None)
    assert (executed, relay, pending) == (None, None, [])


# --- V1-M6: fs.read / fs.write ---

def _configure_fs(tmp_path):
    import capabilities.primitives.fs_ops as fs_ops
    fs_ops.configure_for_test([tmp_path], [".ssh", "*.pem", ".env"])
    from capability_invocation import dispatch as dispatch_module
    dispatch_module._current_directory = None  # reset session cwd between tests
    return fs_ops


async def test_fs_write_confirms_with_real_resolved_path_then_executes(tmp_path):
    _configure_fs(tmp_path)
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.write", raw_user_text="write hello to notes.txt",
        path_ref="notes.txt", content="hello",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)

    resolved = str((tmp_path / "notes.txt").resolve())
    assert executed is None
    assert len(pending) == 1
    assert resolved in pending[0].reason  # confirmation prompt shows the REAL resolved path
    assert "notes.txt" not in relay or resolved in relay  # never just the shorthand alone

    status = await resolve_pending_capability_invocation(pending[0], approved=True)
    assert status is not None and "Completed" in status
    assert (tmp_path / "notes.txt").read_text() == "hello"


async def test_fs_write_declined_does_not_touch_disk(tmp_path):
    _configure_fs(tmp_path)
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.write", raw_user_text="write hello to notes.txt",
        path_ref="notes.txt", content="hello",
    )
    _executed, _relay, pending = await resolve_and_dispatch(candidate)
    status = await resolve_pending_capability_invocation(pending[0], approved=False)
    assert status is None
    assert not (tmp_path / "notes.txt").exists()


async def test_fs_read_never_confirms(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "README.md").write_text("hi")
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read README.md", path_ref="README.md",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert pending == []
    assert relay is None
    assert executed is not None and str((tmp_path / "README.md").resolve()) in executed


async def test_path_outside_allowed_root_is_refused_before_any_action(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read /etc/passwd", path_ref="/etc/passwd",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "outside the folders" in relay


async def test_blocklisted_path_is_refused_before_any_action(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read my ssh key", path_ref=".ssh/id_rsa",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "sensitive" in relay


async def test_fs_write_without_content_asks_clarify():
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.write", raw_user_text="write a file", path_ref="notes.txt", content=None,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "what should I write" in relay


async def test_fs_capability_without_path_ref_asks_clarify():
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read the file", path_ref=None,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "which file" in relay


async def test_current_directory_updates_after_successful_navigation(tmp_path):
    from capability_invocation.dispatch import get_current_directory
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "jarvis").mkdir()
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="list jarvis", path_ref="jarvis",
    )
    await resolve_and_dispatch(candidate)
    assert get_current_directory() == str((tmp_path / "jarvis").resolve())


async def test_current_directory_defaults_to_first_allowed_root(tmp_path):
    from capability_invocation.dispatch import get_current_directory
    fs_ops = _configure_fs(tmp_path)
    assert get_current_directory() == str(tmp_path.resolve())


async def test_current_directory_prefers_real_process_cwd_when_under_an_allowed_root(tmp_path, monkeypatch):
    # Regression test for the 2026-08-31 dogfooding bug: defaulting to
    # the FIRST allowed root (config order) instead of where the
    # process is actually running resolved "read cli.py in the current
    # repo" against the wrong directory and produced a spurious File
    # Not Found. The real os.getcwd(), when it's a descendant of an
    # allowed root, must win over config root order.
    from capability_invocation.dispatch import get_current_directory
    fs_ops = _configure_fs(tmp_path)
    nested = tmp_path / "Jarvis" / "jarvis-M6"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert get_current_directory() == str(nested.resolve())


async def test_current_directory_falls_back_to_first_root_when_cwd_is_outside_every_root(tmp_path, monkeypatch):
    from capability_invocation.dispatch import get_current_directory
    fs_ops = _configure_fs(tmp_path)
    outside = tmp_path.parent / "somewhere_else"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert get_current_directory() == str(tmp_path.resolve())


async def test_fs_read_directory_listing_is_in_the_executed_message(tmp_path):
    # Regression test for the 2026-08-31 dogfooding bug: the "completed"
    # branch used to say only "Ran fs.read on X." and discard the actual
    # directory listing, so the reply model had nothing to relay and
    # fabricated a fake slash command instead of just answering.
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "README.md").write_text("hi")
    (tmp_path / "notes.txt").write_text("hi")
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="what's in this directory", path_ref=".",
    )
    executed, _relay, _pending = await resolve_and_dispatch(candidate)
    assert executed is not None
    assert "README.md" in executed
    assert "notes.txt" in executed


async def test_fs_read_file_content_is_in_the_executed_message(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "README.md").write_text("hello from the readme")
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read the readme", path_ref="README.md",
    )
    executed, _relay, _pending = await resolve_and_dispatch(candidate)
    assert "hello from the readme" in executed


async def test_fs_read_large_file_content_is_truncated_in_the_executed_message(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 5000)
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="read big.txt", path_ref="big.txt",
    )
    executed, _relay, _pending = await resolve_and_dispatch(candidate)
    assert "5000 chars total" in executed
    assert "describe/summarize, don't quote it all" in executed
    assert len(executed) < 1000  # must actually fit a 200-token reply budget, not just be "shorter than before"


async def test_fs_read_empty_directory_says_empty(tmp_path):
    fs_ops = _configure_fs(tmp_path)
    (tmp_path / "empty_dir").mkdir()
    candidate = CapabilityInvocationCandidate(
        capability_id="fs.read", raw_user_text="what's in empty_dir", path_ref="empty_dir",
    )
    executed, _relay, _pending = await resolve_and_dispatch(candidate)
    assert "(empty)" in executed


# --- synthetic HIGH-risk test double (not a real capability) ---
#
# Per the milestone brief: goals.advance never exercises
# CONFIRMATION_REQUIRED for real, so the confirm/reject branches need a
# throwaway registered capability that computes to HIGH risk.
# LOW baseline + ActionType.WRITE (impact +1) + supports_undo=False
# (reversibility +1) = ordinal 2 = HIGH (permission/risk_calculator.py).
_FAKE_CAPABILITY_ID = "test.fake_high_risk"


async def _fake_executor(parameters: dict) -> dict:
    return {"ok": True}


def _register_fake_high_risk_capability():
    register_capability(
        Capability(
            id=_FAKE_CAPABILITY_ID, name="Fake High Risk Op", description="Test double only — never a real capability.",
            category="Test", capability_type=CapabilityType.PRIMITIVE,
            baseline_risk_level=RiskLevel.LOW, required_permissions=[], supports_undo=False,
        ),
        _fake_executor,
    )


@pytest.fixture
async def fake_high_risk_capability():
    """Registers, then deregisters, a throwaway test-double capability.
    capabilities/registry.py's _REGISTRY is a module-level global with
    no `clean_db`-style reset — left registered, it would leak into
    other tests in the same process (e.g. test_m5_capabilities.py's
    exact-set assertion on list_capabilities()). Reach into the
    "private" registry dict directly for cleanup rather than adding a
    production unregister() function this milestone doesn't otherwise
    need."""
    import capabilities.registry as registry_module
    from capability_invocation import dispatch as dispatch_module

    _register_fake_high_risk_capability()
    dispatch_module._ACTION_TYPE_BY_CAPABILITY[_FAKE_CAPABILITY_ID] = dispatch_module.ActionType.WRITE
    yield
    registry_module._REGISTRY.pop(_FAKE_CAPABILITY_ID, None)
    dispatch_module._ACTION_TYPE_BY_CAPABILITY.pop(_FAKE_CAPABILITY_ID, None)


async def test_high_risk_capability_awaits_confirmation_then_resolves_approved(fake_high_risk_capability):
    candidate = CapabilityInvocationCandidate(
        capability_id=_FAKE_CAPABILITY_ID, raw_user_text="do the fake risky thing",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)

    assert executed is None
    assert len(pending) == 1
    assert "confirm" in relay

    status = await resolve_pending_capability_invocation(pending[0], approved=True)
    assert status is not None and "Completed" in status


async def test_high_risk_capability_confirmation_declined_does_not_run(fake_high_risk_capability):
    candidate = CapabilityInvocationCandidate(
        capability_id=_FAKE_CAPABILITY_ID, raw_user_text="do the fake risky thing",
    )
    _executed, _relay, pending = await resolve_and_dispatch(candidate)

    status = await resolve_pending_capability_invocation(pending[0], approved=False)
    assert status is None


# --- M5: calendar.read_events / calendar.create_event clarify gates ---

async def test_calendar_missing_start_end_asks_clarify_without_dispatching():
    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.read_events", raw_user_text="what's on my calendar",
        start=None, end=None,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "date/time range" in relay


async def test_calendar_create_event_missing_title_asks_clarify():
    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.create_event", raw_user_text="add something tomorrow at noon",
        title=None, start="2026-09-01T12:00:00+00:00", end="2026-09-01T13:00:00+00:00",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "should the event be called" in relay


async def test_calendar_unresolved_account_ref_asks_clarify():
    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.read_events", raw_user_text="what's on my side-hustle calendar",
        start="2026-09-01T00:00:00+00:00", end="2026-09-08T00:00:00+00:00",
        account_ref="side-hustle", resolved_account=None, account_match_count=0,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert "don't have a calendar account" in relay


async def test_calendar_omitted_account_is_not_a_clarify_case():
    # account_ref=None (never specified) must NOT trigger the same gate
    # as an unresolved-but-given one — it should sail through to
    # dispatch and hit the real CONFIRMATION_REQUIRED path instead.
    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.create_event", raw_user_text="add standup tomorrow at 9",
        title="Standup", start="2026-09-01T09:00:00+00:00", end="2026-09-01T09:15:00+00:00",
        account_ref=None,
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert len(pending) == 1
    assert "confirm" in relay


async def test_calendar_create_event_dispatches_and_reaches_confirmation(monkeypatch):
    from unittest.mock import AsyncMock
    import capabilities.primitives.calendar_ops as calendar_ops_module

    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.create_event", raw_user_text="add standup tomorrow at 9",
        title="Standup", start="2026-09-01T09:00:00+00:00", end="2026-09-01T09:15:00+00:00",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    # MEDIUM baseline + WRITE + no undo = HIGH -> confirmation, never
    # reaches call_calendar_tool at this stage — nothing to mock yet;
    # the actual MCP call only happens once resolve_pending_... runs it.
    assert executed is None
    assert len(pending) == 1

    fake_result = {"id": "evt-xyz"}
    monkeypatch.setattr(calendar_ops_module, "call_calendar_tool", AsyncMock(return_value=fake_result))
    status = await resolve_pending_capability_invocation(pending[0], approved=True)
    assert status is not None and "Completed" in status


# --- 2026-09-06 fix: completed calendar reads/writes must relay real data ---

def test_completed_detail_summarizes_real_events():
    from capability_invocation.dispatch import _completed_detail
    output = {"events": [{"summary": "studying session", "start": {"dateTime": "2026-09-06T17:45:00+05:45"}}]}
    detail = _completed_detail("calendar.read_events", output)
    assert "studying session" in detail
    assert "2026-09-06T17:45:00+05:45" in detail


def test_completed_detail_honest_about_empty_results():
    from capability_invocation.dispatch import _completed_detail
    assert "No events found" in _completed_detail("calendar.read_events", {"events": []})
    assert "No events found" in _completed_detail("calendar.read_events", None)


def test_completed_detail_includes_create_event_link():
    from capability_invocation.dispatch import _completed_detail
    detail = _completed_detail("calendar.create_event", {"event_id": "abc123", "html_link": "https://cal.example/abc"})
    assert "https://cal.example/abc" in detail


def test_completed_detail_empty_for_unrelated_capabilities():
    from capability_invocation.dispatch import _completed_detail
    assert _completed_detail("goals.advance", {"version": 2}) == ""


async def test_calendar_read_events_relays_real_event_data_end_to_end(monkeypatch):
    from unittest.mock import AsyncMock
    import capabilities.primitives.calendar_ops as calendar_ops_module

    candidate = CapabilityInvocationCandidate(
        capability_id="calendar.read_events", raw_user_text="what's on my calendar today",
        start="2026-09-06T00:00:00+05:45", end="2026-09-07T00:00:00+05:45",
    )
    real_response = {"events": [{"summary": "studying session", "start": {"dateTime": "2026-09-06T17:45:00+05:45"}}]}
    monkeypatch.setattr(calendar_ops_module, "call_calendar_tool", AsyncMock(return_value=real_response))

    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert pending == []
    assert relay is None
    assert "studying session" in executed
    assert "17:45:00+05:45" in executed


# --- generic-parameter mechanism (2026-09-07) ---
#
# A throwaway capability declaring a REQUIRED extra_parameters field,
# same registered-then-deregistered pattern as fake_high_risk_capability
# above — proves dispatch.py's generic clarify-gate (one loop over
# capability.extra_parameters) without depending on a real capability's
# specific fields.
_FAKE_EXTRA_PARAM_CAPABILITY_ID = "test.fake_extra_param"


def _register_fake_extra_param_capability():
    from contracts.capability import ParamSpec
    from contracts.enums import ParamType

    register_capability(
        Capability(
            id=_FAKE_EXTRA_PARAM_CAPABILITY_ID, name="Fake Extra Param Op",
            description="Test double only — never a real capability.",
            category="Test", capability_type=CapabilityType.PRIMITIVE,
            baseline_risk_level=RiskLevel.LOW, required_permissions=[], supports_undo=True,
            extra_parameters={
                "flavor": ParamSpec(
                    type=ParamType.ENUM, required=True, choices=["vanilla", "chocolate"],
                    description="what flavor should it be?",
                ),
            },
        ),
        _fake_executor,
    )


@pytest.fixture
async def fake_extra_param_capability():
    import capabilities.registry as registry_module

    _register_fake_extra_param_capability()
    yield
    registry_module._REGISTRY.pop(_FAKE_EXTRA_PARAM_CAPABILITY_ID, None)


async def test_missing_required_extra_param_asks_clarify_without_dispatching(fake_extra_param_capability):
    candidate = CapabilityInvocationCandidate(
        capability_id=_FAKE_EXTRA_PARAM_CAPABILITY_ID, raw_user_text="do the fake thing",
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert executed is None
    assert pending == []
    assert relay == "what flavor should it be?"


async def test_present_required_extra_param_dispatches_and_merges_into_parameters(fake_extra_param_capability):
    candidate = CapabilityInvocationCandidate(
        capability_id=_FAKE_EXTRA_PARAM_CAPABILITY_ID, raw_user_text="do the fake thing, chocolate",
        extra={"flavor": "chocolate"},
    )
    executed, relay, pending = await resolve_and_dispatch(candidate)
    assert relay is None
    assert pending == []
    assert executed is not None
