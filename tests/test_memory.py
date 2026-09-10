"""Tests for §6.3 Memory."""
import pytest
from pydantic import ValidationError

from contracts.memory import Memory
from contracts.enums import MemoryType, Importance, MemoryStatus


def _memory(**overrides):
    defaults = dict(
        type=MemoryType.PREFERENCE,
        title="Preferred IDE",
        value="VS Code",
        confidence=0.98,
        source_ids=["conversation-124"],
    )
    defaults.update(overrides)
    return Memory(**defaults)


def test_requires_at_least_one_source_id():
    with pytest.raises(ValidationError):
        _memory(source_ids=[])


def test_confidence_must_be_in_unit_interval():
    with pytest.raises(ValidationError):
        _memory(confidence=1.5)
    with pytest.raises(ValidationError):
        _memory(confidence=-0.1)


def test_defaults():
    m = _memory()
    assert m.status == MemoryStatus.ACTIVE
    assert m.importance == Importance.MEDIUM
    assert m.version == 1
