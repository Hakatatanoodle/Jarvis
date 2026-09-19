"""Tests for §6.8 Capability — decision #2 (renamed risk field) and
decision #5 (composite must compile to a Plan, never call capabilities
directly — the "no second orchestrator" rule)."""
import pytest
from pydantic import ValidationError

from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel


def _primitive(**overrides):
    defaults = dict(
        id="calendar.create_event",
        name="Create Calendar Event",
        description="Creates a new event in the user's calendar.",
        category="Calendar",
        capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.MEDIUM,
    )
    defaults.update(overrides)
    return Capability(**defaults)


def test_field_is_named_baseline_risk_level_not_risk_level():
    c = _primitive()
    assert hasattr(c, "baseline_risk_level")
    assert not hasattr(c, "risk_level")


def test_composite_without_plan_template_rejected():
    with pytest.raises(ValidationError):
        _primitive(
            id="weekly_review",
            capability_type=CapabilityType.COMPOSITE,
            compiles_to_plan_template=None,
        )


def test_composite_with_plan_template_accepted():
    c = _primitive(
        id="weekly_review",
        capability_type=CapabilityType.COMPOSITE,
        compiles_to_plan_template="weekly_review_template",
    )
    assert c.compiles_to_plan_template == "weekly_review_template"


def test_primitive_cannot_carry_a_plan_template():
    with pytest.raises(ValidationError):
        _primitive(compiles_to_plan_template="should_not_be_set")
