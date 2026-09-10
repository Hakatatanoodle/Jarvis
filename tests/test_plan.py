"""Tests for §6.6 Plan."""
import pytest
from pydantic import ValidationError

from contracts.plan import Plan
from contracts.enums import PlanStatus


def _plan(**overrides):
    defaults = dict(
        decision_id="decision-123",
        title="Optimize Weekly Schedule",
        objective="Create an effective weekly schedule.",
        action_ids=["action-1"],
    )
    defaults.update(overrides)
    return Plan(**defaults)


def test_requires_at_least_one_action():
    with pytest.raises(ValidationError):
        _plan(action_ids=[])


def test_references_exactly_one_decision_by_shape():
    # decision_id is a single scalar (not a list) — the schema itself
    # enforces "references exactly one Decision" (§6.6 invariant).
    p = _plan()
    assert isinstance(p.decision_id, str)


def test_defaults_to_draft_status():
    assert _plan().status == PlanStatus.DRAFT


def test_progress_bounded_zero_to_hundred():
    with pytest.raises(ValidationError):
        _plan(progress=101)
    with pytest.raises(ValidationError):
        _plan(progress=-1)
