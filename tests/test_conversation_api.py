"""Integration tests for conversation/api.py against a real Postgres
instance — matches acceptance criteria in V1_M1_IMPLEMENTATION_PLAN.md §8.
"""
import pytest
from unittest.mock import AsyncMock, patch

from conversation.api import (
    _claims_unbacked_action,
    _matches_fast_path_greeting,
    _strip_markdown_formatting,
    _CONVERSATIONAL_SYSTEM,
    _GROUNDED_SYSTEM,
    _REAL_INTERFACE,
    _ROUTING_SYSTEM,
    handle,
    resolve_pending_memory,
    resolve_pending_state_change,
)
from contracts.enums import GoalStatus, GoalType, MemoryStatus, MemoryType
from contracts.memory_candidate import MemoryCandidate, MemoryScope
from contracts.state_change_candidate import StateChangeCandidate, StateChangeOperation
from action_engine.api import list_actions
from goals.api import create_goal, list_goals, set_status
from infra.storage import connection
from memory.api import create_memory, list_memories, list_taxonomy_gaps
from mission.api import get_active_mission, set_mission
from reasoning.api import list_decisions, list_decisions_with_intent

pytestmark = pytest.mark.usefixtures("clean_db")


# --- capability-claim guard (real bug: "I've noted the word 'coco'") --

@pytest.mark.parametrize("text,expected", [
    ("I've noted the word \"coco\" for the moment.", True),  # the actual reported bug
    ("I'll remind you about that tomorrow.", True),
    ("I've saved that to your profile.", True),
    ("Got it, I'll keep track of that for you.", True),
    ("Not much, just checking the weather.", False),
    ("I don't have access to your name at the moment.", False),
    ("Sure, I can help with that once it's built.", False),
])
def test_claims_unbacked_action_detector(text, expected):
    assert _claims_unbacked_action(text) == expected


async def test_conversational_reply_claiming_memory_gets_replaced_with_honesty():
    # Direct regression test for the reported bug: LLM claims to have
    # remembered something this turn when no memory write actually
    # happened (extract_candidates is unmocked here — real network is
    # unreachable in test env, so it fails closed to no candidates, same
    # as production would for genuinely non-memory-worthy text). Post-M3
    # persistence is real, but ONLY for a verified WRITE_DURABLE/
    # BLOCK_SENSITIVE decision this same turn (memory_grounding) — this
    # claim isn't backed by one, so it must still never reach the user
    # as-is.
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="I've noted the word 'coco' for the moment.")):
        outcome = await handle("remember this word = 'coco'")
    assert outcome.routed_to == "conversation"
    assert "noted the word" not in outcome.response_text.lower()
    assert "won't claim that" in outcome.response_text.lower()


# --- 2026-08-17: "what do you know about me" false-block bug ---------
# Live dogfooding: with real memories on file, a recall answer got
# blocked by this same guard and replaced by a fallback that was itself
# stale (claimed persistence "doesn't exist" — false post-M3). Two
# separate fixes: the fallback text (below), and prompt guidance
# steering the LLM toward present-tense recall phrasing instead of
# persistence-action phrasing for OLD memory (conversation/api.py's
# _CONVERSATIONAL_SYSTEM). The guard's own regex is intentionally
# UNCHANGED — these two tests document why: recall phrased safely isn't
# touched by it at all; recall phrased carelessly is still (correctly)
# caught. The prompt change is a probabilistic nudge, not a guarantee —
# these tests can't verify the LLM follows it, only that nothing
# downstream sabotages it either way.

async def test_present_tense_recall_of_existing_memory_is_not_blocked():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(type=MemoryType.FACT, title="user's name", value="Yochan", source_ids=["src-1"])
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[])), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(
             return_value="You're Yochan, and I know you like working mornings.")):
        outcome = await handle("what do you know about me?")
    assert outcome.response_text == "You're Yochan, and I know you like working mornings."


async def test_persistence_phrasing_about_old_memory_is_still_blocked():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(type=MemoryType.FACT, title="user's name", value="Yochan", source_ids=["src-1"])
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[])), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="I've noted that you're Yochan.")):
        outcome = await handle("what do you know about me?")
    assert "won't claim that" in outcome.response_text.lower()


# --- fast-path greeting matcher (pure function, no DB/LLM needed) -----

@pytest.mark.parametrize("text,expected", [
    ("hi", True), ("hello", True), ("hey", True), ("yo", True),
    ("good morning", True), ("goodmorning", True), ("good night", True),
    ("thanks", True), ("thank you", True), ("bye", True), ("hi jarvis", True),
    ("yo whatup sleeping ?", False),
    ("what should i work on today?", False),
    ("12345", False),
    ("night owl", False),
])
def test_fast_path_greeting_matcher(text, expected):
    assert _matches_fast_path_greeting(text) == expected


# --- acceptance table cases, against a real DB ------------------------

async def test_no_mission_gives_onboarding_not_a_raw_exception():
    # bug.md BUG-001: this used to be an unhandled NoActiveMissionError.
    outcome = await handle("hello jarvis")
    assert outcome.routed_to == "conversation"
    assert "mission set" in outcome.response_text.lower()


async def test_no_mission_onboarding_is_archived():
    await handle("hello jarvis")
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM conversation_turn WHERE user_text = 'hello jarvis';")
    assert row is not None
    assert row["routed_to"] == "conversation"


async def test_greeting_never_reaches_reasoning():
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.gather_grounded_evidence", new=AsyncMock()) as mock_gather:
        outcome = await handle("yo")
    assert outcome.routed_to == "conversation"
    mock_gather.assert_not_called()


async def test_planning_question_still_routes_to_reasoning():
    # Regression guard: routing to Reasoning's grounded evidence must
    # still happen for genuine planning requests — the 2026-08-2X
    # rewrite (see ARCHITECTURE_ISSUES.md) changed WHAT happens once
    # routed there (a free-form grounded answer, not a rigid template),
    # not WHETHER it gets routed. No LLM is mocked here, so the actual
    # grounded reply falls back to _render_evidence_plainly (real
    # network unreachable in test env) — assert on that, not internals.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    outcome = await handle("what should i work on today?")
    assert outcome.routed_to == "reasoning"
    assert "Build jarvis v1" in outcome.response_text


async def test_routing_llm_failure_defaults_to_reasoning_not_conversation():
    # §3: safer default on failure — an ungrounded conversational answer
    # risks asserting something about real goals; unclear input should
    # fall into the rigorous path, not out of it.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value=None)):
        outcome = await handle("12345")
    assert outcome.routed_to == "reasoning"


async def test_ambiguous_input_routed_conversational_by_llm_does_not_reach_reasoning():
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Ha, not much! What's up?")):
        outcome = await handle("why do you always talk about goals?")
    assert outcome.routed_to == "conversation"
    assert outcome.response_text == "Ha, not much! What's up?"


