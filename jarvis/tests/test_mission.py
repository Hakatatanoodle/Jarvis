"""Tests for §6.1 Mission."""
import pytest
from pydantic import ValidationError

from contracts.mission import Mission
from contracts.enums import MissionStatus


def _mission(**overrides):
    defaults = dict(title="Become the best version of myself", statement="A real statement.")
    defaults.update(overrides)
    return Mission(**defaults)


def test_defaults_to_active_status():
    m = _mission()
    assert m.status == MissionStatus.ACTIVE


def test_empty_statement_rejected():
    with pytest.raises(ValidationError):
        _mission(statement="   ")


def test_empty_title_rejected():
    with pytest.raises(ValidationError):
        _mission(title="")


def test_version_defaults_to_one_and_previous_version_id_nullable():
    m = _mission()
    assert m.version == 1
    assert m.previous_version_id is None


def test_new_version_links_to_previous():
    v1 = _mission()
    v2 = _mission(version=2, previous_version_id=v1.id, status=MissionStatus.ACTIVE)
    assert v2.previous_version_id == v1.id
    assert v2.version == 2
