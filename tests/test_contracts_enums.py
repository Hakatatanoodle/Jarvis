"""Regression coverage for contracts/enums.py.

Added 2026-09-08 after a real dogfooding crash: MemoryType.RELATIONSHIP
and MemoryType.CONSTRAINT were silently lost during an unrelated edit
(a str_replace whose old_str matched only PREFERENCE/FACT/SKILL,
splitting the rest of the class body onto the next class defined below
it — valid Python, wrong enum, no import error, nothing to catch it).
memory/extraction.py's own prompt still listed "Relationship" as a
choice, so the LLM picked it, and reading a memory row already stored
with that type crashed the whole turn with an uncaught
`ValueError: 'Relationship' is not a valid MemoryType` — 462 passing
tests at the time never touched RELATIONSHIP/CONSTRAINT, so nothing
caught it before it reached a live session.

These tests pin the exact member sets these enums are the single source
of truth for, so any future edit that silently drops/moves a member
fails a test immediately instead of surfacing as a live crash on
whatever real data happens to hit it first."""
from contracts.enums import MemoryType, ParamType


def test_memory_type_has_all_five_canonical_members():
    # memory/extraction.py's _EXTRACTION_SYSTEM prompt offers exactly
    # these five (plus null) to the LLM — this set is the actual
    # contract, not just "whatever the enum currently happens to have."
    assert {t.value for t in MemoryType} == {
        "Preference", "Fact", "Skill", "Relationship", "Constraint",
    }


def test_param_type_has_only_its_own_four_members():
    # ParamType (contracts/capability.py's ParamSpec) is a completely
    # different, unrelated enum — this pins it apart from MemoryType so
    # the two can never again bleed into each other unnoticed.
    assert {t.value for t in ParamType} == {"string", "enum", "datetime", "bool"}
