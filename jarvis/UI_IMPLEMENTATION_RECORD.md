# UI Implementation Record

## Scope
First pass at the real UI from `UI_HANDOVER_PROMPT.md`: an Electron
desktop shell over a new thin FastAPI backend (`server/app.py`) that
wraps `conversation.api` and the per-domain APIs. No changes to
`conversation/api.py`, `contracts/`, or `cli.py`.

## Open decisions — resolved
1. **Deployment**: Electron desktop app (user has full local machine
   access already; a desktop buddy, not a hosted service).
2. **Single vs multi-device**: single device, no auth. Revisit if
   remote/mobile access is ever wanted.
3. **Real-time vs polling**: polling on window focus (`BrowserWindow`
   `focus` event → renderer refetches). No websockets/push.
4. **Pending-confirmation rendering**: inline cards in the chat thread,
   normalized to one shape (`{id, kind, title, detail, reason}`) at the
   API JSON boundary in `server/app.py`'s `_normalize_pending` — the
   three underlying dataclasses in `conversation/api.py` and
   `capability_invocation/dispatch.py` are untouched.
5. **`/help /status /quit`**: `/status` → top status bar (`GET
   /status`, backend `/status` route). `/help` and the read-only slash
   commands → sidebar panels (Goals, Mission, Memory, Decisions), each
   calling `goals.api`/`mission.api`/`memory.api`/`reasoning.api`
   directly, not routed through chat. `/quit` → closing the window.

## What was built
- `jarvis/server/app.py` — FastAPI app. `/chat` wraps
  `conversation.api.handle()`; `/pending/resolve` wraps the three
  `resolve_pending_*` functions via a process-local id→object cache
  (bridges the request/response cycle the way `cli.py`'s loop variable
  bridged its blocking `input()` calls). `/goals`, `/mission`,
  `/memories`, `/decisions` are direct pass-throughs for the panels.
- `electron-app/` — `main.js` spawns the backend
  (`uvicorn server.app:app`) as a child process on launch and kills it
  on quit; `preload.js` exposes the backend URL and a `refresh` event
  to the renderer; `renderer/` is a single-page vanilla-JS UI (chat +
  4 panels), no build step.

## Not done / next
- No packaging (electron-builder) yet — `npm start` runs it unpacked.
- No tests added for `server/app.py` — needs the same real-Postgres
  discipline as the rest of the suite before this ships; flagging
  rather than skipping silently.
- `_PENDING_CACHE` is in-memory/process-local — fine for one desktop
  process, would need rethinking before any multi-device future.
- Goal creation panel (UI) not built — backend route (`POST /goals`)
  exists; only the read table is wired up in the renderer so far.
