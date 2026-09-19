"""
Nika UI backend (V1-UI milestone). Thin HTTP wrapper around the existing
system — conversation/api.py's handle()/resolve_pending_*, plus the
per-domain APIs the CLI's slash commands already call directly
(goals.api, memory.api, mission.api, reasoning.api).

Per UI_HANDOVER_PROMPT.md's "What NOT to change": no logic lives here.
Every endpoint is a direct pass-through to an existing async function,
plus JSON (de)serialization. If something is awkward to expose this
way, that's a signal to revisit the contract, not to route around it.

Local-only, no auth (single-device Electron shell — see handover doc's
open decision #2, resolved: no auth needed at this stage).

Run: uvicorn server.app:app --port 8756
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import capabilities.bootstrap  # noqa: F401 — registers all primitive capabilities, same as cli.py
import conversation.api as conv
from config.loader import load_config
from contracts.enums import GoalStatus
from goals import api as goals_api
from infra.migrate import apply_migrations
from infra.storage import close_pool, init_pool
from memory import api as memory_api
from mission import api as mission_api
from reasoning import api as reasoning_api

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Same bootstrap cli.py's _bootstrap() does — init_pool + migrations
    # — must happen before any subsystem touches the database. Missing
    # this is exactly what produced the "Storage pool not initialized"
    # 500 on every route in the first cut of this file.
    cfg = load_config()
    pool = await init_pool(cfg)
    await apply_migrations(pool)
    yield
    await close_pool()


app = FastAPI(title="Nika UI backend", lifespan=lifespan)

# Electron loads the renderer from a file:// or localhost origin;
# either way this is a local, single-user process, so a permissive
# CORS policy here doesn't widen any real attack surface.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _json_safe(value: Any) -> Any:
    """Recursively make dataclasses/enums/UUID/datetime JSON-serializable
    without hand-writing a serializer per contract type — every contract
    in contracts/ is a plain @dataclass, so this generalizes over all of
    them instead of drifting out of sync as new ones get added."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _json_safe(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, UUID)):
        return str(value)
    if hasattr(value, "value") and type(value).__mro__[1].__name__ == "Enum":
        return value.value
    return value


# ---------------------------------------------------------------------
# Chat — the one integration seam the handover doc calls out. The
# blocking input()-loop shape in cli.py is replaced by: send the reply
# + a normalized list of pending-confirmation cards in one response;
# the frontend renders them; each is resolved via its own follow-up
# call to /pending/resolve.
# ---------------------------------------------------------------------

class ChatRequest(BaseModel):
    text: str


def _normalize_pending(outcome: conv.ConversationOutcome) -> list[dict]:
    """Open decision #4 (handover doc): the three pending-confirmation
    types differ slightly in shape today (capability's `reason` already
    includes risk framing; memory/state-change don't). Rather than
    touching the contracts themselves (explicitly out of scope), this
    normalizes them at the API boundary into one card shape the
    frontend can render identically: {id, kind, title, detail, reason}.
    """
    cards: list[dict] = []
    for pm in outcome.pending_memories:
        c = pm.candidate
        cards.append({
            "id": pm.source_id + ":" + str(id(pm)),
            "kind": "memory",
            "title": f"Remember: {c.title}",
            "detail": c.value,
            "reason": pm.reason,
            "_raw": _json_safe(pm),
        })
    for pc in outcome.pending_state_changes:
        c = pc.candidate
        cards.append({
            "id": str(id(pc)),
            "kind": "state_change",
            "title": f"{c.operation.value} — {getattr(c, 'title', getattr(c, 'goal_id', ''))}",
            "detail": str(getattr(c, "new_status", "") or getattr(c, "statement", "")),
            "reason": pc.reason,
            "_raw": _json_safe(pc),
        })
    for pci in outcome.pending_capability_invocations:
        cards.append({
            "id": pci.pcr_id,
            "kind": "capability",
            "title": "Run capability",
            "detail": "",
            "reason": pci.reason,  # already carries the risk framing
            "_raw": _json_safe(pci),
        })
    return cards


# Pending confirmations referenced by id across the pending->resolve
# round trip. conversation.api's resolve_* functions take the original
# dataclass instance back, not just an id, so this process-local cache
# bridges the one request/response cycle in between (mirrors how
# cli.py held them in a local loop variable — same lifetime, just
# spanning two HTTP calls instead of one blocking input()).
_PENDING_CACHE: dict[str, tuple[str, Any]] = {}


