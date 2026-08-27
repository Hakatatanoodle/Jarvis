"""Tests for §6.11 LearningProposal — reconstructed shape, see
ARCHITECTURE_ISSUES.md (2026-08-01)."""
import pytest
from pydantic import ValidationError

from contracts.learning_proposal import LearningProposal
from contracts.enums import LearningCategory, LearningStatus


def _proposal(**overrides):
    defaults = dict(
        category=LearningCategory.USER,
        title="User prefers morning coding",
        evidence_ids=["session-1", "session-2"],
        confidence=0.8,
    )
    defaults.update(overrides)
    return LearningProposal(**defaults)


def test_minimum_two_evidence_ids_required():
    with pytest.raises(ValidationError):
        _proposal(evidence_ids=["only-one"])


def test_two_evidence_ids_is_the_floor():
    p = _proposal(evidence_ids=["a", "b"])
    assert len(p.evidence_ids) == 2


def test_defaults_to_proposed_status_no_resulting_memory():
    p = _proposal()
    assert p.status == LearningStatus.PROPOSED
    assert p.resulting_memory_id is None
