"""
Insight API (§7; §9 M7). Decision #3: one engine, two output modes
(reflection=internal/system-facing, review=user-facing narrative), one
shared evidence pipeline, one shared archive (InsightRecord, §6.12).
Weekly scope only (§5.1) — `days` defaults to 7, InsightScope is
literally restricted to WEEKLY at the contract level (contracts/insight_record.py).

Narrative generation is template-based, same scoping as reasoning/api.py's
Decision generation and orchestrator/intent.py's classifier (no LLM
wired yet — see ARCHITECTURE_ISSUES.md's M3 entry, which this extends
rather than repeats).

RE-06/RV-04 Evidence First: every claim cites real IDs. RE-10/REV-01
Radical Honesty without shaming: problems are stated as facts about
activity, never about the user. RV-05 wants both wins and problems in
every review — but fabricating a problem with no evidence to cite would
violate evidence-first worse than an empty list would violate RV-05, so
`problems` is genuinely empty when nothing negative is found.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from action_engine.api import list_actions
from contracts.enums import ActionStatus, GoalStatus, InsightMode, InsightScope
from contracts.insight_record import EvidenceClaim, InsightRecord
from goals.api import list_goals, list_recent_goal_history
from infra.llm_router import complete_json
from infra.logging import get_logger
from infra.storage import connection
from memory.api import list_memories
from mission.api import get_active_mission
from reasoning.api import list_decisions

log = get_logger("insight.api")


def _row_to_insight(row) -> InsightRecord:
    return InsightRecord(
        id=str(row["id"]), mode=InsightMode(row["mode"]), scope=InsightScope(row["scope"]),
        narrative=row["narrative"],
        evidence=[EvidenceClaim(**e) for e in json.loads(row["evidence"])],
        wins=json.loads(row["wins"]), problems=json.loads(row["problems"]),
        user_response=row["user_response"], created_at=row["created_at"],
    )


async def get_insight(insight_id: str) -> Optional[InsightRecord]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM insight_record WHERE id = $1;", insight_id)
        return _row_to_insight(row) if row else None


async def list_insights(mode: Optional[InsightMode] = None) -> list[InsightRecord]:
    if mode is not None:
        query, params = "SELECT * FROM insight_record WHERE mode = $1 ORDER BY created_at DESC;", [mode.value]
    else:
        query, params = "SELECT * FROM insight_record ORDER BY created_at DESC;", []
    async with connection() as conn:
        rows = await conn.fetch(query, *params)
        return [_row_to_insight(r) for r in rows]


async def _gather_evidence(since: datetime) -> dict:
    goals = await list_goals()
    history = await list_recent_goal_history(since)
    memories = [m for m in await list_memories() if m.created_at >= since]
    decisions = [d for d in await list_decisions() if d.created_at >= since]
    completed_actions = [a for a in await list_actions(ActionStatus.COMPLETED) if a.completed_at and a.completed_at >= since]
    failed_actions = [a for a in await list_actions(ActionStatus.FAILED) if a.completed_at and a.completed_at >= since]
    completed_goals = [g for g in goals if g.completed_at and g.completed_at >= since]

    mission = await get_active_mission()
    # DISABLED, not fixed — 2026-08-27, see ARCHITECTURE_ISSUES.md's
    # matching entry. This used to compare against `mission.id` (the
    # current Mission VERSION row's own id), which made it fire for
    # EVERY active goal the instant a mission was ever superseded —
    # the same id/identity_id mix-up fixed for real in
    # reasoning/api.py's gather_grounded_evidence, but NOT fixable the
    # same way here: this check's whole premise (flag goals created
    # under a stale mission version) needs data Goal never records —
    # which mission version was active at creation time. Comparing
    # against `mission.identity_id` instead doesn't implement that
    # premise, it just makes the condition permanently False (Goal only
    # ever stores identity_id, so it always equals this), which is a
    # deliberate, explicit no-op — always empty, on purpose — until a
    # real fix is designed, rather than a check that's always wrong.
    misaligned_goals = [
        g for g in goals if g.status == GoalStatus.ACTIVE and mission and g.mission_id != mission.identity_id
    ]

    return {
        "goals": goals, "history": history, "memories": memories, "decisions": decisions,
        "completed_actions": completed_actions, "failed_actions": failed_actions,
        "completed_goals": completed_goals, "misaligned_goals": misaligned_goals,
    }


def _build_claims(ev: dict) -> list[EvidenceClaim]:
    claims = []
    if ev["completed_goals"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['completed_goals'])} goal(s) completed this week: "
                  + ", ".join(g.title for g in ev["completed_goals"]),
            supporting_ids=[g.id for g in ev["completed_goals"]],
        ))
    if ev["history"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['history'])} goal update(s) recorded this week.",
            supporting_ids=[h["goal_id"] for h in ev["history"]],
        ))
    if ev["memories"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['memories'])} new memor{'y' if len(ev['memories'])==1 else 'ies'} captured.",
            supporting_ids=[m.id for m in ev["memories"]],
        ))
    if ev["completed_actions"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['completed_actions'])} action(s) completed successfully.",
            supporting_ids=[a.id for a in ev["completed_actions"]],
        ))
    if ev["failed_actions"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['failed_actions'])} action(s) failed: "
                  + ", ".join(a.title for a in ev["failed_actions"]),
            supporting_ids=[a.id for a in ev["failed_actions"]],
        ))
    if ev["misaligned_goals"]:
        claims.append(EvidenceClaim(
            claim=f"{len(ev['misaligned_goals'])} active goal(s) reference a Mission version that's "
                  f"since been superseded.",
            supporting_ids=[g.id for g in ev["misaligned_goals"]],
        ))
    return claims


def _build_wins_and_problems(ev: dict) -> tuple[list[str], list[str]]:
    wins, problems = [], []
    if ev["completed_goals"]:
        wins.append(f"Completed {len(ev['completed_goals'])} goal(s): " + ", ".join(g.title for g in ev["completed_goals"]))
    if ev["completed_actions"]:
        wins.append(f"{len(ev['completed_actions'])} action(s) executed successfully.")
    if not wins:
        problems.append("No completed goals or actions recorded this week.")
    if ev["failed_actions"]:
        problems.append(f"{len(ev['failed_actions'])} action(s) failed and may need attention.")
    if ev["misaligned_goals"]:
        problems.append("Some active goals reference a Mission version that's since changed — worth reviewing (RV-08).")
    if not ev["history"] and not ev["memories"]:
        problems.append("No recorded goal or memory activity this week.")
    return wins, problems


def _template_narrative(mode: InsightMode, ev: dict, wins: list[str], problems: list[str]) -> str:
    if mode == InsightMode.REFLECTION:
        # RE-10: internal, direct, evidence-first — closer to raw findings.
        parts = [f"{len(wins)} win(s), {len(problems)} problem(s) this week."]
        parts += wins + problems
        return " ".join(parts)
    # REV-02: user-facing conversational narrative wrapping the same evidence.
    if wins:
        narrative = "This week: " + "; ".join(wins) + "."
    else:
        narrative = "This week was quiet — no completed goals or actions to report."
    if problems:
        narrative += " Worth a look: " + "; ".join(problems) + "."
    return narrative


# Real LLM replacement (architect decision, 2026-08-04): wins/problems stay
# computed deterministically from real evidence (unchanged) — the LLM only
# composes prose FROM that fixed list, grounded strictly in it, never
# inventing new claims. Falls back to the template above on any failure.
_LLM_SYSTEM = {
    InsightMode.REFLECTION: (
        "You are Jarvis's internal reflection engine. Write a blunt, direct, "
        "evidence-based paragraph from the wins/problems given — no praise, "
        "no shaming, just the facts and their implications (RE-10). Never "
        "mention anything not in the list given. Respond ONLY with JSON: "
        '{"narrative": "..."}'
    ),
    InsightMode.REVIEW: (
        "You are Jarvis, writing a warm but honest user-facing weekly review "
        "narrative (REV-01/REV-02) from the wins/problems given. Conversational, "
        "not a bullet list. Never mention anything not in the list given. "
        'Respond ONLY with JSON: {"narrative": "..."}'
    ),
}


async def _narrative(mode: InsightMode, ev: dict, wins: list[str], problems: list[str]) -> str:
    prompt = f"Wins: {wins or 'none'}\nProblems: {problems or 'none'}"
    task_type = "complex" if mode == InsightMode.REFLECTION else "conversation"
    result = await complete_json(system=_LLM_SYSTEM[mode], prompt=prompt, task_type=task_type, max_tokens=300)
    if not result or not result.get("narrative"):
        log.info(f"Insight narrative LLM call unavailable/invalid for mode={mode.value} — using template")
        return _template_narrative(mode, ev, wins, problems)
    return result["narrative"]


async def generate_insight(mode: InsightMode, days: int = 7) -> InsightRecord:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    ev = await _gather_evidence(since)

    evidence = _build_claims(ev)
    wins, problems = _build_wins_and_problems(ev)
    narrative = await _narrative(mode, ev, wins, problems)

    record = InsightRecord(
        mode=mode, scope=InsightScope.WEEKLY, narrative=narrative,
        evidence=evidence, wins=wins, problems=problems,
    )

    async with connection() as conn:
        await conn.execute(
            """INSERT INTO insight_record (id, mode, scope, narrative, evidence, wins, problems,
               user_response, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9);""",
            record.id, record.mode.value, record.scope.value, record.narrative,
            json.dumps([e.model_dump() for e in record.evidence]),
            json.dumps(record.wins), json.dumps(record.problems), record.user_response, record.created_at,
        )
    log.info(f"Generated {mode.value} insight: {len(wins)} win(s), {len(problems)} problem(s)")
    return record


async def record_user_response(insight_id: str, response: str) -> InsightRecord:
    """The one field allowed to change post-creation (§6.12)."""
    async with connection() as conn:
        result = await conn.execute(
            "UPDATE insight_record SET user_response = $1 WHERE id = $2;", response, insight_id
        )
        if result == "UPDATE 0":
            raise ValueError(f"InsightRecord {insight_id} does not exist")
    return await get_insight(insight_id)
