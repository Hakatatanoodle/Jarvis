"""Tests for §6.9 PermissionCheckResult — the load-bearing authoritative
authorization record. See ARCHITECTURE_ISSUES.md (2026-08-01) for what's
deferred to M4's risk calculator vs. what's enforced here."""
import pytest
from pydantic import ValidationError

from contracts.permission_check_result import PermissionCheckResult, RiskFactors
from contracts.enums import RiskLevel, PermissionStatus, ConfirmationStatus


def _result(**overrides):
    defaults = dict(
        capability_id="calendar.create_event",
        baseline_risk=RiskLevel.MEDIUM,
        computed_risk=RiskLevel.LOW,
        permission_status=PermissionStatus.GRANTED,
        permission_rule_used="auto_approve_low_risk",
    )
    defaults.update(overrides)
    return PermissionCheckResult(**defaults)


def test_confirmation_required_cannot_pair_with_not_applicable():
    with pytest.raises(ValidationError):
        _result(
            permission_status=PermissionStatus.CONFIRMATION_REQUIRED,
            confirmation_status=ConfirmationStatus.NOT_APPLICABLE,
        )


def test_confirmation_required_pairs_with_pending():
    r = _result(
        permission_status=PermissionStatus.CONFIRMATION_REQUIRED,
        confirmation_status=ConfirmationStatus.PENDING,
    )
    assert r.confirmation_status == ConfirmationStatus.PENDING


def test_granted_defaults_confirmation_status_not_applicable():
    r = _result()
    assert r.confirmation_status == ConfirmationStatus.NOT_APPLICABLE


def test_risk_factors_default_to_empty_but_present():
    r = _result()
    assert isinstance(r.risk_factors, RiskFactors)
    assert r.risk_factors.impact == ""


def test_computed_risk_and_baseline_risk_are_valid_enum_members():
    r = _result()
    assert r.baseline_risk in RiskLevel
    assert r.computed_risk in RiskLevel
    with pytest.raises(ValidationError):
        _result(computed_risk="not_a_real_level")
