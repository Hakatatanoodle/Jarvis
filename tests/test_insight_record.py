"""Tests for §6.12 InsightRecord — evidence-first, weekly-only in V0."""
import pytest
from pydantic import ValidationError

from contracts.insight_record import InsightRecord, EvidenceClaim
from contracts.enums import InsightMode, InsightScope


def _record(**overrides):
    defaults = dict(
        mode=InsightMode.REVIEW,
        narrative="This month you made strong progress on programming.",
        evidence=[EvidenceClaim(claim="17 coding sessions in 21 days.", supporting_ids=["log-1"])],
    )
    defaults.update(overrides)
    return InsightRecord(**defaults)


def test_evidence_claim_requires_supporting_ids():
    with pytest.raises(ValidationError):
        EvidenceClaim(claim="You seem more productive.", supporting_ids=[])


def test_v0_rejects_non_weekly_scope():
    with pytest.raises(ValidationError):
        _record(scope=InsightScope.MONTHLY)


def test_weekly_scope_accepted():
    r = _record(scope=InsightScope.WEEKLY)
    assert r.scope == InsightScope.WEEKLY


def test_user_response_defaults_none_and_is_the_mutable_field():
    r = _record()
    assert r.user_response is None
    r.user_response = "Agreed, adjusting next week."
    assert r.user_response == "Agreed, adjusting next week."
