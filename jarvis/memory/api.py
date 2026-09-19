"""
Memory API (§7: public interface for the Memory System; §9 M2).

Per decision #10 (§4): Memory has exactly one writer — this module.
Every other subsystem may only *propose* (via LearningProposal, §5.2,
not built until M8); nothing else calls a memory-create path directly.

M2 scope is rule-based (deterministic) creation only (§5.1) — no AI
judgment, no probabilistic proposals. `remember_fact` is the deterministic
entry point: given a (type, source) pair, it either creates a new Memory
or merges into an existing one, per §6.3's own validation rule
("Duplicate memories should merge rather than multiply").
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from contracts.memory import Memory
from contracts.memory_candidate import MemoryCandidate
from contracts.taxonomy_gap_proposal import TaxonomyGapProposal
from contracts.enums import Importance, MemoryStatus, MemoryType
from infra.logging import get_logger
from infra.storage import connection

log = get_logger("memory.api")

# Same situation as goals/api.py's _ALLOWED_TRANSITIONS: §6.3 gives Memory
# three status values (Active/Archived/Forgotten) but no lifecycle
# diagram at all — not even the partial one Goal had. This is my
# best-faith minimal transition table: reactivation is allowed
# (Archived -> Active), Forgotten is terminal (matches "forget this"
# being presented as final in constitution.md's User-Controlled category).
_ALLOWED_TRANSITIONS: dict[MemoryStatus, set[MemoryStatus]] = {
    MemoryStatus.ACTIVE: {MemoryStatus.ARCHIVED, MemoryStatus.FORGOTTEN},
    MemoryStatus.ARCHIVED: {MemoryStatus.ACTIVE, MemoryStatus.FORGOTTEN},
    MemoryStatus.FORGOTTEN: set(),
}


class MemoryNotFoundError(Exception):
    pass


class InvalidMemoryTransitionError(Exception):
    pass


def _row_to_memory(row) -> Memory:
    return Memory(
        id=str(row["id"]),
        type=MemoryType(row["type"]),
        title=row["title"],
        value=row["value"],
        confidence=row["confidence"],
        importance=Importance(row["importance"]),
        status=MemoryStatus(row["status"]),
        source_ids=json.loads(row["source_ids"]),
        related_goal_ids=json.loads(row["related_goal_ids"]),
        related_memory_ids=json.loads(row["related_memory_ids"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


async def get_memory(memory_id: str) -> Optional[Memory]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM memory WHERE id = $1;", memory_id)
        return _row_to_memory(row) if row else None


async def list_memories(
    status: Optional[MemoryStatus] = None,
    type: Optional[MemoryType] = None,
) -> list[Memory]:
    query = "SELECT * FROM memory WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        params.append(status.value)
        query += f" AND status = ${len(params)}"
    if type is not None:
        params.append(type.value)
        query += f" AND type = ${len(params)}"
    query += " ORDER BY created_at ASC;"

    async with connection() as conn:
        rows = await conn.fetch(query, *params)
        return [_row_to_memory(r) for r in rows]


async def create_memory(
    type: MemoryType,
    title: str,
    value: str,
    source_ids: list[str],
    confidence: float = 1.0,
    importance: Importance = Importance.MEDIUM,
    related_goal_ids: Optional[list[str]] = None,
    related_memory_ids: Optional[list[str]] = None,
) -> Memory:
    now = datetime.now(timezone.utc)
    new_memory = Memory(
        type=type,
        title=title,
        value=value,
        confidence=confidence,
        importance=importance,
        status=MemoryStatus.ACTIVE,
        source_ids=source_ids,
        related_goal_ids=related_goal_ids or [],
        related_memory_ids=related_memory_ids or [],
        created_at=now,
        updated_at=now,
        version=1,
    )

    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO memory
                (id, type, title, value, confidence, importance, status,
                 source_ids, related_goal_ids, related_memory_ids,
                 created_at, updated_at, version)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13);
            """,
            new_memory.id, new_memory.type.value, new_memory.title, new_memory.value,
            new_memory.confidence, new_memory.importance.value, new_memory.status.value,
            json.dumps(new_memory.source_ids), json.dumps(new_memory.related_goal_ids),
            json.dumps(new_memory.related_memory_ids), new_memory.created_at,
            new_memory.updated_at, new_memory.version,
        )
    log.info(f"Created Memory '{new_memory.title}' ({new_memory.id}), type={new_memory.type.value}")
    return new_memory


