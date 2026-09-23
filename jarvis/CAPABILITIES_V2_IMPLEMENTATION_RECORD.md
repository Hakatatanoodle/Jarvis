# Capabilities V2 — Implementation Record (web.search + reminders)

Suite: baseline 475 passed / 1 failed; now **499 passed / 1 failed**. The one failure,
`test_conversation_api.py::test_grounded_prompt_forbids_markdown`, fails identically on the
untouched tarball — pre-existing, not investigated here. 24 new tests in `tests/test_capabilities_v2.py`.

## Search provider: Tavily (open decision resolved)
Built for LLM-agent search (clean `content` snippets), free tier ~1,000 credits/month with no card
(basic search = 1 credit), one JSON POST. Over DuckDuckGo (keyless but noisy, no SLA) and Brave/Serper
(more cleanup for the same job). Config: `search.providers.tavily.api_key_env: TAVILY_API_KEY` in
`config/default.yaml` (+ `.env.example`), key read via `os.environ` from that named var; base_url,
`max_results` (6), `timeout_seconds` (8) also live in config. Swapping providers = one function + config.

## Risk math (ordinal LOW=0..CRITICAL=3, summed, clamped; permission/api.py: LOW/MEDIUM→GRANTED, HIGH→CONFIRM, CRITICAL→DENIED)
| capability | baseline | action type | undo | sum | result |
|---|---|---|---|---|---|
| web.search | LOW 0 | READ +0 | True +0 | 0 = LOW | GRANTED |
| reminders.create | LOW 0 | WRITE +1 | True +0 | 1 = MEDIUM | GRANTED |
| reminders.list | LOW 0 | READ +0 | True +0 | 0 = LOW | GRANTED |
| reminders.cancel | LOW 0 | WRITE +1 | False +1 | 2 = HIGH | CONFIRMATION_REQUIRED |

A MEDIUM baseline on either write capability would compute to 3 = CRITICAL = DENIED (the calendar_ops bug),
so both are LOW. `reminders.create` claims `supports_undo=True` only because `reminders.cancel` exists.
Cancel confirms on purpose (deletion). Tests pin every row via `compute_risk` and the real `check_permission`.

## Reminder delivery: poll-based (option 1 + the option-2 timer)
- `GET /reminders/due` (server/app.py) → `pop_due_reminders()`: one atomic `UPDATE … RETURNING`
  (pending→delivered), so overlapping polls can't double-deliver (tested with concurrent gather).
- `electron-app/src/main.js` polls on launch, on window focus, **and every 30s** while running, shows an OS
  `Notification` from the main process (renderer permission handler only allows `media`), and forwards
  to the renderer, which appends "⏰ Reminder: …" to the chat (preload +1 line, app.js +2).
- **Deviation from "poll-on-open baseline":** I added the 30s timer. Focus-only polling would deliver a 3pm
  reminder whenever you next click the window — not a notification. It's the handover's own option 2, ~5 lines.
- **Known limitations:** nothing fires while the app is fully quit (delivered on next launch); a reminder is
  marked delivered when *returned*, so if the renderer/OS toast fails it is not retried; the in-chat message
  isn't persisted to conversation history; CLI has no delivery path; background daemon = future work.

## Pipeline touch-points (all additive; handle()/Decision/Plan/Action/PCR untouched)
- Both capabilities use the existing `extra_parameters` mechanism (`query`; `reminder_text`, `due_at`,
  `cancel_target`) — no new hardcoded fields or dispatch clarify branches.
- `dispatch.py`: 4 entries in `_ACTION_TYPE_BY_CAPABILITY`; `_completed_detail` now covers the new ids (search
  results / reminder time in local tz folded into the grounding fact, else the reply model has nothing to relay);
  the hardcoded calendar tuple became `_DETAIL_CAPABILITY_IDS`.
- `extraction.py`: one prompt paragraph (V2 field guidance; `due_at` follows the calendar local-time rules).
- `conversation/api.py`: **router prompt string only** — added search/remind examples to #4. Without them the
  cheap router pass could return `capability_invocation_possible=false` for "remind me…" and the command would
  be silently dropped. No logic change.
- New: `contracts/reminder.py` (status enum kept out of enums.py), `reminders/api.py`, migration `0018_reminder.sql`,
  `capabilities/primitives/{web_ops,reminder_ops}.py`, two imports in `bootstrap.py`.
- Test infra: `conftest.py` TRUNCATE gained `reminder`; `test_five_primitives_registered` now pins all nine ids.

## Awkward spot in the pipeline (proposal, not done)
A required extra's `ParamSpec.description` is used both as the LLM's field description and verbatim as the user's
clarifying question (dispatch.py's generic gate). The `due_at` text is therefore a compromise. Proposal: an
optional `ParamSpec.clarify_question`. Also: `reminders.cancel` matches by case-insensitive substring and errors
on 0/>1 matches rather than asking "which one?" — a proper disambiguation gate would live in dispatch.

## Dogfooding — NOT done
The build sandbox has no Tavily key, no LLM keys, no Electron/display. Verified: real Postgres+pgvector suite,
mocked HTTP for search, `node --check` on the JS. **Unverified live:** Tavily's real response shape (coded
from its documented `results[].{title,url,content}`), router/extraction actually selecting the new capabilities
from natural language, and the Notification/chat delivery. Please dogfood: "search the web for …",
"remind me in 2 minutes to …", "what reminders do I have", "cancel my … reminder", and a reminder due while the
window is unfocused.
