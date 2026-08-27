#!/usr/bin/env python3
"""
Minimal terminal CLI for Jarvis (architect decision, 2026-08-04, updated
2026-08-08 for V1-M1): "Just enough to talk to Jarvis from the
terminal." No GUI, no web frontend. Explicit slash-commands for system
management (/mission, /goal, /memory, /help, /status, /decision);
everything else goes through the Conversation layer
(conversation.api.handle), which decides whether it needs the full
Reasoning pipeline or can be answered as ordinary conversation — see
V1_M1_IMPLEMENTATION_PLAN.md. Limited natural-language CRUD for
Goals/Mission (create, status changes, mission changes) landed in
V1-M2 — see ARCHITECTURE_ISSUES.md's 2026-08-25 entry — routed through
the same conversation.api.handle() call, not a separate command.
"""
from __future__ import annotations

import asyncio
import difflib
import re
import sys

import capabilities.bootstrap  # noqa: F401 — registers all primitive capabilities
import conversation.api
from config.loader import load_config
from contracts.enums import GoalStatus, GoalType, Priority
from goals.api import create_goal, list_goals, set_status as set_goal_status
from infra.migrate import apply_migrations
from infra.storage import close_pool, init_pool
from memory.api import forget_memory, list_memories, list_taxonomy_gaps
from mission.api import get_active_mission, set_mission
from reasoning.api import list_decisions, list_decisions_with_intent


async def _bootstrap():
    cfg = load_config()
    pool = await init_pool(cfg)
    await apply_migrations(pool)
    return cfg


def _print_help():
    print(
        "\nCommands:\n"
        "  /mission                  show the active Mission\n"
        "  /mission set <title> | <statement>   create/update the Mission\n"
        "  /goal create <type> <title>          type: LifeGoal|Project|Task|Habit\n"
        "  /goal list                           list all goals\n"
        "  /goal status <id> <status>           Draft|Active|Paused|Completed|Archived|Cancelled\n"
        "  /memory list                         list active memories\n"
        "  /memory forget <id>                  forget a memory (short ID from /memory list)\n"
        "  /memory gaps                         statements that didn't fit any memory type\n"
        "  /status                              quick system snapshot\n"
        "  /decision [n]                        list last 10 decisions, or show detail for #n\n"
        "  /help                                this message\n"
        "  /quit, /exit                         leave\n"
        "  <anything else>                      goes through Jarvis's reasoning pipeline — plain "
        "statements like \"my name is X\" or \"I prefer Y\" are remembered automatically\n"
    )


async def _cmd_mission(args: list[str]):
    if not args:
        m = await get_active_mission()
        print(f"\n{m.title}\n{m.statement}\n(v{m.version})" if m else "\nNo active Mission yet. Try: /mission set <title> | <statement>")
        return
    if args[0] == "set":
        rest = " ".join(args[1:])
        if "|" not in rest:
            print("Usage: /mission set <title> | <statement>")
            return
        title, statement = [p.strip() for p in rest.split("|", 1)]
        m = await set_mission(title=title, statement=statement)
        print(f"\nMission set (v{m.version}): {m.title}")


async def _cmd_goal(args: list[str]):
    if not args:
        print("Usage: /goal create|list|status ...")
        return
    if args[0] == "create" and len(args) >= 3:
        try:
            gtype = GoalType(args[1])
        except ValueError:
            print(f"Unknown type '{args[1]}'. Use one of: LifeGoal, Project, Task, Habit")
            return
        title = " ".join(args[2:])
        try:
            g = await create_goal(type=gtype, title=title)
        except Exception as e:
            print(f"Could not create goal: {e}")
            return
        print(f"\nCreated {g.type.value} '{g.title}' ({_cache_id(g.id)}), status={g.status.value}")
    elif args[0] == "list":
        goals = await list_goals()
        if not goals:
            print("\nNo goals yet.")
        for g in goals:
            print(f"  [{_cache_id(g.id)}] {g.type.value:10} {g.status.value:10} {g.title}")
    elif args[0] == "status":
        if len(args) < 3:
            print("Usage: /goal status <id> <status>")
            return
        raw_id = args[1]
        # A goal name (quoted or not) produces more than 3 tokens, or a
        # first token that isn't ID-shaped. Either way, goal names aren't
        # supported yet (short-IDs only) — say so plainly instead of
        # letting a stray word get parsed as the status and producing a
        # confusing "Unknown status" error.
        if len(args) > 3 or not _looks_like_id(raw_id):
            print(
                f"Goal names aren't supported for /goal status yet — use the "
                f"short ID shown by /goal list (e.g. 693e8048), not '{raw_id}'."
            )
            return
        new_status = _parse_status(args[2])
        if new_status is None:
            valid = ", ".join(s.value for s in GoalStatus)
            suggestion = _closest_status(args[2])
            hint = f" Did you mean '{suggestion}'?" if suggestion else ""
            print(f"Unknown status '{args[2]}'.{hint} Valid: {valid}")
            return
        goal_id = await _resolve_short_id(raw_id)
        try:
            g = await set_goal_status(goal_id, new_status, reason="via CLI")
            print(f"\n{g.title}: -> {g.status.value}")
        except Exception as e:
            print(f"Could not update: {e}")
    else:
        print("Usage: /goal create <type> <title> | /goal list | /goal status <id> <status>")


