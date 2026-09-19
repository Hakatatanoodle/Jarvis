"""Capability Registry (CAP-05, CAP-10) — id -> (Capability metadata, executor).
Executor signature: async def executor(parameters: dict) -> dict (output)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from contracts.capability import Capability

Executor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_REGISTRY: dict[str, tuple[Capability, Executor]] = {}


def register(capability: Capability, executor: Executor) -> None:
    _REGISTRY[capability.id] = (capability, executor)


def get(capability_id: str) -> Optional[tuple[Capability, Executor]]:
    return _REGISTRY.get(capability_id)


def list_capabilities() -> list[Capability]:
    return [c for c, _ in _REGISTRY.values()]
