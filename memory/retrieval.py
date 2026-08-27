"""V1-M3 Decision 5: "simple implementation now, replaceable boundary
later." `MemoryRetriever` is a Protocol so Conversation and Reasoning
depend on the interface, not this specific implementation — a future
SemanticMemoryRetriever/VectorMemoryRetriever (pgvector is already
provisioned, INF-02) can swap in behind the same call shape without
touching any caller.

SimpleMemoryRetriever is deliberately dumb: substring match against
title/value if a query is given, else just importance-then-recency.
Reasoning already has its own richer scoring (reasoning/scoring.py) and
does not use this module — this is for Conversation, which has nothing
like that yet.
"""
from __future__ import annotations

from typing import Protocol

from contracts.enums import Importance, MemoryStatus
from contracts.memory import Memory
from memory.api import list_memories

_IMPORTANCE_RANK = {Importance.HIGH: 2, Importance.MEDIUM: 1, Importance.LOW: 0}


class MemoryRetriever(Protocol):
    async def retrieve(self, query: str = "", limit: int = 5) -> list[Memory]: ...


class SimpleMemoryRetriever:
    async def retrieve(self, query: str = "", limit: int = 5) -> list[Memory]:
        memories = await list_memories(status=MemoryStatus.ACTIVE)
        if query:
            q = query.lower()
            matched = [m for m in memories if q in m.title.lower() or q in m.value.lower()]
            memories = matched if matched else memories
        memories.sort(key=lambda m: (_IMPORTANCE_RANK[m.importance], m.updated_at), reverse=True)
        return memories[:limit]
