"""
Mission API (§7: public interface for the Constitution system; §9 M1).

Per RFC-014, every other package talks to Mission only through this
module — never by querying the `mission` table directly.

There is deliberately no separate create/update split: §6.1's own
lifecycle diagram is Created -> Active -> (Optional) Updated -> Previous
version archived -> New version active, all as one described flow. That
maps to exactly one operation here (`set_mission`), which either creates
the first version or supersedes the current one — matching "editable only
through an explicit rare user action" (§5.1): there's one door in, and
walking through it always means "the user just did the rare thing."
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from contracts.mission import Mission
from contracts.enums import MissionStatus
from infra.logging import get_logger
from infra.storage import connection

log = get_logger("mission.api")

# Fixed advisory-lock key for the "exactly one active Mission" invariant.
# Any value works as long as it's constant and unique to this invariant
# within the app; hashing a human-readable string keeps it self-documenting
# rather than a magic number nobody can trace back to its purpose.
_SINGLE_ACTIVE_MISSION_LOCK_KEY = "jarvis.mission.single_active_invariant"


def _row_to_mission(row) -> Mission:
    return Mission(
        id=str(row["id"]),
        identity_id=str(row["identity_id"]),
        version=row["version"],
        title=row["title"],
        statement=row["statement"],
        principles=json.loads(row["principles"]),
        status=MissionStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        previous_version_id=str(row["previous_version_id"]) if row["previous_version_id"] else None,
    )


async def get_active_mission() -> Optional[Mission]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM mission WHERE status = 'active';")
        return _row_to_mission(row) if row else None


async def get_mission_by_id(mission_id: str) -> Optional[Mission]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM mission WHERE id = $1;", mission_id)
        return _row_to_mission(row) if row else None


async def list_mission_versions() -> list[Mission]:
    """Old versions remain permanently accessible (§6.1: 'Archived: Yes,
    Old versions remain permanently accessible')."""
    async with connection() as conn:
        rows = await conn.fetch("SELECT * FROM mission ORDER BY version ASC;")
        return [_row_to_mission(r) for r in rows]


async def set_mission(title: str, statement: str, principles: Optional[list[str]] = None) -> Mission:
    """The one explicit, rare user action that creates or supersedes the
    Mission. Validates through the Mission contract before ever touching
    the database, so a bad statement/title never reaches Postgres."""
    now = datetime.now(timezone.utc)

    async with connection() as conn:
        async with conn.transaction():
            # Serializes every set_mission() call against every other one.
            # Held for the duration of this transaction only (xact-scoped),
            # released automatically on commit/rollback. This is what
            # actually enforces "exactly one active Mission" under
            # concurrent writers now that 0004 dropped the SQL-level
            # unique index — the invariant lives here, in the repository
            # layer, not in a database constraint.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1)::bigint);",
                _SINGLE_ACTIVE_MISSION_LOCK_KEY,
            )

            current = await conn.fetchrow("SELECT * FROM mission WHERE status = 'active';")

            if current is None:
                new_mission = Mission(
                    title=title,
                    statement=statement,
                    principles=principles or [],
                    version=1,
                    status=MissionStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                    previous_version_id=None,
                )
                # First version's own id doubles as the stable identity —
                # no separate lookup needed, and it's what the migration's
                # backfill already assumes for pre-existing lineages.
                new_mission.identity_id = new_mission.id
                await conn.execute(
                    "INSERT INTO mission_identity (id) VALUES ($1);", new_mission.identity_id
                )
                log.info(f"Creating first Mission: '{new_mission.title}'")
            else:
                new_mission = Mission(
                    title=title,
                    statement=statement,
                    principles=principles or [],
                    version=current["version"] + 1,
                    status=MissionStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                    previous_version_id=str(current["id"]),
                )
                # Carry the identity forward — this is the fix. Without
                # this line, Mission's default_factory would give the
                # superseding version a brand-new identity_id too, and
                # we'd be right back to every edit orphaning every Goal.
                new_mission.identity_id = str(current["identity_id"])
                await conn.execute(
                    "UPDATE mission SET status = 'archived', updated_at = $1 WHERE id = $2;",
                    now, current["id"],
                )
                log.info(
                    f"Superseding Mission v{current['version']} -> v{new_mission.version}: "
                    f"'{new_mission.title}'"
                )

            await conn.execute(
                """
                INSERT INTO mission
                    (id, identity_id, version, title, statement, principles, status,
                     created_at, updated_at, previous_version_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);
                """,
                new_mission.id, new_mission.identity_id, new_mission.version, new_mission.title,
                new_mission.statement, json.dumps(new_mission.principles),
                new_mission.status.value, new_mission.created_at,
                new_mission.updated_at, new_mission.previous_version_id,
            )

    return new_mission
