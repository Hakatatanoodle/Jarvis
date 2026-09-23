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

## Architecture review follow-up (2026-09-18)
A friend's review of the first cut caught 4 real issues. Status:

1. **Nika didn't know it had a GUI — FIXED.** `conversation/api.py`'s
   system prompt and honesty guards hardcoded "terminal-only, no GUI"
   unconditionally, so the model could (and, per dogfooding-style
   tests, would) tell an Electron user "there's no menu for that."
   Fixed by introducing an `Interface` dataclass
   (`TERMINAL_INTERFACE` / `GUI_INTERFACE`) threaded as an optional
   `handle(..., interface=...)` param, defaulting to `TERMINAL_INTERFACE`
   so `cli.py` and every existing test are unchanged. `server/app.py`'s
   `/chat` route passes `GUI_INTERFACE`. The onboarding message
   ("no Mission set yet") is now also interface-aware — GUI users are
   told to use the Mission tab, not `/mission set`. New tests added
   (`test_gui_interface_does_not_flag_real_gui_elements`,
   `test_gui_interface_does_not_claim_terminal_only`); one existing
   test (`test_grounded_prompt_forbids_markdown`) was updated because
   it literally asserted the now-fixed bug's wording ("plain text
   terminal").
2. **Packaging/distribution — NOT fixed, genuinely open.** The
   Electron app currently launches Python from an external
   `NIKA_REPO_PATH` and expects a pre-existing venv + reachable
   Postgres — a dev shell, not a shippable app. Real options, not yet
   decided:
   - Bundle a Python runtime + deps into the Electron package
     (`electron-builder` + `pyinstaller`/embedded Python) — biggest
     effort, most self-contained.
   - Require Postgres separately (Docker Compose or a documented local
     install) and only bundle the Python app itself — medium effort.
   - Ship as a dev-only tool for now and defer real packaging until
     the UI's feature set stabilizes — zero effort, but not
     distributable to anyone but the builder.
   Needs a decision before this goes further than one machine.
3. **Unsafe `innerHTML` with backend-sourced data — FIXED.** Goals,
   Memory, Mission, Decisions panels and the pending-confirmation cards
   now build DOM nodes via a small `el()` helper that always uses
   `textContent`, never string-interpolated `innerHTML`, for anything
   that ultimately comes from the database or the model.
4. **Chat history wiped on tab switch — FIXED.** `#chatlog` is now a
   single DOM node created once and re-attached to the view container
   on every visit to the Chat tab, instead of being rebuilt from
   scratch by `chatView()` — switching to Goals and back no longer
   loses the visible conversation (the backend's own history was never
   actually lost; only the display was).

## Naming
Per review: distinguish **Nika** (product/user-facing name) from
**Jarvis** (historical/internal project name, used in pre-rename docs
and some internal paths) rather than aggressively renaming everything
now. Revisit as a deliberate repo-level rename later, not as a side
effect of UI work.

## Still not done / next
- No tests added for `server/app.py` itself — needs the same
  real-Postgres discipline as the rest of the suite before this ships.
- `_PENDING_CACHE` (in `server/app.py`) is in-memory/process-local —
  fine for one desktop process, would need rethinking before any
  multi-device future.
- Goal creation panel (UI) not built — backend route (`POST /goals`)
  exists; only the read table is wired up in the renderer so far.

## Voice (2026-09-20)
- **Input (STT):** `/voice/transcribe`, local `faster-whisper`
  (`base.en` by default, `NIKA_WHISPER_MODEL` to override). Push-to-talk
  mic button in the chat input bar — records, transcribes, drops text
  into the input box for review; nothing auto-sends. Requires system
  `ffmpeg`. Confirmed working end-to-end on the earphone mic (internal
  mic on this laptop is dead — see mic debug report; charger must be
  unplugged or swapped to avoid interference noise, and
  `amixer -c 0 sset Capture 50%` now runs automatically on every
  Electron launch).
- **Output (TTS):** `/voice/speak`, local Piper (`NIKA_PIPER_VOICE`
  must point at a downloaded `.onnx` voice model — not bundled, ~60MB,
  see the comment above `_get_piper_voice` in `server/app.py` for the
  download source). Opt-in via a 🔊 toggle in the chat input bar,
  off by default on first run, persisted in `localStorage` after that.
  When on, every Nika reply is spoken automatically after it's
  displayed. TTS failures are non-fatal to chat — logged, not thrown
  at the user.
- Both features are local-only by design — no audio leaves the
  machine, and both keep working with no internet connection.
- **Streaming (2026-09-20):** `/voice/speak` now streams raw 16-bit
  PCM chunks (via a background thread + queue, so long synthesis
  doesn't block the event loop) instead of returning one finished WAV
  file. The renderer plays each chunk on arrival via the Web Audio API
  (gapless scheduling, not `<audio>` + blob), so playback starts on
  the first chunk rather than waiting for the whole reply to finish
  synthesizing. Also fixed a real bug found while debugging silent
  TTS output: `piper-tts`'s `synthesize()` is a generator in current
  versions (yields chunks with `.audio_int16_bytes`), not a function
  that writes into a wave file directly — calling it the old way
  silently produced a valid-looking but completely empty (44-byte,
  zero-frame) WAV with no error at all.