async def _snapshot_and_update(conn, current: Memory, new_fields: dict[str, Any], reason: str) -> Memory:
    """Same pattern as goals/api.py's _snapshot_and_update — the one write
    path for every Memory mutation. Snapshot first, then apply."""
    now = datetime.now(timezone.utc)

    await conn.execute(
        """
        INSERT INTO memory_history (id, memory_id, version, snapshot, reason, changed_at)
        VALUES ($1, $2, $3, $4, $5, $6);
        """,
        str(uuid4()), current.id, current.version,
        current.model_dump_json(), reason, now,
    )

    merged = current.model_dump()
    merged.update(new_fields)
    merged["version"] = current.version + 1
    merged["updated_at"] = now

    candidate = Memory(**merged)  # re-validates the whole proposed state

    await conn.execute(
        """
        UPDATE memory SET
            title = $1, value = $2, confidence = $3, importance = $4,
            status = $5, source_ids = $6, related_goal_ids = $7,
            related_memory_ids = $8, updated_at = $9, version = $10
        WHERE id = $11;
        """,
        candidate.title, candidate.value, candidate.confidence, candidate.importance.value,
        candidate.status.value, json.dumps(candidate.source_ids),
        json.dumps(candidate.related_goal_ids), json.dumps(candidate.related_memory_ids),
        candidate.updated_at, candidate.version, candidate.id,
    )
    return candidate


async def update_memory(
    memory_id: str,
    reason: str,
    title: Optional[str] = None,
    value: Optional[str] = None,
    confidence: Optional[float] = None,
    importance: Optional[Importance] = None,
) -> Memory:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM memory WHERE id = $1 FOR UPDATE;", memory_id)
            if row is None:
                raise MemoryNotFoundError(f"Memory {memory_id} does not exist")
            current = _row_to_memory(row)

            new_fields: dict[str, Any] = {}
            if title is not None:
                new_fields["title"] = title
            if value is not None:
                new_fields["value"] = value
            if confidence is not None:
                new_fields["confidence"] = confidence
            if importance is not None:
                new_fields["importance"] = importance

            updated = await _snapshot_and_update(conn, current, new_fields, reason)
    log.info(f"Updated Memory {memory_id}: {reason}")
    return updated


async def set_status(memory_id: str, new_status: MemoryStatus, reason: str) -> Memory:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM memory WHERE id = $1 FOR UPDATE;", memory_id)
            if row is None:
                raise MemoryNotFoundError(f"Memory {memory_id} does not exist")
            current = _row_to_memory(row)

            if new_status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidMemoryTransitionError(
                    f"Cannot transition Memory {memory_id} from {current.status.value} "
                    f"to {new_status.value}."
                )

            updated = await _snapshot_and_update(conn, current, {"status": new_status}, reason)
    log.info(f"Memory {memory_id} status: {current.status.value} -> {new_status.value} ({reason})")
    return updated


async def archive_memory(memory_id: str, reason: str) -> Memory:
    return await set_status(memory_id, MemoryStatus.ARCHIVED, reason)


async def forget_memory(memory_id: str, reason: str) -> Memory:
    return await set_status(memory_id, MemoryStatus.FORGOTTEN, reason)


async def get_memory_history(memory_id: str) -> list[dict]:
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT version, snapshot, reason, changed_at FROM memory_history "
            "WHERE memory_id = $1 ORDER BY version ASC;",
            memory_id,
        )
        return [
            {
                "version": r["version"],
                "snapshot": json.loads(r["snapshot"]),
                "reason": r["reason"],
                "changed_at": r["changed_at"],
            }
            for r in rows
        ]


async def remember_fact(
    type: MemoryType,
    title: str,
    value: str,
    source_ids: list[str],
    importance: Importance = Importance.MEDIUM,
    confidence: float = 1.0,
    related_goal_ids: Optional[list[str]] = None,
) -> Memory:
    """The deterministic rule-based entry point (§5.1 / decision #10).

    Callers (e.g. goals/api.py) use this instead of create_memory()
    directly whenever a rule fires — this is what implements §6.3's
    'Duplicate memories should merge rather than multiply': if an ACTIVE
    Memory already exists for the same type + overlapping source_ids,
    update it in place (versioned, via _snapshot_and_update) rather than
    creating a second one. Confidence defaults to 1.0 because rule-based
    creation is deterministic by definition ('No AI judgment required',
    constitution.md) — there's nothing probabilistic to express doubt about.
    """
    async with connection() as conn:
        async with conn.transaction():
            existing_row = await conn.fetchrow(
                """
                SELECT * FROM memory
                WHERE status = $1 AND type = $2
                  AND source_ids::jsonb ?| $3::text[]
                FOR UPDATE;
                """,
                MemoryStatus.ACTIVE.value, type.value, source_ids,
            )

            if existing_row is not None:
                current = _row_to_memory(existing_row)
                if current.title == title and current.value == value:
                    return current  # nothing actually changed — no-op, no version bump

                updated = await _snapshot_and_update(
                    conn, current,
                    {"title": title, "value": value},
                    reason="Merged: rule-based trigger fired again with updated facts",
                )
                log.info(f"Merged rule-based update into existing Memory {updated.id}")
                return updated

    return await create_memory(
        type=type, title=title, value=value, source_ids=source_ids,
        confidence=confidence, importance=importance, related_goal_ids=related_goal_ids,
    )


