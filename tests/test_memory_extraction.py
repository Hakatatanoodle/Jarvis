"""Unit tests for memory/extraction.py's validation + fail-closed path.
complete_json() is mocked — this tests the deterministic re-validation
layer, not any real model, matching how conversation/api.py's own
routing tests mock the LLM boundary.

Covers the 2026-08-15 worth-based, multi-candidate revision — see
memory/extraction.py's module docstring for why v1 (shape-based,
single-candidate) was a real bug, not a simplification."""
from unittest.mock import AsyncMock, patch

from contracts.enums import MemoryType
from contracts.memory_candidate import MemoryScope
from memory.extraction import _EXTRACTION_SYSTEM, extract_candidates


async def test_single_valid_candidate():
    good = {"candidates": [
        {"type": "Fact", "title": "user's name", "value": "Yochan", "scope": "durable", "confidence": 0.95},
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=good)):
        cs = await extract_candidates("My name is Yochan.")
    assert len(cs) == 1
    assert cs[0].type is MemoryType.FACT
    assert cs[0].scope is MemoryScope.DURABLE
    assert cs[0].explicit is True
    assert cs[0].value == "Yochan"


async def test_multiple_candidates_from_one_dense_message():
    # The real dogfooding case this revision fixes: a long, multi-part
    # statement of values/aspirations should yield several candidates,
    # not zero and not one compressed/lossy one.
    good = {"candidates": [
        {"type": "Fact", "title": "what motivates the user", "value": "financial freedom",
         "scope": "durable", "confidence": 0.8},
        {"type": "Fact", "title": "user's values", "value": "good relationships and staying healthy",
         "scope": "durable", "confidence": 0.8},
        {"type": "Preference", "title": "user's music taste while working", "value": "music/rock",
         "scope": "durable", "confidence": 0.6},
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=good)):
        cs = await extract_candidates(
            "i have a dream that one day i will be financially free, have good "
            "relationships and be healthy, with music playing while I work"
        )
    assert len(cs) == 3
    titles = {c.title for c in cs}
    assert "what motivates the user" in titles
    assert "user's values" in titles


async def test_empty_candidate_list_is_valid_not_a_reject():
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value={"candidates": []})):
        assert await extract_candidates("what's the weather like") == []


async def test_llm_unavailable_returns_empty_list():
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=None)):
        assert await extract_candidates("My name is Yochan.") == []


async def test_missing_candidates_key_returns_empty_list():
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value={"oops": []})):
        assert await extract_candidates("whatever") == []


async def test_one_bad_entry_only_drops_that_entry():
    mixed = {"candidates": [
        {"type": "Fact", "title": "user's name", "value": "Yochan", "scope": "durable", "confidence": 0.9},
        {"type": "NotARealType", "title": "x", "value": "y", "scope": "durable", "confidence": 0.9},
        {"type": "Fact", "title": "", "value": "y", "scope": "durable"},  # missing title
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=mixed)):
        cs = await extract_candidates("whatever")
    assert len(cs) == 1
    assert cs[0].value == "Yochan"


async def test_invalid_scope_drops_that_entry():
    bad = {"candidates": [
        {"type": "Fact", "title": "x", "value": "y", "scope": "forever", "confidence": 0.9},
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=bad)):
        assert await extract_candidates("whatever") == []


async def test_null_type_allowed_for_taxonomy_gap():
    gap = {"candidates": [
        {"type": None, "title": "user's blood type", "value": "O negative", "scope": "durable", "confidence": 0.8},
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=gap)):
        cs = await extract_candidates("My blood type is O negative.")
    assert len(cs) == 1
    assert cs[0].type is None
    assert cs[0].taxonomy_fit is False


async def test_malformed_confidence_defaults_instead_of_dropping():
    ok = {"candidates": [
        {"type": "Fact", "title": "x", "value": "y", "scope": "durable", "confidence": "not-a-number"},
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=ok)):
        cs = await extract_candidates("whatever")
    assert len(cs) == 1
    assert cs[0].confidence == 0.7


