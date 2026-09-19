"""
Minimal migration runner (§7: infra/storage.py "Postgres + pgvector
connection, migrations" — split into its own module here for clarity,
storage.py still owns the connection itself).

Applies infra/migrations/*.sql in filename order, tracking what's already
applied in a schema_migrations table. Deliberately simple (§3: "simplicity
over cleverness") — no rollback/down-migrations, no ORM. A solo V0 build
doesn't need more than this yet.
"""
from __future__ import annotations

from pathlib import Path

import asyncpg

from infra.logging import get_logger

log = get_logger("infra.migrate")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        applied_rows = await conn.fetch("SELECT filename FROM schema_migrations;")
        already_applied = {r["filename"] for r in applied_rows}

        newly_applied: list[str] = []
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already_applied:
                continue
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1);", path.name
                )
            newly_applied.append(path.name)
            log.info(f"Applied migration: {path.name}")

        if not newly_applied:
            log.info("No pending migrations.")
        return newly_applied
