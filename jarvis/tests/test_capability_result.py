"""Tests for §6.10 CapabilityResult."""
from contracts.capability_result import CapabilityResult


def test_minimal_success_result():
    r = CapabilityResult(action_id="action-123", success=True, output={"event_id": "78291"})
    assert r.success is True
    assert r.error is None
    assert r.tokens_used == 0


def test_failure_result_can_carry_error():
    r = CapabilityResult(action_id="action-123", success=False, error="Calendar API timeout")
    assert r.success is False
    assert r.error == "Calendar API timeout"