async def test_every_turn_archived_exactly_once_both_paths():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": True, "memory_candidate_possible": True, "state_change_possible": False})):
        await handle("what should i work on today?")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value=None)), \
         patch("conversation.api.complete", new=AsyncMock(return_value="hey!")):
        await handle("yo")

    async with connection() as conn:
        rows = await conn.fetch("SELECT routed_to FROM conversation_turn ORDER BY created_at;")
    assert [r["routed_to"] for r in rows] == ["reasoning", "conversation"]


async def test_slash_commands_unaffected_by_mission_gate():
    # plan §4: /mission set must still work with no Mission — that's how
    # the first one gets created. conversation.handle is never invoked
    # for slash commands at all (cli.py dispatches those separately),
    # so this just confirms set_mission itself has no such gate.
    m = await set_mission(title="Grow", statement="Statement.")
    assert m.version == 1


# --- M1 session-context follow-up (2026-08-09) -------------------------

async def test_recent_turns_get_fed_into_the_conversational_prompt():
    import conversation.api as conv_api
    await set_mission(title="Grow", statement="Statement.")

    captured_prompt = {}

    async def fake_complete(system, prompt, task_type, max_tokens):
        captured_prompt["value"] = prompt
        return "Sure thing, Yochan."

    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("call me Yochan from now on")
        await handle("what did I just ask you to call me?")

    assert "call me Yochan from now on" in captured_prompt["value"]