# P4 fix (Jarvis_V0_Testing_Review, 2026-08-05): status parsing was
# exact-case and errors on bad input gave no guidance.
_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,36}$")


def _looks_like_id(s: str) -> bool:
    return bool(_ID_RE.match(s))


def _parse_status(raw: str) -> GoalStatus | None:
    """Case-insensitive status lookup — 'completed', 'Completed', and
    'COMPLETED' all valid. Nobody decided exact-case was required; it
    was just an unhandled gap."""
    lowered = raw.strip().lower()
    for s in GoalStatus:
        if s.value.lower() == lowered:
            return s
    return None


def _closest_status(raw: str) -> str | None:
    candidates = [s.value for s in GoalStatus]
    match = difflib.get_close_matches(raw.strip().lower(), [c.lower() for c in candidates], n=1)
    if not match:
        return None
    return next(c for c in candidates if c.lower() == match[0])


# BUG (found in dogfooding, 2026-08-05): this cache existed and
# _resolve_short_id() read from it, but nothing ever wrote to it — every
# short ID shown by `/goal list` etc. was unresolvable, so `/goal status
# <short-id> ...` always failed UUID parsing at the DB layer. Fix: every
# call site that prints a truncated ID now goes through _cache_id()
# instead of slicing directly, so the mapping actually exists by the
# time a command tries to resolve it.
_ID_CACHE: dict[str, str] = {}


def _cache_id(full_id: str) -> str:
    short = full_id[:8]
    _ID_CACHE[short] = full_id
    return short


async def _resolve_short_id(short: str) -> str:
    if short in _ID_CACHE:
        return _ID_CACHE[short]
    if len(short) < 32:
        # Not cached yet — e.g. a fresh session where /goal status ran
        # before any /goal list populated the cache. Found in testing
        # 2026-08-06: this used to fall through to `short` unchanged and
        # leak a raw asyncpg UUID-parse error at the DB layer. Live
        # prefix lookup instead, same idea as the cache, just not
        # session-dependent.
        for g in await list_goals():
            if g.id.startswith(short):
                _cache_id(g.id)  # populate the cache for future lookups
                return g.id      # BUG (found via direct DB reproduction, 2026-08-06):
                                 # _cache_id() returns the SHORT form (that's its
                                 # display purpose) — `return _cache_id(g.id)` was
                                 # handing the unresolved short id right back,
                                 # silently defeating the whole fallback.
        # V1-M3: /memory forget hits this same cold-cache path — the
        # cache is a single global dict keyed by short prefix across
        # every entity type, but the live fallback above only ever
        # checked Goals. Extended rather than duplicated, same bug shape
        # the 2026-08-06 fix already covered for Goals.
        for m in await list_memories():
            if m.id.startswith(short):
                _cache_id(m.id)
                return m.id
    return short


async def _cmd_memory(args: list[str]):
    # V1-M3: most memory writes now happen implicitly via ordinary
    # conversation (conversation.api._apply_memory_policy) — this
    # command stays read/forget only, same as it was for M2's rule-based
    # memories. No /memory remember here; that would be a second write
    # path outside memory/api.py's remember_from_candidate/remember_fact.
    if args and args[0] == "forget":
        if len(args) != 2 or not _looks_like_id(args[1]):
            print("Usage: /memory forget <id> — use the short ID shown by /memory list.")
            return
        memory_id = await _resolve_short_id(args[1])
        try:
            m = await forget_memory(memory_id, reason="via CLI")
            print(f"\nForgot: {m.title}")
        except Exception as e:
            print(f"Could not forget: {e}")
        return

    if args and args[0] == "gaps":
        # V1-M3 follow-up (2026-08-15b): surfaces the durable trail from
        # infra/migrations/0017_taxonomy_gap_proposal.sql — read-only,
        # same as the rest of this command. Promoting one of these to a
        # real MemoryType is still an architect/human call, not
        # something this command does.
        gaps = await list_taxonomy_gaps()
        if not gaps:
            print("\nNo taxonomy gaps recorded.")
            return
        print(f"\n{len(gaps)} memory-worthy statement(s) that didn't fit the current taxonomy:")
        for g in gaps:
            print(f"  [{g.created_at:%Y-%m-%d}] {g.title}: {g.value}")
        return

    memories = await list_memories()
    if not memories:
        print("\nNo memories yet.")
    for m in memories:
        print(f"  [{_cache_id(m.id)}] {m.type.value:12} {m.status.value:10} {m.title}: {m.value}")


