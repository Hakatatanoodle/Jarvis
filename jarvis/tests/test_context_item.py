"""Tests for §6.4 ContextItem — ephemeral, immutable, never versioned."""
import pytest
from pydantic import ValidationError

from contracts.context_item import ContextItem
from contracts.enums import ContextSourceType, Priority


def _item(**overrides):
    defaults = dict(
        source_type=ContextSourceType.GOAL,
        source_id="goal-build-jarvis",
        relevance_score=0.98,
        confidence=1.0,
        freshness=0.95,
        reasoning="Active high-priority goal related to user request.",
    )
    defaults.update(overrides)
    return ContextItem(**defaults)


def test_is_frozen_immutable():
    item = _item()
    with pytest.raises(ValidationError):
        item.relevance_score = 0.1  # type: ignore[misc]


def test_score_fields_bounded_zero_to_one():
    with pytest.raises(ValidationError):
        _item(relevance_score=1.1)
    with pytest.raises(ValidationError):
        _item(confidence=-0.01)


def test_no_status_field_exists():
    # ContextItem has no lifecycle beyond "exists for one reasoning cycle" —
    # confirming the model doesn't carry a status/version field that would
    # imply persistence (§6.4: "never persisted, never versioned").
    item = _item()
    assert not hasattr(item, "status")
    assert not hasattr(item, "version")


def test_default_priority_is_medium():
    assert _item().priority == Priority.MEDIUM
