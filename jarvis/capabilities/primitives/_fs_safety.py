"""V1-M6: deterministic, Python-only enforcement of the allow-listed-
roots + no-delete + blocklist decisions in M6_HANDOVER_PROMPT.md. Never
trusted to the model's own judgment — extraction only ever proposes a
raw path string; every path from that string to disk goes through
`resolve_and_authorize` here first.

Same "reject deterministically" precedent as file_ops.py's old
_safe_path, but that check (no '/', '\\', '..' in a bare filename)
does not generalize to real multi-segment paths — this is a redesign,
not an extension, per the handover's explicit instruction.

Resolution algorithm:
1. Join the raw input against the current working directory (relative)
   or treat it as absolute, then Path.resolve() — the REAL,
   symlink-resolved absolute path. Never string concatenation / naive
   join (that's exactly how "../../../etc/passwd"-style traversal
   defeats a folder-name-only check).
2. Verify the resolved path is actually a descendant of (or equal to)
   one of the allow-listed roots, themselves resolved the same way at
   load time. A path resolving outside every allowed root is refused,
   not clamped or reinterpreted.
3. Separately, check the resolved path's segments against the
   blocklist glob patterns. This runs even for a path inside an
   allowed root — an allowed root like ~/Projects does not guarantee
   nothing sensitive ever ends up under it (a .env file in a project
   directory is extremely common).
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable


class PathNotAllowed(Exception):
    """Resolved path is not a descendant of any allow-listed root."""


class PathBlocked(Exception):
    """Resolved path matches a sensitive-pattern blocklist entry, even
    though it is within an allowed root."""


def load_allowed_roots(raw_roots: Iterable[str]) -> list[Path]:
    """Expand ~ and resolve each configured root once. Roots that don't
    exist yet are kept (a root can be created later) but still resolved
    via os.path semantics (strict=False) so traversal comparisons below
    are always against a real absolute path."""
    return [Path(r).expanduser().resolve(strict=False) for r in raw_roots]


def _is_descendant(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _matches_blocklist(path: Path, patterns: Iterable[str]) -> bool:
    # Checked against every segment of the resolved path, not just the
    # final filename, so e.g. ".ssh" anywhere in the path (a directory
    # component, not only a leaf file) trips it, and multi-segment
    # patterns (".git/config", "Library/Keychains") are checked against
    # the joined tail of segments as well as each individual one.
    parts = path.parts
    joined_suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    candidates = list(parts) + joined_suffixes
    return any(fnmatch.fnmatch(c, pat) for c in candidates for pat in patterns)


def resolve_and_authorize(
    raw_path: str,
    cwd: Path,
    allowed_roots: list[Path],
    blocked_patterns: Iterable[str],
) -> Path:
    """The one function every fs.read / fs.write / navigation step must
    go through. Raises PathNotAllowed or PathBlocked; returns the real,
    resolved, authorized absolute Path otherwise."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    real = candidate.resolve(strict=False)

    if not any(_is_descendant(real, root) for root in allowed_roots):
        raise PathNotAllowed(
            f"'{raw_path}' resolves to {real}, which is outside every allowed root "
            f"({', '.join(str(r) for r in allowed_roots)})."
        )
    if _matches_blocklist(real, blocked_patterns):
        raise PathBlocked(f"'{raw_path}' resolves to {real}, which matches a blocked sensitive-file pattern.")
    return real