async def test_more_than_max_candidates_is_truncated_not_rejected():
    many = {"candidates": [
        {"type": "Fact", "title": f"fact {i}", "value": f"value {i}", "scope": "durable", "confidence": 0.8}
        for i in range(10)
    ]}
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=many)):
        cs = await extract_candidates("a very long list of things")
    assert len(cs) == 5  # _MAX_CANDIDATES


# --- 2026-08-16: known_memories -> title/type-reuse for corrections ---
# Fixes a real live bug: "I like girls" then "I don't like girls" was
# stored as two separate, contradictory memories because extraction had
# no visibility into the existing title. See module docstring.

async def test_no_known_memories_uses_plain_user_text_as_prompt():
    captured = {}

    async def fake_complete_json(system, prompt, task_type, max_tokens):
        captured["prompt"] = prompt
        return {"candidates": []}

    with patch("memory.extraction.complete_json", new=AsyncMock(side_effect=fake_complete_json)):
        await extract_candidates("My name is Yochan.")

    assert captured["prompt"] == "My name is Yochan."


async def test_known_memories_are_included_in_the_prompt():
    captured = {}

    async def fake_complete_json(system, prompt, task_type, max_tokens):
        captured["prompt"] = prompt
        return {"candidates": []}

    known = [("user's romantic attraction", "likes girls", "Preference")]
    with patch("memory.extraction.complete_json", new=AsyncMock(side_effect=fake_complete_json)):
        await extract_candidates("I don't like girls", known_memories=known)

    assert "user's romantic attraction" in captured["prompt"]
    assert "likes girls" in captured["prompt"]
    assert "[Preference]" in captured["prompt"]
    assert "I don't like girls" in captured["prompt"]


async def test_extraction_can_reuse_an_exact_known_title():
    # Doesn't test the LLM's judgment (that's not mockable/meaningful
    # here) — proves the plumbing: if the (mocked) model does what the
    # prompt asks and reuses the exact title, it comes through as a
    # valid candidate with that exact title.
    good = {"candidates": [
        {"type": "Preference", "title": "user's romantic attraction", "value": "doesn't like girls",
         "scope": "durable", "confidence": 0.9},
    ]}
    known = [("user's romantic attraction", "likes girls", "Preference")]
    with patch("memory.extraction.complete_json", new=AsyncMock(return_value=good)):
        cs = await extract_candidates("I don't like girls", known_memories=known)

    assert len(cs) == 1
    assert cs[0].title == "user's romantic attraction"
    assert cs[0].value == "doesn't like girls"


async def test_known_memories_list_is_capped_in_the_prompt():
    captured = {}

    async def fake_complete_json(system, prompt, task_type, max_tokens):
        captured["prompt"] = prompt
        return {"candidates": []}

    known = [(f"title {i}", f"value {i}", "Fact") for i in range(30)]
    with patch("memory.extraction.complete_json", new=AsyncMock(side_effect=fake_complete_json)):
        await extract_candidates("whatever", known_memories=known)

    assert "title 19" in captured["prompt"]
    assert "title 20" not in captured["prompt"]  # _MAX_KNOWN_MEMORIES_SHOWN == 20


# --- 2026-08-18: facts about Jarvis itself must never be extracted ---
# Live regression: "I'm naming you Infinity" got captured as an
# ordinary user-fact Memory, which then polluted the "What you know
# about the user" section and caused a real name-confusion bug (Jarvis
# calling the user "Infinity", denying it knew the user's real name).
# See ARCHITECTURE_ISSUES.md's 2026-08-18 entry. Same honest limitation
# as other prompt-only fixes — this can only assert the prompt now says
# the right thing, not that the model always follows it.

def test_extraction_prompt_excludes_facts_about_jarvis_itself():
    lowered = _EXTRACTION_SYSTEM.lower()
    assert "jarvis itself" in lowered
    assert "never a fact about the user" in lowered
