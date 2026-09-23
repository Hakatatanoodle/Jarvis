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

import asyncio
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, UploadFile
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
from reminders import api as reminders_api

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
    outcome = await conv.handle(req.text, interface=conv.GUI_INTERFACE)
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
# Reminders (Capabilities V2) — delivery is poll-based. Returns every
# pending reminder whose due_at has passed and marks each delivered in
# the same atomic statement, so it is returned exactly once. Polled by
# electron-app/src/main.js (on launch, on window focus, and on a timer).
# ---------------------------------------------------------------------

@app.get("/reminders/due")
async def reminders_due():
    rems = await reminders_api.pop_due_reminders()
    return [{"id": r.id, "text": r.text, "due_at": r.due_at.isoformat()} for r in rems]


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


# ---------------------------------------------------------------------
# Voice input (STT). Push-to-talk only, no wake-word, no auto-send —
# the renderer records a clip, posts it here, gets a transcript back,
# and drops it into the chat input box for the person to review before
# sending. Local transcription (faster-whisper), not a cloud API: voice
# never leaves the machine, and it works offline.
#
# Requires ffmpeg/libav present on the system for decoding whatever
# format the browser's MediaRecorder produced (typically webm/opus) —
# `sudo apt install ffmpeg` on Debian/Ubuntu/Mint if transcription
# fails with a decode error.
#
# Model loads lazily on first request (a few seconds), not at server
# startup, so a slow model download/load never blocks /status or /chat
# from working. Size is small on purpose — this laptop is an older
# Ivy Bridge CPU (see UI_IMPLEMENTATION_RECORD.md's mic debug notes);
# "base.en" is a reasonable accuracy/speed tradeoff for short
# push-to-talk clips. Override with NIKA_WHISPER_MODEL if it's too
# slow or not accurate enough.
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        import os
        size = os.environ.get("NIKA_WHISPER_MODEL", "base.en")
        _whisper_model = WhisperModel(size, device="cpu", compute_type="int8")
    return _whisper_model


@app.post("/voice/transcribe")
async def transcribe(audio: UploadFile):
    import tempfile

    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        model = _get_whisper_model()
        segments, _info = model.transcribe(tmp_path, language="en")
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as e:
        raise HTTPException(500, f"transcription failed: {e}")
    finally:
        os.unlink(tmp_path)
    return {"text": text}


# ---------------------------------------------------------------------
# Voice output (TTS). Local synthesis via Piper — no cloud call, works
# offline, and small enough to run acceptably on an old CPU (see
# UI_IMPLEMENTATION_RECORD.md — same laptop as the whisper model, same
# reasoning for staying local and lightweight).
#
# Requires a Piper voice model downloaded once, since the ~60MB model
# files aren't bundled: from
# https://huggingface.co/rhasspy/piper-voices, grab e.g.
# en_US-lessac-medium.onnx and en_US-lessac-medium.onnx.json (same
# folder, same base filename — Piper expects them side by side), then
# set NIKA_PIPER_VOICE to the .onnx file's path. Without that env var
# set, /voice/speak returns a clear 500 rather than crashing at import
# time — TTS is opt-in, not required for the rest of the app to run.
_piper_voice = None


def _get_piper_voice():
    global _piper_voice
    if _piper_voice is None:
        voice_path = os.environ.get("NIKA_PIPER_VOICE")
        if not voice_path:
            raise HTTPException(
                500,
                "NIKA_PIPER_VOICE is not set — download a Piper voice model "
                "(see server/app.py's comment above _get_piper_voice) and "
                "point NIKA_PIPER_VOICE at its .onnx file.",
            )
        from piper import PiperVoice
        _piper_voice = PiperVoice.load(voice_path)
    return _piper_voice


class SpeakRequest(BaseModel):
    text: str


@app.post("/voice/speak")
async def speak(req: SpeakRequest):
    # Streams raw 16-bit PCM chunks as Piper generates them, instead of
    # synthesizing the entire reply into one WAV file before sending
    # anything — that made even a short reply wait for full synthesis
    # before any sound started. The renderer plays each chunk the
    # moment it arrives (see speakText's Web Audio scheduling in
    # app.js), so playback starts on the first chunk, not the last.
    #
    # No WAV container here — just raw PCM — since a WAV header needs
    # the total byte count up front, which isn't known until synthesis
    # finishes. Sample rate is sent as a response header instead so the
    # client knows how to interpret the raw bytes.
    #
    # Piper's synthesize() is itself a blocking, CPU-bound generator —
    # run in a background thread with a queue rather than iterated
    # directly in the route, so a long reply doesn't stall the event
    # loop (and every other request, like /status or /chat) while it's
    # being spoken.
    import queue
    import threading

    voice = _get_piper_voice()
    sample_rate = voice.config.sample_rate
    q: "queue.Queue" = queue.Queue()
    _SENTINEL = object()

    def produce():
        try:
            for chunk in voice.synthesize(req.text):
                q.put(chunk.audio_int16_bytes)
        except Exception as e:  # surfaced to the client via the stream below
            q.put(e)
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=produce, daemon=True).start()

    async def pcm_stream():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        pcm_stream(),
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": str(sample_rate)},
    )
