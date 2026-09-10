"""Integration tests for memory/api.py against a real Postgres instance."""
import pytest

from contracts.enums import Importance, MemoryStatus, MemoryType
from contracts.memory_candidate import MemoryCandidate, MemoryScope
from memory.api import (
    InvalidMemoryTransitionError,
    MemoryNotFoundError,
    archive_memory,
    create_memory,
    forget_memory,
    get_memory_history,
    list_memories,
    list_taxonomy_gaps,
    record_taxonomy_gap,
    remember_fact,
    remember_from_candidate,
    set_status,
    update_memory,
)

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_create_memory_basic():
    m = await create_memory(
        type=MemoryType.PREFERENCE, title="Preferred IDE", value="VS Code",
        source_ids=["conversation-1"],
    )
    assert m.status == MemoryStatus.ACTIVE
    assert m.confidence == 1.0
    assert m.version == 1


async def test_update_bumps_version_and_records_history():
    m = await create_memory(
        type=MemoryType.FACT, title="Original", value="v1", source_ids=["src-1"],
    )
    m = await update_memory(m.id, reason="corrected", value="v2")
    assert m.version == 2
    assert m.value == "v2"

    history = await get_memory_history(m.id)
    assert len(history) == 1
    assert history[0]["snapshot"]["value"] == "v1"
    assert history[0]["reason"] == "corrected"


async def test_archive_then_reactivate():
    m = await create_memory(type=MemoryType.FACT, title="T", value="V", source_ids=["s"])
    m = await archive_memory(m.id, reason="no longer relevant")
    assert m.status == MemoryStatus.ARCHIVED

    m = await set_status(m.id, MemoryStatus.ACTIVE, reason="relevant again")
    assert m.status == MemoryStatus.ACTIVE


async def test_forgotten_is_terminal():
    m = await create_memory(type=MemoryType.FACT, title="T", value="V", source_ids=["s"])
    m = await forget_memory(m.id, reason="user said forget this")
    assert m.status == MemoryStatus.FORGOTTEN

    with pytest.raises(InvalidMemoryTransitionError):
        await set_status(m.id, MemoryStatus.ACTIVE, reason="should not be allowed")


async def test_operations_on_nonexistent_memory_raise():
    with pytest.raises(MemoryNotFoundError):
        await archive_memory("00000000-0000-0000-0000-000000000000", reason="x")


async def test_remember_fact_creates_new_when_no_existing_match():
    m = await remember_fact(
        type=MemoryType.FACT, title="Long-term goal: Build Jarvis",
        value="Build Jarvis", source_ids=["goal-1"],
    )
    assert m.version == 1


async def test_remember_fact_merges_into_existing_active_memory():
    first = await remember_fact(
        type=MemoryType.FACT, title="Long-term goal: Build Jarvis",
        value="Build Jarvis", source_ids=["goal-1"],
    )
    second = await remember_fact(
        type=MemoryType.FACT, title="Long-term goal: Build Jarvis v2",
        value="Build Jarvis, revised scope", source_ids=["goal-1"],
    )
    # same underlying id, merged not duplicated (§6.3: "Duplicate memories
    # should merge rather than multiply")
    assert second.id == first.id
    assert second.version == 2
    assert second.title == "Long-term goal: Build Jarvis v2"

    all_active = [m for m in [second]]  # sanity: only one row should exist
    from memory.api import list_memories
    active_memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(active_memories) == 1


async def test_remember_fact_is_a_no_op_when_nothing_changed():
    first = await remember_fact(
        type=MemoryType.FACT, title="T", value="V", source_ids=["goal-1"],
    )
    second = await remember_fact(
        type=MemoryType.FACT, title="T", value="V", source_ids=["goal-1"],
    )
    assert second.version == first.version == 1  # no spurious version bump


# --- V1-M3: remember_from_candidate — the User-Controlled path ---

def _candidate(**overrides) -> MemoryCandidate:
    defaults = dict(
        type=MemoryType.PREFERENCE, title="user's preferred code editor", value="VS Code",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="I prefer VS Code",
    )
    defaults.update(overrides)
    return MemoryCandidate(**defaults)


async def test_remember_from_candidate_creates_new_memory():
    m = await remember_from_candidate(_candidate(), source_id="turn-1")
    assert m.status == MemoryStatus.ACTIVE
    assert m.version == 1
    assert m.source_ids == ["turn-1"]


async def test_remember_from_candidate_updates_same_titled_memory():
    first = await remember_from_candidate(_candidate(), source_id="turn-1")
    second = await remember_from_candidate(
        _candidate(value="Neovim"), source_id="turn-2"
    )
    # Same (type, normalized title) -> update in place, not a duplicate
    # (V1-M3 §10: title-keyed dedup, no semantic resolver).
    assert second.id == first.id
    assert second.version == 2
    assert second.value == "Neovim"
    assert set(second.source_ids) == {"turn-1", "turn-2"}

    active = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(active) == 1


async def test_remember_from_candidate_title_match_is_case_insensitive():
    first = await remember_from_candidate(_candidate(title="User's Preferred Code Editor"), source_id="turn-1")
    second = await remember_from_candidate(
        _candidate(title="user's preferred code editor", value="Neovim"), source_id="turn-2"
    )
    assert second.id == first.id
    assert second.version == 2


async def test_remember_from_candidate_identical_restatement_is_a_no_op():
    first = await remember_from_candidate(_candidate(), source_id="turn-1")
    second = await remember_from_candidate(_candidate(), source_id="turn-2")
    assert second.id == first.id
    assert second.version == first.version == 1  # no spurious bump, no new source appended


async def test_remember_from_candidate_different_title_creates_separate_memory():
    first = await remember_from_candidate(_candidate(), source_id="turn-1")
    second = await remember_from_candidate(
        _candidate(title="user's preferred terminal", value="iTerm2"), source_id="turn-2"
    )
    assert second.id != first.id
    active = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(active) == 2


async def test_remember_from_candidate_rejects_taxonomy_gap_candidate():
    with pytest.raises(ValueError):
        await remember_from_candidate(_candidate(type=None), source_id="turn-1")


# --- V1-M3 follow-up (2026-08-15b): taxonomy gap persistence ---
# Architect review: a gap must leave a durable trail, not just a log
# line. See infra/migrations/0017_taxonomy_gap_proposal.sql.

async def test_record_taxonomy_gap_persists_it():
    gap_candidate = _candidate(type=None, title="user's blood type", value="O negative")
    proposal = await record_taxonomy_gap(gap_candidate)
    assert proposal.title == "user's blood type"
    assert proposal.value == "O negative"

    gaps = await list_taxonomy_gaps()
    assert len(gaps) == 1
    assert gaps[0].id == proposal.id


async def test_list_taxonomy_gaps_most_recent_first():
    await record_taxonomy_gap(_candidate(type=None, title="first", value="a"))
    await record_taxonomy_gap(_candidate(type=None, title="second", value="b"))

    gaps = await list_taxonomy_gaps()
    assert len(gaps) == 2
    assert gaps[0].title == "second"  # most recent first
    assert gaps[1].title == "first"


async def test_taxonomy_gaps_are_independent_of_memory_table():
    # Recording a gap must never create a row in `memory` — it's a
    # separate, explicitly not-yet-a-Memory record.
    await record_taxonomy_gap(_candidate(type=None, title="x", value="y"))
    assert await list_memories(status=MemoryStatus.ACTIVE) == []
