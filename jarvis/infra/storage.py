"""
Postgres + pgvector connection layer (§7, §9 M0, INF-01).

Structured data → PostgreSQL. pgvector is enabled from the start per §6's
stack assumption, even though semantic search itself stays unused until
§5.3 is unlocked — this module only exposes a connection pool; no
subsystem-specific tables live here (RFC-014: subsystems own their own
storage, this just gets everyone to it).

Async-first (§8): every query goes through asyncpg, nothing blocks the
event loop on I/O.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg

from config.loader import Config
from infra.logging import get_logger

log = get_logger("infra.storage")

_pool: Optional[asyncpg.Pool] = None


async def init_pool(config: Config) -> asyncpg.Pool:
    """Called once at startup (RFC-025: Load Configuration -> Connect
    Database -> ...). Subsequent calls to get_pool() reuse this."""
    global _pool
    if _pool is not None:
        return _pool

    host = config.get("storage.postgres.host", "localhost")
    port = config.get("storage.postgres.port", 5432)
    database = config.get("storage.postgres.database", "jarvis")
    user = config.get("storage.postgres.user", "jarvis")
    password = config.get("storage.postgres.password", "")

    log.info(f"Connecting to Postgres at {host}:{port}/{database} as {user}")
    _pool = await asyncpg.create_pool(
        host=host, port=port, database=database, user=user, password=password,
        min_size=1, max_size=10,
    )

    if config.get("storage.pgvector_enabled", True):
        async with _pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        log.info("pgvector extension confirmed enabled (unused until §5.3 is unlocked)")

    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "Storage pool not initialized — call infra.storage.init_pool(config) "
            "at startup before any subsystem touches the database."
        )
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """Every subsystem's storage layer uses this instead of touching the
    pool directly, so the connection lifecycle is consistent everywhere."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool() -> None:
    """Called on graceful shutdown (RFC-025: Finish Actions -> Flush Logs
    -> Save State -> Close Connections)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("Storage pool closed.")
