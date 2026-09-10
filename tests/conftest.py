"""Ensures the repo root is importable, and provides DB fixtures for the
integration-style tests introduced in M1 (Mission/Goal API tests hit a
real Postgres instance rather than mocking it out — consistent with how
M0's storage sanity check was verified against a live DB, not a stub)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest_asyncio

from config.loader import load_config
from infra.storage import init_pool, close_pool
from infra.migrate import apply_migrations


@pytest_asyncio.fixture
async def db_pool():
    """Function-scoped deliberately: infra.storage keeps a module-level
    pool singleton, and pytest-asyncio gives each test its own event
    loop by default. A session-scoped pool would be bound to whichever
    loop created it and break on every other test. Reopening per test is
    cheap at V0's test-suite size and avoids fighting that mismatch."""
    cfg = load_config()
    # Local dev password only — never committed to config/default.yaml,
    # per INF-13 (no hardcoded secrets in versioned config).
    cfg._data.setdefault("storage", {}).setdefault("postgres", {})["password"] = "jarvis_dev_local"
    pool = await init_pool(cfg)
    await apply_migrations(pool)
    yield pool
    await close_pool()  # resets the storage.py singleton for the next test


@pytest_asyncio.fixture
async def clean_db(db_pool):
    """Every DB-backed test starts from an empty mission/goal/goal_history
    state, so tests can't leak into each other regardless of order."""
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE conversation_turn, taxonomy_gap_proposal, insight_record, capability_result, calendar_event, permission_check_result, action, plan, decision, memory_history, memory, goal_history, goal, mission CASCADE;")
    yield