async def _cmd_decision(args: list[str]):
    pairs = await list_decisions_with_intent(limit=10)
    if not pairs:
        print("\nNo decisions yet.")
        return
    if not args:
        for n, (intent, d) in enumerate(pairs, start=1):
            snippet = d.summary if len(d.summary) <= 70 else d.summary[:67] + "..."
            print(f"  #{n} [{intent:10}] {snippet}")
        print("\n(/decision <#> for detail)")
        return
    try:
        idx = int(args[0])
        intent, d = pairs[idx - 1]
    except (ValueError, IndexError):
        print(f"No decision #{args[0]}. /decision lists the last {len(pairs)}.")
        return
    print(
        f"\nDecision #{idx}\n"
        f"Intent: {intent}\n"
        f"User Input: {d.objective}\n"
        f"Decision: {d.summary}\n"
        f"Confidence: {d.confidence:.2f}   Mission alignment: {d.mission_alignment:.2f}\n"
        f"Reasoning: {d.reasoning}\n"
        f"Evidence: {len(d.context_item_ids)} context item(s), {len(d.goal_ids)} goal(s) referenced\n"
        f"Created: {d.created_at.isoformat()}"
    )


async def _cmd_status():
    mission = await get_active_mission()
    goals = await list_goals()
    memories = await list_memories()
    decisions = await list_decisions()
    print(
        f"\nMission: {mission.title if mission else '(none)'}\n"
        f"Goals: {len(goals)} ({sum(1 for g in goals if g.status.value == 'Active')} active)\n"
        f"Memories: {len(memories)}\n"
        f"Decisions made: {len(decisions)}"
    )


async def _handle_request(text: str):
    conv_outcome = await conversation.api.handle(text)

    # 2026-08-2X: the reasoning-routed path is now a real, free-form
    # grounded answer (conversation.api._grounded_reply) — see
    # ARCHITECTURE_ISSUES.md. It no longer carries Plan/Action/
    # PermissionCheckResult data (ordinary conversation, however
    # grounded, never auto-fires either anymore), so both routing
    # outcomes render identically here now. The old Plan/Action
    # rendering + confirmation loop that used to live in this function
    # is gone because it could never execute — orchestrator.api.
    # run_request()/resume_action() are still real and tested, just no
    # longer reachable from this CLI's normal flow.
    #
    # 2026-08-27: printed BEFORE the confirmation loops below (was
    # after) — found in dogfooding that with the old order, a y/N
    # prompt could resolve and print "-> Updated ... to Cancelled."
    # several lines above a reply that was generated (and worded)
    # before that resolution ever happened, reading as a direct
    # contradiction of what had just occurred. Printing the reply first
    # makes the turn read as one coherent exchange: Jarvis responds,
    # then (if needed) asks for the y/N.
    print(f"\n{conv_outcome.response_text}")

    # V1-M3: ambiguous-scope memory candidates ask before writing —
    # same y/N shape as the (now-removed) action-confirmation loop that
    # used to sit here, deliberately not routed through it (see
    # conversation.api.PendingMemoryConfirmation). A single message can
    # surface several (2026-08-15 revision), so each gets its own
    # prompt in turn rather than one bundled y/N.
    for pm in conv_outcome.pending_memories:
        answer = input(f"  ? remember this — {pm.reason}? [y/N] ")
        status = await conversation.api.resolve_pending_memory(pm, approved=answer.strip().lower() == "y")
        if status:
            print(f"    -> {status}")

    # V1-M2: same y/N shape, same reasoning, for high-impact Goal/Mission
    # state changes (see conversation.api.PendingStateChangeConfirmation).
    for pc in conv_outcome.pending_state_changes:
        answer = input(f"  ? {pc.reason} [y/N] ")
        status = await conversation.api.resolve_pending_state_change(pc, approved=answer.strip().lower() == "y")
        if status:
            print(f"    -> {status}")


async def main():
    await _bootstrap()
    print("Jarvis V0 — terminal. /help for commands, /quit to leave.")

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line == "/help":
            _print_help()
        elif line.startswith("/mission"):
            await _cmd_mission(line.split()[1:])
        elif line.startswith("/goal"):
            await _cmd_goal(line.split()[1:])
        elif line.startswith("/memory"):
            await _cmd_memory(line.split()[1:])
        elif line == "/status":
            await _cmd_status()
        elif line.startswith("/decision"):
            await _cmd_decision(line.split()[1:])
        elif line.startswith("/"):
            print(f"Unknown command '{line}'. /help for the list.")
        else:
            try:
                await _handle_request(line)
            except Exception as e:
                print(f"Error: {e}")

    await close_pool()
    print("Goodbye.")


if __name__ == "__main__":
    if "--migrate-only" in sys.argv:
        async def _migrate_only():
            await _bootstrap()
            await close_pool()
        asyncio.run(_migrate_only())
    else:
        asyncio.run(main())