@app.post("/chat")
async def chat(req: ChatRequest):
    outcome = await conv.handle(req.text)
    cards = _normalize_pending(outcome)
    for pm in outcome.pending_memories:
        cid = pm.source_id + ":" + str(id(pm))
        _PENDING_CACHE[cid] = ("memory", pm)
    for pc in outcome.pending_state_changes:
        _PENDING_CACHE[str(id(pc))] = ("state_change", pc)
    for pci in outcome.pending_capability_invocations:
        _PENDING_CACHE[pci.pcr_id] = ("capability", pci)
    return {
        "routed_to": outcome.routed_to,
        "response_text": outcome.response_text,
        "pending": [{k: v for k, v in c.items() if k != "_raw"} for c in cards],
    }


class ResolveRequest(BaseModel):
    id: str
    approved: bool


@app.post("/pending/resolve")
async def resolve_pending(req: ResolveRequest):
    entry = _PENDING_CACHE.pop(req.id, None)
    if entry is None:
        raise HTTPException(404, "pending confirmation not found or already resolved")
    kind, obj = entry
    if kind == "memory":
        note = await conv.resolve_pending_memory(obj, req.approved)
    elif kind == "state_change":
        note = await conv.resolve_pending_state_change(obj, req.approved)
    else:
        note = await conv.resolve_pending_capability_invocation(obj, req.approved)
    return {"note": note}


# ---------------------------------------------------------------------
# Structured panels — open decision from the handover doc: slash
# commands become real UI panels calling per-domain APIs directly,
# not routed through chat.
# ---------------------------------------------------------------------

@app.get("/goals")
async def get_goals(mission_id: Optional[str] = None):
    goals = await goals_api.list_goals(mission_id=mission_id)
    return [_json_safe(g) for g in goals]


class CreateGoalRequest(BaseModel):
    title: str
    description: str = ""
    goal_type: str = "Task"
    parent_goal_id: Optional[str] = None


@app.post("/goals")
async def post_goal(req: CreateGoalRequest):
    from contracts.enums import GoalType
    try:
        gtype = GoalType(req.goal_type)
    except ValueError:
        raise HTTPException(400, f"unknown goal_type {req.goal_type!r}")
    goal = await goals_api.create_goal(
        type=gtype, title=req.title, description=req.description,
        parent_goal_id=req.parent_goal_id,
    )
    return _json_safe(goal)


class SetGoalStatusRequest(BaseModel):
    status: str
    reason: str = "Set via UI"


@app.post("/goals/{goal_id}/status")
async def post_goal_status(goal_id: str, req: SetGoalStatusRequest):
    try:
        status = GoalStatus(req.status)
    except ValueError:
        raise HTTPException(400, f"unknown status {req.status!r}")
    goal = await goals_api.set_status(goal_id, status, req.reason)
    return _json_safe(goal)


@app.get("/memories")
async def get_memories(status: Optional[str] = None):
    from contracts.enums import MemoryStatus
    st = MemoryStatus(status) if status else None
    mems = await memory_api.list_memories(status=st)
    return [_json_safe(m) for m in mems]


@app.post("/memories/{memory_id}/forget")
async def forget_memory(memory_id: str, reason: str = "Forgotten via UI"):
    mem = await memory_api.forget_memory(memory_id, reason)
    return _json_safe(mem)


@app.get("/memories/taxonomy-gaps")
async def taxonomy_gaps():
    gaps = await memory_api.list_taxonomy_gaps()
    return [_json_safe(g) for g in gaps]


@app.get("/mission")
async def get_mission():
    m = await mission_api.get_active_mission()
    return _json_safe(m) if m else None


@app.get("/mission/versions")
async def get_mission_versions():
    return [_json_safe(m) for m in await mission_api.list_mission_versions()]


class SetMissionRequest(BaseModel):
    title: str
    statement: str
    principles: Optional[list[str]] = None


@app.post("/mission")
async def post_mission(req: SetMissionRequest):
    m = await mission_api.set_mission(req.title, req.statement, req.principles)
    return _json_safe(m)


@app.get("/decisions")
async def get_decisions(intent: Optional[str] = None):
    decisions = await reasoning_api.list_decisions(intent=intent)
    return [_json_safe(d) for d in decisions]


@app.get("/status")
async def status():
    """Replaces the CLI's /status — a lightweight snapshot for the
    UI's top status bar (open decision #5)."""
    m = await mission_api.get_active_mission()
    return {"mission": _json_safe(m) if m else None, "ok": True}
