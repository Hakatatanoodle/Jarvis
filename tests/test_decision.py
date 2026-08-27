"""Tests for §6.5 Decision — immutable, renamed field, requires evidence."""
import pytest
from pydantic import ValidationError

from contracts.decision import Decision


def _decision(**overrides):
    defaults = dict(
        objective="Plan next week",
        summary="Schedule deep work in the mornings.",
        reasoning="Matches user preferences and active goals.",
        confidence=0.94,
        selected_option="Morning work",
        expected_outcome="Higher productivity.",
        mission_alignment=0.96,
        context_item_ids=["ctx-1", "ctx-2"],
    )
    defaults.update(overrides)
    return Decision(**defaults)


def test_is_frozen_immutable():
    d = _decision()
    with pytest.raises(ValidationError):
        d.summary = "changed"  # type: ignore[misc]


def test_zero_context_items_is_legitimate():
    # Relaxed 2026-08-02 (ARCHITECTURE_ISSUES.md M3 entry): "nothing found"
    # is itself a real, explainable Decision.
    d = _decision(context_item_ids=[])
    assert d.context_item_ids == []


def test_reasoning_cannot_be_empty_even_with_zero_context_items():
    # Explainability (DE-08) is now enforced via `reasoning`, not via a
    # minimum ContextItem count.
    with pytest.raises(ValidationError):
        _decision(context_item_ids=[], reasoning="   ")


def test_renamed_field_is_estimated_confirmation_needed_not_requires_confirmation():
    # decision #7, §4: requires_confirmation -> estimated_confirmation_needed.
    d = _decision()
    assert hasattr(d, "estimated_confirmation_needed")
    assert not hasattr(d, "requires_confirmation")


def test_confidence_and_mission_alignment_bounded():
    with pytest.raises(ValidationError):
        _decision(confidence=1.2)
    with pytest.raises(ValidationError):
        _decision(mission_alignment=-0.1)
