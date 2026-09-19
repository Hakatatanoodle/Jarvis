"""Integration tests for mission/api.py against a real Postgres instance."""
import pytest

from contracts.enums import MissionStatus
from mission.api import get_active_mission, list_mission_versions, set_mission

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_set_mission_creates_first_active_version():
    m = await set_mission(title="Become the best version of myself", statement="A real statement.")
    assert m.version == 1
    assert m.status == MissionStatus.ACTIVE
    assert m.previous_version_id is None


async def test_get_active_mission_none_when_unset():
    assert await get_active_mission() is None


async def test_set_mission_supersedes_previous_version():
    v1 = await set_mission(title="Original", statement="Original statement.")
    v2 = await set_mission(title="Updated", statement="Updated statement.")

    assert v2.version == 2
    assert v2.previous_version_id == v1.id
    assert v2.status == MissionStatus.ACTIVE

    active = await get_active_mission()
    assert active.id == v2.id

    versions = await list_mission_versions()
    assert len(versions) == 2
    archived = [v for v in versions if v.id == v1.id][0]
    assert archived.status == MissionStatus.ARCHIVED


async def test_only_one_active_mission_enforced_by_repository_layer(db_pool):
    # Business rules live in the repository layer now (mission/api.py's
    # advisory lock), not in a SQL constraint — see infra/migrations/
    # 0004_move_business_rules_to_repository_layer.sql. This test proves
    # the guarantee still holds under real concurrent writers, which is
    # the case that actually mattered (a single-writer test wouldn't
    # have caught a race condition either way).
    import asyncio

    await set_mission(title="First", statement="First statement.")

    results = await asyncio.gather(
        *[
            set_mission(title=f"Concurrent {i}", statement=f"Concurrent statement {i}.")
            for i in range(5)
        ]
    )
    versions = sorted(m.version for m in results)
    assert versions == [2, 3, 4, 5, 6]  # advisory lock serialized them, no duplicate versions

    async with db_pool.acquire() as conn:
        active_count = await conn.fetchval("SELECT count(*) FROM mission WHERE status = 'active';")
    assert active_count == 1
