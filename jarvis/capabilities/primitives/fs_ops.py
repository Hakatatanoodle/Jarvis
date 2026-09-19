"""V1-M6: fs.read / fs.write — real navigation of person-designated
folders, replacing file_ops.py's fixed data/files/ sandbox. Read and
write only (settled requirement #2, M6_HANDOVER_PROMPT.md) — no delete
capability in this milestone.

Enforcement lives in two places, deliberately redundant:
  1. capability_invocation/dispatch.py resolves+authorizes the path
     BEFORE creating the Action, so the confirmation prompt (for
     writes) can show the real resolved path, and a disallowed path
     never even becomes an Action.
  2. The executors here re-run the exact same check before touching
     disk. This is defense-in-depth for the case an Action gets
     created directly (bypassing dispatch — e.g. by a future caller,
     or in tests, same shape as tests/test_m5_capabilities.py's
     `_make_plan_action` helper) rather than trusting that every
     caller went through dispatch first.

fs.write's baseline_risk_level is MEDIUM (same as calendar.create_event)
but that's not what forces its confirmation — force_confirmation=True
(contracts/capability.py) makes permission.api.check_permission()
always return CONFIRMATION_REQUIRED for it, unconditionally, per the
settled requirement #3. fs.read has no such override: reads are never
confirmed (see module docstring in capability_invocation/dispatch.py's
fs-navigation section for why).
"""
from __future__ import annotations

from pathlib import Path

from capabilities.primitives._fs_safety import (
    PathBlocked, PathNotAllowed, load_allowed_roots, resolve_and_authorize,
)
from config.loader import load_config
from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel
from capabilities.registry import register

_ALLOWED_ROOTS: list[Path] = []
_BLOCKED_PATTERNS: list[str] = []


def _load_fs_config() -> None:
    global _ALLOWED_ROOTS, _BLOCKED_PATTERNS
    cfg = load_config()
    _ALLOWED_ROOTS = load_allowed_roots(cfg.get("filesystem.allowed_roots", []))
    _BLOCKED_PATTERNS = list(cfg.get("filesystem.blocked_patterns", []))


def configure_for_test(roots: list[Path], patterns: list[str]) -> None:
    """Test-only hook to point fs_ops at a tmp_path sandbox instead of
    the real config/default.yaml roots, without touching the config
    file. Never called from production code paths."""
    global _ALLOWED_ROOTS, _BLOCKED_PATTERNS
    _ALLOWED_ROOTS = roots
    _BLOCKED_PATTERNS = patterns


_load_fs_config()


def _authorize(path_str: str) -> Path:
    # Executors only ever receive paths dispatch has already resolved
    # to absolute (see module docstring) — cwd is irrelevant once a
    # path is absolute, so Path("/") is a harmless placeholder here,
    # never actually used for resolution.
    return resolve_and_authorize(path_str, cwd=Path("/"), allowed_roots=_ALLOWED_ROOTS, blocked_patterns=_BLOCKED_PATTERNS)


async def _read(parameters: dict) -> dict:
    real = _authorize(parameters["path"])
    if not real.exists():
        raise FileNotFoundError(f"{real} does not exist")
    if real.is_dir():
        return {"path": str(real), "is_directory": True, "entries": sorted(p.name for p in real.iterdir())}
    return {"path": str(real), "content": real.read_text()}


async def _write(parameters: dict) -> dict:
    real = _authorize(parameters["path"])
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(parameters["content"])
    return {"path": str(real), "bytes_written": len(parameters["content"])}


register(
    Capability(
        id="fs.read", name="Read File or Directory",
        description="Reads a file's content, or lists a directory, within an allow-listed root.",
        category="System", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["fs.read"], supports_undo=True,
    ),
    _read,
)
register(
    Capability(
        id="fs.write", name="Write File",
        description="Creates or overwrites a file within an allow-listed root. Always requires confirmation.",
        category="System", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.MEDIUM, required_permissions=["fs.write"], supports_undo=False,
        force_confirmation=True,
    ),
    _write,
)

__all__ = ["PathBlocked", "PathNotAllowed"]