async def test_session_boundary_is_real_not_just_claimed():
    # Confirms the actual fix for "gone after /exit": a turn tagged with
    # a DIFFERENT session_id (simulating a prior process run) must never
    # be picked up as "recent" by this session.
    import conversation.api as conv_api
    await set_mission(title="Grow", statement="Statement.")

    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_turn (id, session_id, user_text, response_text, routed_to, created_at)
            VALUES (gen_random_uuid(), gen_random_uuid(), 'from a previous run', 'old reply', 'conversation', now());
            """
        )

    recent = await conv_api._recent_turns()
    assert all(t.user_text != "from a previous run" for t in recent)


async def test_first_message_this_session_has_empty_recent_context():
    import conversation.api as conv_api
    assert await conv_api._recent_turns() == []


# --- Fabricated-UI guard (found in dogfooding, 2026-08-10) ------------

@pytest.mark.parametrize("text,expected", [
    ("it's all listed for you under the Mission tab.", True),  # actual reported bug
    ("check the settings page for that.", True),
    ("click the dashboard button.", True),
    ("Monkey D. Luffy is a great character.", False),
    ("I don't know much about One Piece.", False),
    ("What do you think makes Luffy great?", False),
])
def test_mentions_fabricated_ui_detector(text, expected):
    from conversation.api import _mentions_fabricated_ui
    assert _mentions_fabricated_ui(text) == expected


async def test_conversational_reply_inventing_a_ui_gets_replaced_with_honesty():
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(
             return_value="It's all listed for you under the Mission tab."
         )):
        outcome = await handle("what are your capabilities?")
    assert outcome.routed_to == "conversation"
    assert "mission tab" not in outcome.response_text.lower()
    assert "terminal-only" in outcome.response_text.lower()


# --- Prompt-leakage guard (found in dogfooding, 2026-08-10) -----------

@pytest.mark.parametrize("text,expected", [
    ("Mission: I wanna be ironman\n\nLet's talk about that.", True),  # actual reported bug
    ("Capabilities: reading files, calendar events", True),
    ("User: what should i do next?", True),
    ("Since your mission is to be Iron Man, let's talk about that.", False),
    ("Sorry to hear about that, want to talk?", False),
])
def test_leaks_prompt_structure_detector(text, expected):
    from conversation.api import _leaks_prompt_structure
    assert _leaks_prompt_structure(text) == expected


async def test_conversational_reply_leaking_prompt_labels_gets_replaced():
    await set_mission(title="I wanna be ironman", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(
             return_value="Mission: I wanna be ironman\n\nLet's talk about becoming Iron Man."
         )):
        outcome = await handle("she rejected me, there's nothing left to explore")
    assert outcome.routed_to == "conversation"
    assert not outcome.response_text.lower().startswith("mission:")
    assert "garbled" in outcome.response_text.lower()


# --- V1-M3: Memory candidate detection wired into Conversation --------
# (2026-08-15 revision: extract_candidates returns a list now — see
# memory/extraction.py docstring — so mocks below return lists, and
# outcome.pending_memories is a list too.)

async def test_explicit_statement_creates_durable_memory():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="user's name", value="Yochan",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="My name is Yochan.",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Nice to meet you, Yochan!")):
        outcome = await handle("My name is Yochan.")

    assert outcome.pending_memories == []
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 1
    assert memories[0].value == "Yochan"


async def test_multiple_candidates_from_one_message_all_written():
    # The dogfooding case this revision fixes: a dense statement yields
    # several candidates, and all of them get evaluated/written, not
    # just the first.
    await set_mission(title="Grow", statement="Statement.")
    candidates = [
        MemoryCandidate(
            type=MemoryType.FACT, title="what motivates the user", value="financial freedom",
            scope=MemoryScope.DURABLE, explicit=True, raw_user_text="dream text",
        ),
        MemoryCandidate(
            type=MemoryType.FACT, title="user's values", value="good relationships and health",
            scope=MemoryScope.DURABLE, explicit=True, raw_user_text="dream text",
        ),
    ]
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=candidates)), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="That's a real blueprint.")):
        outcome = await handle("dream text")

    assert outcome.pending_memories == []
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 2
    assert {m.title for m in memories} == {"what motivates the user", "user's values"}


async def test_memory_action_grounding_reaches_the_prompt():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="user's name", value="Yochan",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="My name is Yochan.",
    )
    captured = {}

    async def fake_complete(system, prompt, task_type, max_tokens):
        captured["value"] = prompt
        return "Nice to meet you, Yochan!"

    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("My name is Yochan.")

    assert "Just saved to memory" in captured["value"]


async def test_second_mention_updates_rather_than_duplicates():
    await set_mission(title="Grow", statement="Statement.")
    first = MemoryCandidate(
        type=MemoryType.PREFERENCE, title="user's preferred editor", value="VS Code",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="I prefer VS Code",
    )
    second = MemoryCandidate(
        type=MemoryType.PREFERENCE, title="user's preferred editor", value="Neovim",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="Actually I switched to Neovim",
    )
    with patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Got it.")):
        with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[first])):
            await handle("I prefer VS Code")
        with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[second])):
            await handle("Actually I switched to Neovim")

    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 1
    assert memories[0].value == "Neovim"
    assert memories[0].version == 2


async def test_ambiguous_candidate_asks_for_confirmation_not_immediate_write():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="today's mood", value="tired",
        scope=MemoryScope.AMBIGUOUS, explicit=True, raw_user_text="I'm tired today",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Hope your day gets better.")):
        outcome = await handle("I'm tired today")

    assert len(outcome.pending_memories) == 1
    assert outcome.pending_memories[0].candidate.value == "tired"
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0


async def test_multiple_ambiguous_candidates_all_queued_for_confirmation():
    await set_mission(title="Grow", statement="Statement.")
    candidates = [
        MemoryCandidate(
            type=MemoryType.FACT, title="today's mood", value="tired",
            scope=MemoryScope.AMBIGUOUS, explicit=True, raw_user_text="text",
        ),
        MemoryCandidate(
            type=MemoryType.FACT, title="today's activity", value="debugging",
            scope=MemoryScope.AMBIGUOUS, explicit=True, raw_user_text="text",
        ),
    ]
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=candidates)), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Got it.")):
        outcome = await handle("text")

    assert len(outcome.pending_memories) == 2
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0


async def test_confirming_pending_memory_writes_it():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="today's mood", value="tired",
        scope=MemoryScope.AMBIGUOUS, explicit=True, raw_user_text="I'm tired today",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Hope your day gets better.")):
        outcome = await handle("I'm tired today")

    status = await resolve_pending_memory(outcome.pending_memories[0], approved=True)
    assert status is not None
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 1


async def test_declining_pending_memory_does_not_write():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="today's mood", value="tired",
        scope=MemoryScope.AMBIGUOUS, explicit=True, raw_user_text="I'm tired today",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Hope your day gets better.")):
        outcome = await handle("I'm tired today")

    status = await resolve_pending_memory(outcome.pending_memories[0], approved=False)
    assert status is None
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0


async def test_sensitive_candidate_never_written_and_never_asks_confirmation():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="a secret", value="my api key is sk-abc123",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="my api key is sk-abc123",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Understood.")):
        outcome = await handle("my api key is sk-abc123")

    assert outcome.pending_memories == []
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0


async def test_taxonomy_gap_candidate_not_written_but_recorded_as_gap():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=None, title="user's blood type", value="O negative",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="My blood type is O negative",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Good to know.")):
        outcome = await handle("My blood type is O negative")

    assert outcome.pending_memories == []
    assert outcome.response_text == "Good to know."
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0

    # 2026-08-15b: the gap must leave a durable trail, not disappear —
    # see ARCHITECTURE_ISSUES.md and infra/migrations/0017_taxonomy_gap_proposal.sql.
    gaps = await list_taxonomy_gaps()
    assert len(gaps) == 1
    assert gaps[0].title == "user's blood type"


async def test_session_scoped_candidate_not_written():
    await set_mission(title="Grow", statement="Statement.")
    candidate = MemoryCandidate(
        type=MemoryType.FACT, title="current activity", value="debugging",
        scope=MemoryScope.SESSION, explicit=True, raw_user_text="I'm debugging something right now",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[candidate])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Good luck with that.")):
        outcome = await handle("I'm debugging something right now")

    assert outcome.pending_memories == []
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 0


async def test_known_memories_are_fed_into_the_conversational_prompt():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(
        type=MemoryType.FACT, title="user's name", value="Yochan", source_ids=["src-1"],
    )
    captured = {}

    async def fake_complete(system, prompt, task_type, max_tokens):
        captured["value"] = prompt
        return "Hey Yochan!"

    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[])), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("hey")

    assert "user's name: Yochan" in captured["value"]


# --- 2026-08-18: regression test for "do you know my name" bug ---
# The bug: _format_known_memories() used an importance-ranked top-5 retriever.
# With 10+ Active memories all at Medium importance (default), the oldest
# fact ("user's name") was ranked out of the window and never reached the
# model's context. This test creates exactly that scenario: 10 other
# memories created AFTER "user's name", all Medium importance, and verifies
# the name still appears in the prompt. A test with only 1-2 memories would
# NOT reproduce this bug — the whole point is it only manifests once enough
# other memories exist to push the target out of a small top-N window.

async def test_format_known_memories_includes_oldest_fact_when_many_exist():
    """Regression test for the 'do you know my name' bug (2026-08-18).

    Creates 11 Active memories where 'user's name' is the OLDEST (created
    first) and all others are created after it. All memories are Medium
    importance (the default from remember_from_candidate). The old
    retriever-based _format_known_memories would return only the 5 most
    recent, losing 'user's name'. The fixed version must return the full
    Active set (capped at 50), so 'user's name' is still included.
    """
    import conversation.api as conv_api
    await set_mission(title="Grow", statement="Statement.")

    # Create "user's name" FIRST (oldest)
    await create_memory(
        type=MemoryType.FACT, title="user's name", value="Yochan", source_ids=["src-1"],
    )

    # Create 10 MORE memories AFTER it (simulating normal usage over time)
    # All at default Medium importance — this is the exact scenario that
    # pushed "user's name" out of the top-5 window
    for i in range(10):
        await create_memory(
            type=MemoryType.FACT,
            title=f"memory {i}",
            value=f"value {i}",
            source_ids=[f"src-{i+2}"],
        )

    # Verify we have 11 total Active memories
    all_memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(all_memories) == 11
    assert all_memories[0].title == "user's name"  # oldest first (created_at ASC)

    # Now call _format_known_memories directly and verify "user's name" is included
    formatted = await conv_api._format_known_memories()

    assert "user's name: Yochan" in formatted, \
        f"'user's name' missing from formatted output:\n{formatted}"

    # Verify all 11 memories appear (well under the 50 cap)
    for i in range(10):
        assert f"memory {i}: value {i}" in formatted, \
            f"'memory {i}' missing from formatted output"


# --- 2026-08-15b: routing/extraction call-count fix (architect review) -
# Two-LLM-call-per-turn was flagged as a real latency problem for an
# ordinary message. _route() now answers both needs_reasoning and
# memory_candidate_possible in one call; extract_candidates() must only
# fire when that flag says it's worth it.

async def test_memory_candidate_impossible_skips_extraction_call_entirely():
    await set_mission(title="Grow", statement="Statement.")
    extract_mock = AsyncMock(return_value=[])
    with patch("conversation.api.extract_candidates", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Sure thing.")):
        outcome = await handle("what should I work on today?")

    extract_mock.assert_not_called()
    assert outcome.pending_memories == []
    assert outcome.response_text == "Sure thing."


async def test_memory_candidate_possible_does_call_extraction():
    await set_mission(title="Grow", statement="Statement.")
    extract_mock = AsyncMock(return_value=[])
    with patch("conversation.api.extract_candidates", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Sure thing.")):
        await handle("call me Yochan")

    extract_mock.assert_called_once_with("call me Yochan", known_memories=[])


async def test_routing_failure_fails_open_to_both_true():
    # §3 in conversation/api.py: memory_candidate_possible fails OPEN
    # (True) on a malformed/unavailable routing call, unlike a false
    # positive there's no downstream recovery for a silently dropped
    # memory. needs_reasoning also stays fail-closed to True (pre-M3
    # behavior, unchanged).
    await set_mission(title="Grow", statement="Statement.")
    extract_mock = AsyncMock(return_value=[])
    with patch("conversation.api.extract_candidates", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(return_value=None)), \
         patch("conversation.api.complete", new=AsyncMock(return_value="ignored")):
        outcome = await handle("some ordinary message")

    extract_mock.assert_called_once()
    assert outcome.routed_to == "reasoning"


async def test_route_uses_exactly_one_llm_call_for_routing_decisions():
    await set_mission(title="Grow", statement="Statement.")
    routing_mock = AsyncMock(return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})
    with patch("conversation.api.complete_json", new=routing_mock), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Sure thing.")):
        await handle("hello there, general banter")

    # exactly one complete_json call for routing (no separate needs_reasoning
    # call anymore, and extraction's own complete_json is a different
    # module reference — see memory/extraction.py — so this only counts
    # conversation.api's own routing call).
    assert routing_mock.call_count == 1


# --- 2026-08-16: known-memory title/type reuse plumbing ---
# Regression coverage for a real live bug: "I like girls" then "I don't
# like girls" was stored as two separate, contradictory memories
# because extraction never saw the existing title. See
# memory/extraction.py's module docstring.

async def test_known_memories_are_passed_to_extraction():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(
        type=MemoryType.PREFERENCE, title="user's romantic attraction", value="likes girls",
        source_ids=["src-1"],
    )
    extract_mock = AsyncMock(return_value=[])
    with patch("conversation.api.extract_candidates", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Got it.")):
        await handle("I don't like girls")

    extract_mock.assert_called_once()
    _, kwargs = extract_mock.call_args
    assert kwargs["known_memories"] == [("user's romantic attraction", "likes girls", "Preference")]


async def test_known_memories_not_fetched_when_memory_candidate_impossible():
    # Consistent with the latency fix — no point paying for the DB read
    # either when the pre-filter already says extraction won't run.
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(
        type=MemoryType.PREFERENCE, title="user's romantic attraction", value="likes girls",
        source_ids=["src-1"],
    )
    extract_mock = AsyncMock(return_value=[])
    with patch("conversation.api.extract_candidates", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Sure.")):
        await handle("what should I have for lunch?")

    extract_mock.assert_not_called()


async def test_reused_title_and_type_from_extraction_correctly_updates_not_duplicates():
    # End-to-end proof that IF extraction does what the prompt asks
    # (reuses the exact title+type it was shown), remember_from_candidate's
    # existing title-keyed dedup correctly updates in place — this is
    # the actual fix confirmed working through the full pipeline, not
    # just the plumbing that gets extraction the context.
    await set_mission(title="Grow", statement="Statement.")
    original = await create_memory(
        type=MemoryType.PREFERENCE, title="user's romantic attraction", value="likes girls",
        source_ids=["src-1"],
    )
    correction = MemoryCandidate(
        type=MemoryType.PREFERENCE, title="user's romantic attraction", value="doesn't like girls",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="I don't like girls",
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[correction])), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Got it.")):
        await handle("I don't like girls")

    memories = await list_memories(status=MemoryStatus.ACTIVE)
    assert len(memories) == 1  # updated in place, not a second contradictory memory
    assert memories[0].id == original.id
    assert memories[0].value == "doesn't like girls"
    assert memories[0].version == 2


# --- 2026-08-17: routing must not send self-referential memory
# questions to Reasoning — see ARCHITECTURE_ISSUES.md's matching entry
# for the live bug ("what do you know about me" got a bare stat count
# from Reflection instead of the actual answer _CONVERSATIONAL_SYSTEM
# was already built to give). This can only assert the prompt now says
# the right thing — the model's actual classification isn't something a
# mocked unit test can verify; that needs real traffic (see the
# dogfooding transcript this fix came from).

def test_routing_prompt_explicitly_addresses_what_do_you_know_about_me():
    lowered = _ROUTING_SYSTEM.lower()
    assert "what do you know about me" in lowered
    assert "conversationally" in lowered


# --- regression: 2026-08-27 dogfooding, round 4b ------------------------
# "what are the status that i can set Test goal to?" — a real question
# about a specific, named goal — was classified needs_reasoning=False,
# almost certainly because it superficially resembles the prompt's own
# "what are your capabilities" (false) example. Same honest limitation
# as the test above: this can only assert the prompt now distinguishes
# the two cases, not that a given model reliably classifies correctly.

def test_routing_prompt_distinguishes_named_goal_questions_from_generic_capability_questions():
    lowered = _ROUTING_SYSTEM.lower()
    assert "what statuses can i move my test goal to" in lowered
    assert "generic" in lowered and "with no specific goal" in lowered


# --- 2026-08-17c: prefer the user's known name over a generic title ---
# Live dogfooding: Jarvis called the user "boss" while "user's name:
# Yochan" sat unused in the same prompt's memory section, until asked
# twice. Same honest limitation as the routing-prompt test above — this
# can only assert the prompt now says the right thing, not that the
# model reliably follows it.

def test_conversational_prompt_prefers_known_name_over_generic_title():
    lowered = _CONVERSATIONAL_SYSTEM.lower()
    assert "address them by it" in lowered
    assert "generic title" in lowered


# --- 2026-08-18: disambiguate Jarvis's own name from the user's name ---
# Defense-in-depth for the same live bug as the extraction test above —
# this covers ALREADY-CONTAMINATED data (a "jarvis's name" row that
# predates the extraction fix and won't retroactively disappear), which
# the extraction-side fix alone can't help with.

def test_conversational_prompt_disambiguates_jarvis_name_from_user_name():
    lowered = _CONVERSATIONAL_SYSTEM.lower()
    assert "jarvis's name" in lowered
    assert "source of truth" in lowered


# --- 2026-08-18b: source-of-truth priority, self-correction ---
# Live regression, worse than the 2026-08-18 one above: even AFTER the
# contaminated "jarvis's name" row was forgotten, Jarvis kept insisting
# "your name is Infinity" on the next turn, justified as "just like you
# mentioned a moment ago" — repeating and defending its OWN earlier
# wrong answer from session history instead of re-deriving from the
# (by then correct) memory section. The 2026-08-18 disambiguation fix
# only addressed confusing two DIFFERENT memory rows; it did nothing
# for the model trusting its own prior turn over ground truth. This is
# a stricter, consolidated rule replacing the narrower one — same
# honest limitation as every other prompt-only fix in this file: this
# can only prove the prompt says the right thing, not that a small,
# fast-tier model reliably follows it under real traffic.

def test_conversational_prompt_states_memory_as_source_of_truth_over_session_history():
    lowered = _CONVERSATIONAL_SYSTEM.lower()
    assert "source of truth" in lowered
    assert "memory section wins" in lowered
    assert "openly correct yourself" in lowered


# --- 2026-08-2X: grounded conversational synthesis replaces the rigid
# Planning/Reflection Decision template on the conversational path ---
# See ARCHITECTURE_ISSUES.md and this module's docstring for the full
# design rationale (user-requested: real conversation, not a fixed
# report format, for anything needing goal/mission/memory grounding).
# Same evidence-gathering and honesty guards reasoning/api.py always
# used — reused directly here, not reimplemented — just no longer
# forced into a two-sentence template, and no longer auto-firing a
# Plan/Action on every ordinary question.

async def test_grounded_reply_uses_the_llm_answer_when_it_passes_the_guards():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    good_answer = "You've got one project on record, Build jarvis v1 — let's break today into two steps."
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value=good_answer)):
        outcome = await handle("build me a plan for today")
    assert outcome.response_text == good_answer


async def test_grounded_reply_falls_back_when_guard_rejects_invented_relationship():
    # Same guard reasoning/api.py's structured Decision path always
    # used (_asserts_unsupported_relationship) — reused here unchanged,
    # just applied to a free-form answer instead of a template sentence.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    await create_goal(type=GoalType.LIFE_GOAL, title="Run a marathon")
    hallucinated = "Build jarvis v1 depends on finishing Run a marathon first."
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value=hallucinated)):
        outcome = await handle("how are my goals related?")
    assert "depends on" not in outcome.response_text
    assert "Build jarvis v1" in outcome.response_text  # plain-evidence fallback still names real goals


async def test_grounded_reply_never_creates_a_plan_or_action():
    # The explicit behavior change agreed on: ordinary conversation,
    # however grounded, no longer auto-fires goals.advance the way the
    # old rigid Planning Decision always did for every visible goal.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Here's a plan for today.")):
        await handle("what should i work on today?")
    actions = await list_actions()
    assert actions == []


async def test_grounded_reply_persists_a_decision_with_no_plan_required():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Here's a plan for today.")):
        await handle("what should i work on today?")
    decisions = await list_decisions()
    assert len(decisions) == 1
    assert decisions[0].summary == "Here's a plan for today."
    assert decisions[0].requires_plan is False


async def test_grounded_reply_intent_is_labeled_conversation_in_decision_history():
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="No goals yet — want to set one?")):
        await handle("what should i work on today?")
    pairs = await list_decisions_with_intent(limit=10)
    assert len(pairs) == 1
    assert pairs[0][0] == "Conversation"


async def test_grounded_reply_gets_recent_conversation_for_followups():
    # The actual motivating example: "lay out a plan, then let me say
    # 'change that to that' and have it work" — requires the grounded
    # path to see prior turns the same way the ordinary conversational
    # path already does.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    captured = {}

    async def fake_complete(system, prompt, task_type, max_tokens):
        captured["prompt"] = prompt
        return "Updated plan: focus on tests first."

    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})):
        with patch("conversation.api.complete", new=AsyncMock(return_value="Here's today's plan: step one, step two.")):
            await handle("build me a plan for today")
        with patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
            await handle("change step one to writing tests instead")

    assert "Here's today's plan" in captured["prompt"]


async def test_grounded_reply_falls_back_plainly_when_llm_unavailable():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    outcome = await handle("what should i work on today?")  # no mocks — real network unreachable in test env
    assert outcome.routed_to == "reasoning"
    assert "Build jarvis v1" in outcome.response_text
    assert "not sure is accurate" in outcome.response_text.lower()


# --- 2026-08-23b: plain-text terminal output — no Markdown, no truncation
# Live transcript: a grounded reply asking for "a schedule for today" came
# back with literal '###'/'**'/'---' Markdown syntax (this is a raw
# terminal, nothing renders it) AND cut off mid-sentence twice in the
# same session (max_tokens=500 was too small for genuine multi-step
# output). Two separate, unrelated bugs, both real, both fixed together
# since they showed up in the same transcript.

def test_grounded_prompt_forbids_markdown():
    lowered = _GROUNDED_SYSTEM.lower()
    assert "markdown" in lowered
    assert "plain text terminal" in lowered


def test_conversational_prompt_also_forbids_markdown():
    # Same rendering bug applies to the ordinary conversational path —
    # closed there too, not just where it was first observed. Lives in
    # _REAL_INTERFACE (injected into every conversational reply's
    # prompt as the "Interface:" line), not _CONVERSATIONAL_SYSTEM
    # itself — that's where the existing "no fake UI elements" guard
    # text already lived, so the no-Markdown rule was added alongside it.
    lowered = _REAL_INTERFACE.lower()
    assert "markdown" in lowered


async def test_grounded_reply_uses_a_generous_token_budget():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    complete_mock = AsyncMock(return_value="A full schedule for today.")
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=complete_mock):
        await handle("build me a schedule for today")

    _, kwargs = complete_mock.call_args
    # 500 was demonstrably too small — a real multi-step schedule got cut
    # off mid-sentence twice in one live session. Not pinning an exact
    # number here (that's an implementation detail that may reasonably
    # change again); asserting it's comfortably larger than the old value.
    assert kwargs["max_tokens"] >= 1000


async def test_grounded_reply_not_rejected_for_accurate_multi_goal_plan():
    # End-to-end reproduction of the live bug: one Active goal, two Draft
    # goals — exactly the shape that made every single real reply get
    # rejected before the 2026-08-23c guard fix (see ARCHITECTURE_ISSUES.md).
    # A realistic, accurate "plan my day" style answer mentioning all
    # three goals in separate sentences must now survive the guards.
    await set_mission(title="Be financially free", statement="Statement.")
    build = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(build.id, GoalStatus.ACTIVE, reason="test")
    await create_goal(type=GoalType.LIFE_GOAL, title="Run an ironman run")
    await create_goal(type=GoalType.LIFE_GOAL, title="Make a game")
    real_answer = (
        "Build Jarvis is your only active project right now, so I'd focus today's "
        "energy there. Your other goals, Run an ironman run and Make a game, are "
        "still in Draft, so no pressure on those yet."
    )
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value=real_answer)):
        outcome = await handle("what should i work on today?")

    assert outcome.response_text == real_answer  # NOT the plain-evidence fallback


# --- 2026-08-24b: deterministic Markdown strip, not just a prompt ask -
# Live dogfooding: the 2026-08-23b "don't use Markdown" instruction
# didn't hold — a real reply came back full of '**bold**' list items
# despite it. Third time this project has found a prompt-only
# instruction unreliable for something mechanical (extraction's
# "explicit only" rule, the name-recall grounding rule, now this) — a
# deterministic pass is the actual fix, the prompt instruction stays as
# a first line of defense, not the only one.

def test_strip_markdown_removes_bold():
    assert _strip_markdown_formatting("**Set a start time** – do the thing.") == "Set a start time – do the thing."


def test_strip_markdown_removes_headers():
    assert _strip_markdown_formatting("### Today's Plan\nDo the thing.") == "Today's Plan\nDo the thing."


def test_strip_markdown_removes_horizontal_rules():
    result = _strip_markdown_formatting("Step one.\n---\nStep two.")
    assert "---" not in result
    assert "Step one." in result and "Step two." in result


def test_strip_markdown_leaves_plain_numbered_lists_alone():
    plain = "1. Wake up early.\n2. Study topic A.\n3. Take a break."
    assert _strip_markdown_formatting(plain) == plain


def test_strip_markdown_does_not_touch_single_asterisks():
    # Deliberately conservative — legitimate uses (multiplication, plain
    # emphasis) are too ambiguous to strip without a real parser.
    text = "5 * 3 = 15, and that's *really* worth noting."
    assert _strip_markdown_formatting(text) == text


async def test_conversational_reply_strips_markdown_from_llm_output():
    await set_mission(title="Grow", statement="Statement.")
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="**Hey there!** Let's go.")):
        outcome = await handle("hi")
    assert "**" not in outcome.response_text
    assert "Hey there!" in outcome.response_text


async def test_grounded_reply_strips_markdown_from_llm_output():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")
    markdown_answer = "1. **Set a start time** – pick something realistic.\n2. **Study topic A** – 45 minutes."
    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(return_value=markdown_answer)):
        outcome = await handle("plan my exam study day")
    assert "**" not in outcome.response_text
    assert "Set a start time" in outcome.response_text


# --- V1-M2: natural-language Goal/Mission state changes -------------------
# Integration tests against a real DB, same shape as the memory tests
# above. state_change.extraction.extract_candidate is patched directly
# (its own module-level LLM call), exactly like extract_candidates is
# patched for the memory pipeline — this exercises the real policy
# layer, the real goals.api/mission.api writes, and handle()'s wiring,
# without depending on any real model.

async def test_create_goal_command_executes_immediately():
    await set_mission(title="Grow", statement="Statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="create a goal to run a marathon",
        title="Run a marathon", type=GoalType.LIFE_GOAL,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Done — added that goal.")):
        outcome = await handle("create a goal to run a marathon")

    assert outcome.pending_state_changes == []
    goals = await list_goals()
    assert len(goals) == 1
    assert goals[0].title == "Run a marathon"
    assert goals[0].status == GoalStatus.DRAFT


async def test_unambiguous_status_change_executes_immediately():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="pause my build jarvis goal",
        goal_ref="Build Jarvis", new_status=GoalStatus.PAUSED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.ACTIVE, match_count=1,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Paused it.")):
        outcome = await handle("pause my build jarvis goal")

    assert outcome.pending_state_changes == []
    updated = await list_goals()
    assert updated[0].status == GoalStatus.PAUSED


async def test_cancel_command_asks_confirmation_and_does_not_write_until_approved():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="cancel my build jarvis goal",
        goal_ref="Build Jarvis", new_status=GoalStatus.CANCELLED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.ACTIVE, match_count=1,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Want me to go ahead?")):
        outcome = await handle("cancel my build jarvis goal")

    assert len(outcome.pending_state_changes) == 1
    still_active = await list_goals()
    assert still_active[0].status == GoalStatus.ACTIVE  # not written yet

    status = await resolve_pending_state_change(outcome.pending_state_changes[0], approved=True)
    assert status is not None
    cancelled = await list_goals()
    assert cancelled[0].status == GoalStatus.CANCELLED


async def test_cancel_command_declined_leaves_goal_unchanged():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="cancel my build jarvis goal",
        goal_ref="Build Jarvis", new_status=GoalStatus.CANCELLED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.ACTIVE, match_count=1,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Want me to go ahead?")):
        outcome = await handle("cancel my build jarvis goal")

    status = await resolve_pending_state_change(outcome.pending_state_changes[0], approved=False)
    assert status is None
    unchanged = await list_goals()
    assert unchanged[0].status == GoalStatus.ACTIVE


# --- regression: 2026-08-27 dogfooding, round 2 -------------------------
# Live session: "delete the Test goal" -> policy correctly resolved to
# ASK_CONFIRMATION (Cancelled) -> the CLI's y/N prompt correctly showed
# and correctly wrote the change to the DB -> and then, on the SAME
# turn, the printed assistant reply said "I can't delete goals right
# now... I don't have that capability" — directly beneath cli.py's own
# "-> Updated 'Test' to Cancelled." line from the write that had just
# succeeded. Same shape repeated for a Mission change one turn later
# ("I can't create or modify missions right now" under "-> Mission
# updated to 'Test mission' (v2)."). Root cause: ASK_CONFIRMATION gave
# the reply model NO grounding at all (`return None, None, [pending]`),
# so it improvised, and cli.py resolves+prints the confirmation before
# printing the already-generated reply text. Fixed by giving
# ASK_CONFIRMATION a relay_note too, with wording that rules out both a
# false "can't do it" AND a false "already done" claim.

async def test_cancel_confirmation_relay_note_neither_denies_capability_nor_claims_completion():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Test")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="delete the Test goal",
        goal_ref="Test", new_status=GoalStatus.CANCELLED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.ACTIVE, match_count=1,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "That'll cancel it permanently, so I want to double-check with you first."

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        outcome = await handle("delete the Test goal")

    # The y/N confirmation must still be there — this fix must not
    # remove the actual confirmation gate, only give the reply the
    # context to not contradict it.
    assert len(outcome.pending_state_changes) == 1
    still_active = await list_goals()
    assert still_active[0].status == GoalStatus.ACTIVE  # not written yet, correctly

    prompt = captured["prompt"].lower()
    assert "can't do this" in prompt or "isn't finished yet" in prompt  # the instruction, not a denial
    assert "don't tell the user you can't do this" in prompt
    assert "don't say or imply that it is" in prompt
    assert "cancelled" in prompt  # the actual pending change is named


async def test_mission_change_confirmation_relay_note_neither_denies_capability_nor_claims_completion():
    await set_mission(title="Grow", statement="Original statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_MISSION, raw_user_text="change my mission to Test mission: test",
        mission_title="Test mission", mission_statement="test",
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "That's a big change, so let me confirm with you first."

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        outcome = await handle("change my mission to Test mission: test")

    assert len(outcome.pending_state_changes) == 1
    still_old = await get_active_mission()
    assert still_old.title == "Grow"  # not written yet, correctly

    prompt = captured["prompt"].lower()
    assert "don't tell the user you can't do this" in prompt
    assert "don't say or imply that it is" in prompt
    assert "test mission" in prompt


async def test_execute_framing_instructs_a_just_done_action_not_a_preexisting_fact():
    # Live session: "can you make the exam overloaded goal active" was
    # correctly EXECUTEd (Draft -> Active) but the reply said "That
    # ... goal is already active ... so we are all set there" — implying
    # nothing changed, when Jarvis had just changed it. The label
    # wasn't false, just weak enough that a small model read a real
    # change as a status report. This only asserts the prompt's
    # instruction was strengthened, not that a given model obeys it.
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="exam overloaded")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="make the exam overloaded goal active",
        goal_ref="exam overloaded", new_status=GoalStatus.ACTIVE,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.DRAFT, match_count=1,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "Done — I've made exam overloaded active."

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("make the exam overloaded goal active")

    prompt = captured["prompt"].lower()
    assert "never as a pre-existing fact" in prompt
    assert "updated \"exam overloaded\" to active" in prompt


async def test_mission_change_asks_confirmation_and_writes_new_version_on_approval():
    await set_mission(title="Grow", statement="Original statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_MISSION, raw_user_text="change my mission to Ship: Ship real things.",
        mission_title="Ship", mission_statement="Ship real things.",
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Confirm the mission change?")):
        outcome = await handle("change my mission to Ship: Ship real things.")

    assert len(outcome.pending_state_changes) == 1
    still_old = await get_active_mission()
    assert still_old.title == "Grow"

    await resolve_pending_state_change(outcome.pending_state_changes[0], approved=True)
    new_mission = await get_active_mission()
    assert new_mission.title == "Ship"
    assert new_mission.version == 2


async def test_ambiguous_goal_reference_asks_clarify_and_writes_nothing():
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build Jarvis")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="pause my goal",
        goal_ref="my goal", new_status=GoalStatus.PAUSED,
        resolved_goal_id=None, resolved_goal_status=None, match_count=0,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Which goal did you mean?")):
        outcome = await handle("pause my goal")

    assert outcome.pending_state_changes == []  # ASK_CLARIFY is not a y/N pending — see conversation.api
    goals = await list_goals()
    assert goals[0].status == GoalStatus.DRAFT  # unchanged


async def test_invalid_transition_rejected_without_writing():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")
    await set_status(goal.id, GoalStatus.COMPLETED, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="pause build jarvis",
        goal_ref="Build Jarvis", new_status=GoalStatus.PAUSED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.COMPLETED, match_count=1,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Can't do that.")):
        outcome = await handle("pause build jarvis")

    assert outcome.pending_state_changes == []
    unchanged = await list_goals()
    assert unchanged[0].status == GoalStatus.COMPLETED


async def test_state_change_grounding_reaches_the_prompt():
    await set_mission(title="Grow", statement="Statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="create a goal to run a marathon",
        title="Run a marathon", type=GoalType.LIFE_GOAL,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "Done."

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("create a goal to run a marathon")

    assert "Run a marathon" in captured["prompt"]


async def test_state_change_and_memory_can_both_fire_from_one_message():
    # The two pipelines are independent — a single routing call flags
    # both, and both policies get to act, same as needs_reasoning +
    # memory_candidate_possible already coexist.
    await set_mission(title="Grow", statement="Statement.")
    memory_candidate = MemoryCandidate(
        type=MemoryType.FACT, title="user's name", value="Yochan",
        scope=MemoryScope.DURABLE, explicit=True, raw_user_text="I'm Yochan, create a goal to run a marathon",
    )
    state_candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="I'm Yochan, create a goal to run a marathon",
        title="Run a marathon", type=GoalType.LIFE_GOAL,
    )
    with patch("conversation.api.extract_candidates", new=AsyncMock(return_value=[memory_candidate])), \
         patch("conversation.api.extract_state_change", new=AsyncMock(return_value=state_candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": True, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Got it, and done.")):
        await handle("I'm Yochan, create a goal to run a marathon")

    memories = await list_memories(status=MemoryStatus.ACTIVE)
    goals = await list_goals()
    assert len(memories) == 1
    assert len(goals) == 1


# --- regression: 2026-08-26 dogfooding bug ---------------------------------
# "add a new goal named exam overloaded" (no type given) -> extraction
# correctly returns type=None -> policy correctly returns ASK_CLARIFY ->
# but the clarify question was being merged into the "Memory action just
# taken this turn (you may state this happened)" block, so the reply
# model was told a pending QUESTION was a completed FACT. The observed
# symptom: the assistant claimed it had no capability to add goals at
# all, instead of asking what type of goal was meant, and nothing was
# ever written. Fixed by splitting _apply_state_change_policy's return
# into executed_note (fact) vs relay_note (question/explanation), each
# with its own labeled block in the reply prompt.

async def test_create_goal_without_type_relays_the_clarifying_question_not_a_fact_claim():
    await set_mission(title="Grow", statement="Statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="could you add a new goal named exam overloaded",
        title="exam overloaded", type=None,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "What kind of goal is exam overloaded — a Task, Habit, Project, or LifeGoal?"

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        outcome = await handle("could you add a new goal named exam overloaded")

    # Nothing written, no pending y/N — a relay question, not a fact.
    assert outcome.pending_state_changes == []
    goals = await list_goals()
    assert goals == []

    # The clarifying question reached the prompt in its own, correctly
    # labeled block — never merged into an "Action just taken this turn"
    # framing, which would tell the model something happened that didn't.
    prompt = captured["prompt"]
    assert "LifeGoal, Project, Task, or Habit" in prompt
    assert "Action just taken this turn" not in prompt


async def test_invalid_transition_relays_explanation_not_a_fact_claim():
    await set_mission(title="Grow", statement="Statement.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="test setup")
    await set_status(goal.id, GoalStatus.COMPLETED, reason="test setup")

    candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="pause build jarvis",
        goal_ref="Build Jarvis", new_status=GoalStatus.PAUSED,
        resolved_goal_id=goal.id, resolved_goal_status=GoalStatus.COMPLETED, match_count=1,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "That goal's already completed, so it can't be paused."

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("pause build jarvis")

    prompt = captured["prompt"]
    assert "can't move that goal from completed to paused" in prompt.lower()
    assert "Action just taken this turn" not in prompt


# --- regression: 2026-08-27 dogfooding, turn 2 --------------------------
# "could you add one more goal named exam overloaded" (no type) correctly
# produced a relay_note asking for the type — but the relay_note block's
# own wording ("Jarvis can't complete yet") combined with
# _CONVERSATIONAL_SYSTEM's default "if you can't do something, say so
# plainly" instruction to make the model prepend a false "I can't do
# that" disclaimer BEFORE asking the clarifying question it was told to
# ask. Fixed by rewording the relay block to frame this as an in-
# progress step, not a capability denial.

async def test_relay_note_framing_does_not_instruct_a_capability_denial():
    # This can only assert the PROMPT we send says the right thing, not
    # that a given real model reliably follows it (same honest
    # limitation test_routing_prompt_explicitly_addresses_what_do_you_know_about_me
    # already documents for a different prompt) — the actual dogfooding
    # transcript is what proved the wording change was needed.
    await set_mission(title="Grow", statement="Statement.")
    candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="could you add one more goal named exam overloaded",
        title="exam overloaded", type=None,
    )
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "What kind of goal is exam overloaded?"

    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("could you add one more goal named exam overloaded")

    prompt = captured["prompt"].lower()
    # The old, buggy wording flatly asserted incapacity ("jarvis can't
    # complete yet") with no instruction against saying so. The fix adds
    # an explicit instruction not to claim incapacity — which legitimately
    # contains the word "can't" inside that instruction itself, so check
    # for the specific bad phrase and for the corrective framing, not a
    # blind substring match.
    assert "jarvis can't complete yet" not in prompt
    assert "don't tell the user you can't do this" in prompt
    assert "in progress" in prompt or "actively handling" in prompt


# --- regression: 2026-08-27 dogfooding, turn 3 --------------------------
# The user answered Jarvis's own clarifying question with a bare "a
# Project" — no goal title, no command language at all. Two real bugs
# stacked here: (1) _route() classified purely from user_text, so a
# bare "a Project" always came back state_change_possible=False and
# extract_state_change never even ran; (2) even if it had run,
# extraction itself had no conversation history to reconstruct the
# original "exam overloaded" title from. Both are now fixed by wiring
# the same recent-conversation text into _route()'s prompt and
# extract_state_change's context. This test verifies the WIRING (both
# call sites receive the real prior exchange) end-to-end across two
# real handle() calls with real archiving — it does not and cannot
# verify that a given live LLM reliably reconstructs the command from
# that context; that's a real-traffic question, not a unit-test one.

async def test_recent_conversation_reaches_routing_and_extraction_for_a_followup_answer():
    await set_mission(title="Grow", statement="Statement.")

    # Turn 1: ask to create a goal with no type -> real ASK_CLARIFY,
    # really archived, exactly like the live session.
    turn1_candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="could you add one more goal named exam overloaded",
        title="exam overloaded", type=None,
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=turn1_candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(
             return_value="What kind of goal would exam overloaded be — a Task, Habit, Project, or LifeGoal?")):
        await handle("could you add one more goal named exam overloaded")
    assert (await list_goals()) == []  # nothing written yet, as expected

    # Turn 2: bare "a Project" — capture exactly what routing and
    # extraction were each given.
    routing_calls = []

    async def fake_route_complete_json(system, prompt, **kwargs):
        if system == _ROUTING_SYSTEM:
            routing_calls.append(prompt)
            return {"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True}
        return None

    turn2_candidate = StateChangeCandidate(
        operation=StateChangeOperation.CREATE_GOAL, raw_user_text="a Project",
        title="exam overloaded", type=GoalType.PROJECT,  # what a correctly-reconstructing model would return
    )
    extract_mock = AsyncMock(return_value=turn2_candidate)
    with patch("conversation.api.extract_state_change", new=extract_mock), \
         patch("conversation.api.complete_json", new=AsyncMock(side_effect=fake_route_complete_json)), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Done — added it as a Project.")):
        await handle("a Project")

    # Routing saw the prior exchange, not just the bare "a Project".
    assert routing_calls, "routing (complete_json) was never called"
    assert "exam overloaded" in routing_calls[0]
    assert "what kind of goal" in routing_calls[0].lower()

    # Extraction was also given the same recent-conversation text.
    extract_mock.assert_called_once()
    _, ex_kwargs = extract_mock.call_args
    assert "exam overloaded" in ex_kwargs["recent_context"]

    # And when extraction DOES correctly reconstruct the command (as
    # mocked here), the write actually happens.
    goals = await list_goals()
    assert len(goals) == 1
    assert goals[0].title == "exam overloaded"
    assert goals[0].type == GoalType.PROJECT


# --- regression: 2026-08-27 dogfooding, round 4 (end-to-end) -----------
# Same bug as reasoning/api.py's test_gather_grounded_evidence_finds_
# goals_after_mission_is_superseded, exercised through the actual layer
# the user hit it through: a real M2 mission-change confirmation,
# approved, followed by a real reasoning-routed "list my goals" call —
# both via handle(), nothing mocked except the LLM calls themselves.

async def test_goals_still_visible_to_reasoning_after_a_confirmed_mission_change():
    await set_mission(title="Grow", statement="Original.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="setup")

    # Turn 1: a real M2 mission-change command, confirmed — exactly the
    # live flow (state_change/policy.py's ASK_CONFIRMATION path).
    mission_candidate = StateChangeCandidate(
        operation=StateChangeOperation.SET_MISSION, raw_user_text="change my mission to New Mission: different",
        mission_title="New Mission", mission_statement="different",
    )
    with patch("conversation.api.extract_state_change", new=AsyncMock(return_value=mission_candidate)), \
         patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": False, "memory_candidate_possible": False, "state_change_possible": True})), \
         patch("conversation.api.complete", new=AsyncMock(return_value="Want me to go ahead?")):
        outcome1 = await handle("change my mission to New Mission: different")
    await resolve_pending_state_change(outcome1.pending_state_changes[0], approved=True)

    mission = await get_active_mission()
    assert mission.title == "New Mission"
    assert mission.version == 2

    # Turn 2: a real reasoning-routed question — real gather_grounded_
    # evidence call, only complete()/complete_json() mocked.
    captured = {}

    async def fake_complete(prompt, system="", **kwargs):
        captured["prompt"] = prompt
        return "You have one active goal: Build Jarvis."

    with patch("conversation.api.complete_json", new=AsyncMock(
             return_value={"needs_reasoning": True, "memory_candidate_possible": False, "state_change_possible": False})), \
         patch("conversation.api.complete", new=AsyncMock(side_effect=fake_complete)):
        await handle("list my goals")

    assert "Build Jarvis" in captured["prompt"]
    assert '"goals": []' not in captured["prompt"]
