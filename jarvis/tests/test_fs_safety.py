"""Unit tests for capabilities/primitives/_fs_safety.py — pure Python,
no DB. Covers real adversarial traversal attempts, not just happy-path
navigation, per M6_HANDOVER_PROMPT.md's explicit test-case requirement."""
from pathlib import Path

import pytest

from capabilities.primitives._fs_safety import (
    PathBlocked, PathNotAllowed, load_allowed_roots, resolve_and_authorize,
)


def _roots(tmp_path):
    root = tmp_path / "Projects"
    root.mkdir()
    return [root]


def test_relative_path_within_root_resolves(tmp_path):
    roots = _roots(tmp_path)
    (roots[0] / "jarvis").mkdir()
    result = resolve_and_authorize("jarvis", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])
    assert result == (roots[0] / "jarvis").resolve()


def test_nested_relative_navigation_resolves(tmp_path):
    roots = _roots(tmp_path)
    nested = roots[0] / "jarvis" / "notes.txt"
    nested.parent.mkdir()
    result = resolve_and_authorize("jarvis/notes.txt", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])
    assert result == nested.resolve()


def test_dotdot_traversal_outside_root_is_rejected(tmp_path):
    roots = _roots(tmp_path)
    with pytest.raises(PathNotAllowed):
        resolve_and_authorize("../../etc/passwd", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])


def test_dotdot_that_resolves_back_inside_root_is_allowed(tmp_path):
    roots = _roots(tmp_path)
    (roots[0] / "a" / "b").mkdir(parents=True)
    result = resolve_and_authorize("a/b/../../a", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])
    assert result == (roots[0] / "a").resolve()


def test_absolute_path_outside_every_root_is_rejected(tmp_path):
    roots = _roots(tmp_path)
    with pytest.raises(PathNotAllowed):
        resolve_and_authorize(str(tmp_path / "Elsewhere" / "file.txt"), cwd=roots[0], allowed_roots=roots, blocked_patterns=[])


def test_absolute_path_inside_root_is_allowed(tmp_path):
    roots = _roots(tmp_path)
    target = roots[0] / "README.md"
    result = resolve_and_authorize(str(target), cwd=roots[0], allowed_roots=roots, blocked_patterns=[])
    assert result == target.resolve()


def test_symlink_escaping_root_is_rejected(tmp_path):
    roots = _roots(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    link = roots[0] / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathNotAllowed):
        resolve_and_authorize("escape", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])


def test_root_itself_is_allowed(tmp_path):
    roots = _roots(tmp_path)
    result = resolve_and_authorize(".", cwd=roots[0], allowed_roots=roots, blocked_patterns=[])
    assert result == roots[0].resolve()


def test_blocklisted_dotfile_inside_allowed_root_is_blocked(tmp_path):
    roots = _roots(tmp_path)
    with pytest.raises(PathBlocked):
        resolve_and_authorize(".env", cwd=roots[0], allowed_roots=roots, blocked_patterns=[".env"])


def test_blocklisted_directory_component_anywhere_in_path_is_blocked(tmp_path):
    roots = _roots(tmp_path)
    with pytest.raises(PathBlocked):
        resolve_and_authorize("jarvis/.ssh/id_rsa", cwd=roots[0], allowed_roots=roots, blocked_patterns=[".ssh", "id_rsa*"])


def test_blocklist_glob_matches_key_file(tmp_path):
    roots = _roots(tmp_path)
    with pytest.raises(PathBlocked):
        resolve_and_authorize("secrets/prod.pem", cwd=roots[0], allowed_roots=roots, blocked_patterns=["*.pem"])


def test_non_blocklisted_file_in_allowed_root_passes(tmp_path):
    roots = _roots(tmp_path)
    result = resolve_and_authorize("README.md", cwd=roots[0], allowed_roots=roots, blocked_patterns=[".ssh", "*.pem"])
    assert result == (roots[0] / "README.md").resolve()


def test_load_allowed_roots_expands_and_resolves(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Projects").mkdir()
    roots = load_allowed_roots(["~/Projects"])
    assert roots == [(tmp_path / "Projects").resolve()]