async def remember_from_candidate(candidate: MemoryCandidate, source_id: str) -> Memory:
    """V1-M3: the User-Controlled memory-creation entry point (constitution.md
    "User-Controlled (Authoritative)"), companion to remember_fact()'s
    Rule-Based path above — same module, same single writer, different
    trigger (an explicit user statement, already cleared by
    memory/candidate_policy.py, instead of a Goal state transition).

    Dedup/update key is (type, normalized title) — matches on an ACTIVE
    memory are treated as the user updating that fact (versioned via
    _snapshot_and_update), not a new memory. Per V1-M3 §10 this is
    deliberately simple: no semantic/fuzzy matching across different
    titles. A different title creates a separate memory rather than
    being silently merged — memory/extraction.py's prompt asks for
    stable, canonical titles specifically so repeated statements about
    the same fact naturally collide here.

    Callers must have already run the candidate through
    candidate_policy.decide() and gotten WRITE_DURABLE — this function
    does not re-check sensitivity, scope, or explicitness, only the
    taxonomy-fit invariant it cannot safely skip.
    """
    if candidate.type is None:
        raise ValueError(
            "remember_from_candidate requires a taxonomy-fit candidate "
            "(candidate.type is None — this should have been stopped "
            "at the TAXONOMY_GAP policy outcome, not reached here)"
        )

    async with connection() as conn:
        async with conn.transaction():
            existing_row = await conn.fetchrow(
                """
                SELECT * FROM memory
                WHERE status = $1 AND type = $2 AND lower(title) = lower($3)
                FOR UPDATE;
                """,
                MemoryStatus.ACTIVE.value, candidate.type.value, candidate.title,
            )

            if existing_row is not None:
                current = _row_to_memory(existing_row)
                if current.value == candidate.value:
                    return current  # identical restatement — no-op (V1-M3 acceptance F)

                merged_sources = current.source_ids if source_id in current.source_ids \
                    else current.source_ids + [source_id]
                updated = await _snapshot_and_update(
                    conn, current,
                    {"value": candidate.value, "confidence": candidate.confidence,
                     "source_ids": merged_sources},
                    reason="User restated/updated this memory in conversation",
                )
                log.info(f"Updated Memory {updated.id} from user-controlled candidate")
                return updated

    return await create_memory(
        type=candidate.type, title=candidate.title, value=candidate.value,
        source_ids=[source_id], confidence=candidate.confidence, importance=candidate.importance,
    )


async def record_taxonomy_gap(candidate: MemoryCandidate) -> TaxonomyGapProposal:
    """V1-M3 follow-up (2026-08-15b): persists a TAXONOMY_GAP policy
    outcome instead of just logging it — see
    infra/migrations/0017_taxonomy_gap_proposal.sql for why. Takes a
    full MemoryCandidate (not loose fields) so this stays consistent
    with remember_from_candidate's signature; only candidate.type is
    guaranteed to already be None here (that's the whole reason this
    path was taken instead of remember_from_candidate)."""
    proposal = TaxonomyGapProposal(
        title=candidate.title, value=candidate.value,
        raw_user_text=candidate.raw_user_text, confidence=candidate.confidence,
    )
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO taxonomy_gap_proposal (id, title, value, raw_user_text, confidence, created_at)
            VALUES ($1, $2, $3, $4, $5, $6);
            """,
            proposal.id, proposal.title, proposal.value, proposal.raw_user_text,
            proposal.confidence, proposal.created_at,
        )
    log.info(f"Recorded taxonomy gap proposal {proposal.id}: '{proposal.title}' = '{proposal.value}'")
    return proposal


async def list_taxonomy_gaps(limit: int = 50) -> list[TaxonomyGapProposal]:
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM taxonomy_gap_proposal ORDER BY created_at DESC LIMIT $1;", limit
        )
    return [
        TaxonomyGapProposal(
            id=str(r["id"]), title=r["title"], value=r["value"], raw_user_text=r["raw_user_text"],
            confidence=r["confidence"], created_at=r["created_at"],
        )
        for r in rows
    ]
