"""Tests for §6.7 Action — including the permission_check_id lifecycle
resolution logged in ARCHITECTURE_ISSUES.md (2026-08-01)."""
import pytest
from pydantic import ValidationError

from contracts.action import Action
from contracts.enums import ActionStatus, ActionType


def _action(**overrides):
    defaults = dict(
        plan_id="plan-456",
        capability_id="calendar.create_event",
        type=ActionType.WRITE,
        title="Create Calendar Event",
        parameters={"title": "Deep Work"},
    )
    defaults.update(overrides)
    return Action(**defaults)


def test_pending_action_may_have_no_permission_check_id():
    a = _action(status=ActionStatus.PENDING)
    assert a.permission_check_id is None


def test_ready_action_requires_permission_check_id():
    with pytest.raises(ValidationError):
        _action(status=ActionStatus.READY, permission_check_id=None)


def test_running_action_requires_permission_check_id():
    with pytest.raises(ValidationError):
        _action(status=ActionStatus.RUNNING, permission_check_id=None)


def test_ready_action_with_permission_check_id_is_valid():
    a = _action(status=ActionStatus.READY, permission_check_id="permcheck-uuid")
    assert a.permission_check_id == "permcheck-uuid"


def test_one_capability_per_action_by_shape():
    a = _action()
    assert isinstance(a.capability_id, str)


def test_retry_count_defaults_to_zero_and_nonnegative():
    a = _action()
    assert a.retry_count == 0
    with pytest.raises(ValidationError):
        _action(retry_count=-1)
