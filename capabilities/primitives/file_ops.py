"""file.read / file.write — sandboxed to data/files/ (no path traversal).
Low-risk-first primitives (§5.1)."""
from __future__ import annotations

from pathlib import Path

from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel
from capabilities.registry import register

_SANDBOX = Path(__file__).parent.parent.parent / "data" / "files"
_SANDBOX.mkdir(parents=True, exist_ok=True)


def _safe_path(filename: str) -> Path:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"Invalid filename: {filename!r} (no path separators or '..')")
    return _SANDBOX / filename


async def _read(parameters: dict) -> dict:
    path = _safe_path(parameters["filename"])
    if not path.exists():
        raise FileNotFoundError(f"{parameters['filename']} does not exist")
    return {"content": path.read_text()}


async def _write(parameters: dict) -> dict:
    path = _safe_path(parameters["filename"])
    path.write_text(parameters["content"])
    return {"filename": parameters["filename"], "bytes_written": len(parameters["content"])}


register(
    Capability(
        id="file.read", name="Read File", description="Reads a file from the local sandbox.",
        category="System", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["file.read"], supports_undo=True,
    ),
    _read,
)
register(
    Capability(
        id="file.write", name="Write File", description="Writes a file in the local sandbox.",
        category="System", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["file.write"], supports_undo=True,
    ),
    _write,
)
