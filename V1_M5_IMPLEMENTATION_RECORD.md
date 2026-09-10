# V1-M5 Implementation Record — Real Google Calendar via MCP

## What shipped
`calendar.read_events` / `calendar.create_event` now call a real Google
Calendar, via a self-hosted `calendar-mcp` server
(`nspady/google-calendar-mcp` v2.6.1, Docker), instead of Jarvis's own
`calendar_event` Postgres table. Routed through V1-M4's existing
`extract_candidate` -> `resolve_and_dispatch` -> `check_permission()`
pipeline exactly as instructed — no new invocation pathway, same two
capability ids as before (`test_five_primitives_registered` in
`tests/test_m5_capabilities.py` still pins this exact 5-id set).

Confirmed decisions (asked and answered before any code was written,
per the handover's explicit gate):
- **MCP server + deployment**: community server, Docker, HTTP
  transport bound to `127.0.0.1` only (not stdio — see
  `docker-compose.yml`'s comment for why: stdio is a 1:1 pipe per
  client process and doesn't fit a long-running compose service the
  way Postgres's connection pool does; loopback-only HTTP accepts the
  same local trust boundary this repo already accepts for Postgres).
- **Multi-account**: work + personal, from the start. Changed
  `CapabilityInvocationCandidate`'s shape (`account_ref` /
  `resolved_account` / `account_match_count`, mirroring `goal_ref`'s
  exact contract) and `config/default.yaml`'s new `calendar.accounts`
  list, rather than being deferred.
- **Credential storage**: new gitignored `data/google_calendar/`
  directory (same pattern `data/files/*` already established), holding
  the GCP OAuth client config and the token file the community server
  manages itself. Jarvis does not implement its own token
  storage/rotation — that's delegated to calendar-mcp; Jarvis's own new
  surface is `infra/mcp_client.py`'s `CalendarAuthError`, which turns
  whatever calendar-mcp returns for a dead/expired token into one
  clear, actionable message instead of a raw protocol error.

## New files
- `infra/mcp_client.py` — thin async client scoped to the one MCP
  server Jarvis has today (no generic multi-server abstraction — see
  its own docstring for why generalizing now would be premature).
  Connects fresh per tool call (simplest-correct-first, same principle
  `permission/risk_calculator.py` already cites for M3). Exposes
  `CalendarMCPError` / `CalendarAuthError`; response parsing/
  error-classification is split into a pure `_parse_tool_result()`
  specifically so it's unit-testable without mocking the transport
  (`tests/test_mcp_client.py`).
  **[updated again: added `_unwrap_exception_group()` so a TaskGroup
  failure surfaces its real underlying exception instead of the
  generic "unhandled errors in a TaskGroup" wrapper — see post-ship
  fixes below]**
- `tests/test_mcp_client.py` — 8 unit tests against fake result
  objects: JSON-object/array/plain-text response shapes, auth-error
  keyword detection (including the `-32600` code upstream's README
  documents for an unauthenticated call), non-auth tool errors, and the
  no-content-on-error edge case.
- `data/google_calendar/.gitkeep` — placeholder for the gitignored
  credential/token directory.

## Files touched
**Note on this section**: written at initial build time, before live
setup surfaced the seven post-ship fixes documented in their own
sections further down this file. Each file below that was touched
again during those fixes is marked **[updated again, see post-ship
fixes below]** rather than re-describing the change twice — this
section is the structural "what and why" from the original build; the
post-ship sections are the "what actually broke and how it was fixed"
from real usage. Read both for the full picture of any one file.

- `capabilities/primitives/calendar_ops.py` — **rewritten**, not
  patched. Old local-Postgres-table executors replaced with
  `infra.mcp_client.call_calendar_tool` calls
  (`list-events`/`create-event`). See "Risk levels, re-derived" below.
  **[updated again: baseline risk level corrected LOW (was wrongly
  MEDIUM), event_id/html_link extraction corrected for the real nested
  response shape — see post-ship fixes below]**
- `contracts/capability_invocation_candidate.py` — added
  `title`/`start`/`end`/`account_ref`/`resolved_account`/
  `account_match_count`, per M4's own docstring plan ("the same
  dataclass is meant to grow new optional fields per capability").
- `capability_invocation/extraction.py` — added calendar's prompt
  section (including the current UTC time, so the model can resolve
  relative phrases like "tomorrow at noon"), `_looks_like_iso_datetime`
  shape validation, and `resolve_account_ref` (a direct structural
  copy of `state_change.extraction.resolve_goal_ref`'s verbatim-match
  contract, over `config/default.yaml`'s static account list instead
  of a DB query — not literally reused, since the two match against
  different shapes).
- `capability_invocation/dispatch.py` — added
  `calendar.read_events`/`calendar.create_event` to
  `_ACTION_TYPE_BY_CAPABILITY` (READ/WRITE), and a clarify-gate block:
  missing start/end, missing title (create_event only), and an
  explicitly-given-but-unresolved account_ref all short-circuit to a
  clarifying question before dispatch — **an omitted account_ref is
  explicitly NOT a clarify case** (see `test_calendar_omitted_account_
  is_not_a_clarify_case`), since calendar-mcp itself treats "no
  account given" as a meaningful default (merge-all for reads,
  auto-select for writes), not a missing required field.
  **[updated again: added `_completed_detail()` so a completed
  calendar action's real output actually reaches the reply-generation
  model instead of being discarded — see post-ship fixes below]**
- `conversation/api.py` — **the one cross-file touch outside new/
  calendar files**, mirroring M4's own "one cross-file touch" precedent
  for `resolve_goal_ref`: added `known_accounts=load_config().get(
  "calendar.accounts", [])` to the existing `extract_capability_
  invocation(...)` call site, and a `config.loader` import. This is
  additive (a new kwarg with a working default), not a redesign of
  anything M4 built — the call site was already generically threading
  capability-specific context (`known_goals`) through in exactly this
  shape.
  **[updated again: added calendar examples + a clock-time-vs-Task
  disambiguation rule to both the `capability_invocation_possible` and
  `state_change_possible` routing gates — see post-ship fixes below]**
- `infra/llm_router.py` — **a second cross-file touch outside M5's own
  calendar files**, not flagged in the original plan since it wasn't
  anticipated: predates M4/M5 entirely (§7, INF-05/06/07/08), but
  needed a genuine bug fix once real usage exposed it. See "Post-ship
  fix... infra/llm_router.py" below for the full story — a control-flow
  ordering bug that made `GroqProvider.call()`'s own truncation-retry
  logic unreachable for the exact case (a reasoning model spending its
  whole token budget before answering) it was built to handle.
- `config/default.yaml` — new `calendar:` section (`mcp.server_url`,
  `mcp.request_timeout_seconds`, `accounts`). No credential or token
  value anywhere in this file, per §8's discipline.
- `docker-compose.yml` — new `calendar-mcp` service.
  **[updated again: `network_mode: host` + `HOST=127.0.0.1` fix for the
  `0.0.0.0` redirect_uri bug, plus a prominent "KNOWN BROKEN" warning —
  this service is no longer the tested/working path, see post-ship
  fixes below]**
- `.gitignore` — `data/google_calendar/*` (mirrors `data/files/*`).
- `requirements.txt` — `mcp>=1.9,<3.0`. Pin range chosen after
  confirming `mcp==2.1.1` (latest on PyPI at pin time) installs and
  imports cleanly in this sandbox (`pip download` + wheel inspection —
  see below); **not** exercised against a live MCP server at pin time
  (later was, live, successfully — see post-ship fixes).
- `tests/test_m5_capabilities.py` — calendar tests rewritten to mock
  `capabilities.primitives.calendar_ops.call_calendar_tool` (patched at
  the executor's import site) instead of asserting against the old
  Postgres table; added a dedicated read-events test and an
  auth-error-surfaces-as-a-failed-CapabilityResult test.
- `tests/test_capability_invocation_extraction.py` — 5 new tests for
  the calendar fields (resolution, omitted-account-is-not-an-error,
  non-ISO start/end dropped, fields absent for non-calendar
  capabilities).
- `tests/test_capability_invocation_dispatch.py` — 6 new tests for the
  clarify-gate block and one end-to-end calendar.create_event dispatch
  (confirmation -> mocked MCP call -> completed).

## Risk levels, re-derived (not inherited — per the handover's explicit instruction)
- `calendar.read_events`: unchanged at LOW / `calendar.read` /
  `supports_undo=True`. A real read carries the same risk shape as the
  old fake one — `ActionType.READ` never bumps in
  `permission/risk_calculator.py`.
- `calendar.create_event`: baseline MEDIUM / `calendar.write` /
  **`supports_undo=False`** (was `True` in the old file, but that
  file's own docstring already admitted undo wasn't actually wired —
  this is a correction, not a downgrade). MEDIUM + WRITE (+1 impact) +
  no undo (+1 reversibility) computes to HIGH ->
  `CONFIRMATION_REQUIRED` every time — same *behavior* as the old file,
  now honestly justified: a write landing on a calendar the person
  actually looks at deserves at least this much friction.
- **Attendees/invites are explicitly out of scope for M5.**
  `calendar.create_event` does not accept an `attendees` parameter.
  Inviting real people is materially different from a solo-calendar
  write (visible to others, not undoable by deleting the event
  afterward) and folding it into this same tier without dedicated
  parameter-level risk logic (`risk_calculator.py`'s still-unused
  `parameter_bump` hook) would under-represent it. This was surfaced
  and left as a deliberate scope cut, not silently supported.

## What was NOT verified (be honest about this before shipping)
This build sandbox has no network path to Docker Hub/arbitrary image
builds, a running calendar-mcp instance, or Google's OAuth endpoints
(same allowlist constraint `docker-compose.yml`'s own comment already
documents for Postgres). Concretely unverified:
1. **Tool names/argument shapes** (`list-events`/`create-event`,
   `timeMin`/`timeMax`, `calendarId`, `account`) — taken from
   upstream's own docs, not observed against a live server. Run
   `list_tools()` against the real running container before trusting
   `calendar_ops.py`'s tool calls.
2. **Response shapes** this module assumes (`{"events": [...]}` for
   list-events; an object with an `id` field for create-event) — same
   caveat.
3. **The `mcp` PyPI package's transport import name** — verified only
   that `mcp==2.1.1` installs and `mcp.client.streamable_http.
   streamable_http_client` + `mcp.ClientSession` import successfully in
   this sandbox (confirmed via `pip download` + wheel inspection); the
   actual `call_calendar_tool()` I/O path has never run against a real
   session.
4. **The auth-error keyword heuristic** (`_looks_like_auth_error`) —
   built from upstream's documented error codes/messages, not an
   observed real failure response.

What WAS verified in this sandbox: `infra/mcp_client.py`'s pure parsing
logic (8 unit tests, passing), all calendar-aware extraction logic (5
new + 7 original tests, passing), all touched files compile
(`py_compile`), and `capabilities.bootstrap` still registers exactly
the expected 5 capability ids with no import-time errors. The two
DB-backed integration suites
(`tests/test_m5_capabilities.py`, `tests/test_capability_invocation_
dispatch.py`) could not be run here — no Postgres in this sandbox,
same constraint `tests/conftest.py`'s own docstring already describes
for every DB-backed test in this repo, M5 included.

## M4 shared-foundation check (per the M5 handover's guardrail)
No bug, gap, or design flaw was found in V1-M4's foundation while
building this. The one addition to a shared file
(`conversation/api.py`'s new `known_accounts` kwarg) is additive with a
working default (`[]`) — it does not change existing behavior for
`goals.advance` or any capability that doesn't read `known_accounts`.
If the M6 (Filesystem) session also needs to thread new
capability-specific context through that same call site, the two new
kwargs should compose fine as separate additions; flag it in the merge
if they don't.

## Known gaps / explicitly out of scope for M5
- No `calendar.update_event` / `calendar.delete_event` capability
  (calendar-mcp exposes both as tools, but only the two pre-existing
  capability ids were replaced — see "What shipped" above).
- No `attendees` support on `calendar.create_event` (see "Risk levels"
  above).
- `data/google_calendar/`'s actual OAuth setup (creating a GCP OAuth
  client, running calendar-mcp's own `auth` flow once per account) is
  an operational step for whoever runs this, not something this
  milestone's code does — same as `docker-compose.yml`'s Postgres
  service already leaves password/DB creation to environment setup.

## Handoff note: this tar ships with ZERO credentials, on purpose
Explicit, not just implied by the `.gitignore` entries: `data/
google_calendar/` in this delivered tar contains only `.gitkeep` — no
`gcp-oauth.keys.json`, no `tokens.json`. `.env` isn't in here either.
Whoever picks this up (M6 merge or otherwise) needs to redo, from
scratch, on their own machine:
1. A Google Cloud OAuth client (Desktop app type) — console.cloud.
   google.com/auth/clients — downloaded once at creation time, since
   Google no longer allows re-downloading an existing client's secret
   later (only re-generating a new one).
2. Placing that as `data/google_calendar/gcp-oauth.keys.json`.
3. Running calendar-mcp (see "calendar-mcp's real root cause" below
   for why that means a native `node build/index.js` process with the
   SDK pin applied, NOT `docker-compose.yml`'s `calendar-mcp` service
   as currently written) and completing the `Add Account` flow at
   `/accounts` for at least one account matching a nickname in
   `config/default.yaml`'s `calendar.accounts` list.
4. A working `.env` with real `GROQ_API_KEY`/`GEMINI_API_KEY`/
   `OPENROUTER_API_KEY` values and a `POSTGRES_PORT` that doesn't
   collide with any pre-existing local Postgres.
None of this is a code gap — it's the same category of "environment
setup, not milestone deliverable" `docker-compose.yml`'s Postgres
service already left to whoever runs it (a fresh DB user/password).
Flagged explicitly here, rather than left implicit, because getting
through steps 1–3 the first time took the better part of this entire
session's live-setup effort — see every "Post-ship fix" section below
for exactly what went wrong at each step and why, so it goes faster the
second time.

## Post-ship fix, found during live setup (2026-08-31): `infra/llm_router.py`
Not part of M5's own scope — `infra/llm_router.py` predates M4/M5
(§7, INF-05/06/07/08; its own docstring's bug-fix history goes back to
2026-08-05) and calendar-mcp integration never touches it. Logged here
anyway because it was the thing actually blocking real end-to-end use
of this milestone's work once OAuth/Docker networking were sorted out,
and because "quietly fixed and undocumented" is exactly the failure
mode the M5 handover's own guardrail exists to prevent — this is that
same discipline applied to a file outside the M4/M5 boundary the
handover specifically named, not an exception to it.

**Symptom**: `GroqProvider.call()` (model `openai/gpt-oss-20b`, the
configured default for the `chat`/fast route — see
`config/default.yaml`'s `llm.routes`) returned `empty/null content` on
essentially every call once real usage started, which made
`capability_invocation/extraction.py`'s `extract_candidate()` fail
closed every time (`complete_json` returns `None` on any
`LLMUnavailableError`) — so `calendar.create_event` could never
actually be reached via natural language, independent of anything
calendar/MCP-specific being wrong.

**Root cause**: `openai/gpt-oss-20b` is a reasoning model
(`reasoning_effort` defaults to `"medium"` per Groq's own docs) that
can spend its entire `max_tokens` budget on internal reasoning tokens
before writing any answer — `extraction.py`'s `max_tokens=400` is
small enough for this to happen routinely. `GroqProvider.call()`
already had a truncation-retry block for exactly this class of problem
(2026-08-24's own fix, cited in the file), but a control-flow bug made
it unreachable for the *total*-truncation case: the empty-content check
ran and raised **before** the `finish_reason == "length"` retry got a
chance to run, so a fully-empty response never reached the retry that
would have recovered it with a bigger budget.

**Fix**: reordered the block in `GroqProvider.call()` so the truncation
retry runs first and the empty-content check runs last, gated on
whatever the retry actually returned — same logic, same cap
(`_TRUNCATION_RETRY_CEILING`), no new parameters or config. Verified
compiles clean; the exact failure sequence from this session's live
logs (empty content + `finish_reason: "length"` on the first call) was
replayed against the new control flow with canned responses (no real
Groq call — no network path to Groq in this sandbox) and confirmed to
now reach and return the second, real response instead of raising.
**Not verified against a live Groq call** — same "requires local
verification" caveat this file's own docstring already carries for
every provider.

**Not done, flagged instead**: if `gpt-oss-20b` at `"medium"` effort
routinely exceeds even the retry's 4000-token ceiling on simple
extraction prompts, the more targeted fix is sending Groq's
`reasoning_effort: "low"` for the `chat`/fast route specifically —
not currently sent by this code at all. Left as a follow-up to try
only if the reorder fix alone turns out insufficient in practice,
since it's a behavior change (faster/shallower reasoning) rather than
a pure bug fix like the reorder above.

## Post-ship fix, found during live setup (2026-09-02/03): calendar-mcp's real root cause — an upstream `@modelcontextprotocol/sdk` regression
This is the actual explanation for essentially every calendar-mcp
failure logged above and below — the Docker networking fix, the
permission fixes, the exception-unwrapping fix were all real and
necessary, but none of them were the true root cause. Logged in full
because of how much investigation it took to find, and because the
fix (an exact version pin) is easy to accidentally lose on a future
`npm install`/`npm audit fix`.

**Symptom**: every single calendar-mcp tool call — `initialize`
included — failed with an empty, generic `HTTP 500` (`text/plain`,
zero-byte body), with **zero** JS-level error surfacing anywhere: not
in `docker compose logs`, not in a live-followed log, not through
`infra/mcp_client.py`'s exception-unwrapping fix, and not even through
a global `process.on('unhandledRejection'/'uncaughtException')`
handler injected directly into the built `build/index.js` as a last
resort. The *first* request to a freshly started server always
succeeded; every request after that, on the same running process,
failed identically.

**Root cause, confirmed by direct reproduction** (cloned
`nspady/google-calendar-mcp` v2.6.1, built and ran it locally,
independent of Jarvis or Docker entirely): the server's HTTP transport
runs in MCP's "stateless" mode (`sessionIdGenerator: undefined`,
`src/transports/http.ts`) and — per its own code comment — is meant to
support "multiple initializations" by reusing one transport instance
across every request. `@modelcontextprotocol/sdk` introduced a
breaking regression (independently confirmed via multiple unrelated
projects' GitHub issues hitting the identical symptom) somewhere around
1.25.0–1.26.0: stateless-mode transports became single-use, and reusing
one across a second request now fails internally in a way that never
surfaces as a normal, catchable JS exception. nspady's `package.json`
pins the SDK with a caret range (`"^1.27.0"`), so any fresh
`npm install` — Docker build or native — pulls whatever the current
1.x release is, which still has this regression baked in as of this
writing; nspady's own request-handling code was never updated to match
the SDK's new single-transport-per-request requirement.

**Fix, verified empirically**: in the locally-cloned server source,
pin `@modelcontextprotocol/sdk` to an exact pre-regression version:
```json
"@modelcontextprotocol/sdk": "1.24.3"
```
(replacing `"^1.27.0"`), then `rm -rf node_modules package-lock.json &&
npm install && npm run build`. Verified on both this build sandbox
(4 repeated calls against one running process, all successful) and the
person's own real machine (3 repeated calls, all successful) — before
the pin, both environments failed identically on every call past the
first. **`npm audit fix`/`npm audit fix --force` must never be run
against this pin** — either would very likely bump the SDK back past
1.24.3 and silently reintroduce this exact bug with no obvious warning
sign until the second request fails again.

**Also pivoted, as a direct consequence**: calendar-mcp moved from a
Docker-built service to a plain native Node process on the person's
own machine (`cd`'d into a manual local clone/build of the server,
run directly via `node build/index.js` with the same env vars
`docker-compose.yml` used to set). This wasn't required by the SDK fix
itself — Docker was never actually the problem, contrary to earlier
suspicion in this record — but the amount of Docker-specific friction
hit along the way (bind-mount permission mismatches between the
container's internal user and the host user, `0.0.0.0` binding into
the redirect_uri, image-layer rebuilds needed for any source change)
made native execution the pragmatic choice once a source-level patch
(the SDK pin) was needed anyway. **`docker-compose.yml`'s `calendar-mcp`
service is left in the repo but is no longer the documented/tested path
— it has NOT been updated with this SDK pin and should be assumed
broken until someone deliberately re-verifies and patches it** (the pin
would need to be applied via a local Dockerfile that clones+patches
`package.json` before building, since the current `build.context`
points directly at nspady's own unpatched Dockerfile/repo).

## Post-ship fix, found during live setup (2026-09-05): the M4 routing prompt never learned about calendar capabilities
Squarely in M5's own scope, not adjacent infra.

**Symptom**: real, correctly-phrased calendar requests ("add an event
at 12pm titled studying session") intermittently got misrouted —
sometimes silently dropped (`capability_invocation_possible` came back
`false`, so extraction never even ran), sometimes misfired into
creating a Goal instead (`state_change_possible` came back `true`,
`capability_invocation_possible` came back `false` for the same
message).

**Root cause**: `conversation/api.py`'s `_ROUTING_SYSTEM` prompt (M4's
own foundation, `capability_invocation_possible` added as its fourth
field per that milestone's record) only ever gave goal-shaped examples
for that field ("touch my Build Jarvis goal", "mark that I reviewed
the exam goal") — zero calendar examples anywhere. M5 extended the
*detailed*, capability-specific prompt in
`capability_invocation/extraction.py` with calendar's own fields, but
never came back to add a calendar example to this earlier, coarser
routing gate that decides whether that detailed pass even runs. With
no calendar-shaped example to pattern-match against, the model
defaulted toward the only shape it had ever been shown — goal-like.

**Fix**: added an explicit calendar example ("add an event today at
3pm titled Standup", "what's on my calendar this week") to
`capability_invocation_possible`'s guidance, plus an explicit
disambiguation rule (a clock time/date paired with a scheduling verb
signals calendar, not a goal, even when the message also names an
activity that could superficially read as a Task title). Applied the
mirror-image rule to `state_change_possible` (#3) as well, since that
gate was the one that actually fired and created a stray Goal in the
observed failure — without the matching carve-out there, fixing #4
alone wouldn't stop `state_change_possible` from independently saying
`true` for the same ambiguous phrasing. Verified: `conversation/api.py`
compiles, and the two new example phrases are present in
`_ROUTING_SYSTEM` at runtime (`'clock time' in _ROUTING_SYSTEM`).
**Not verified against a live LLM call** — this changes prompt wording,
not code logic, and its actual effect on real model routing decisions
can only be confirmed by the person retrying real calendar requests
against Groq/OpenRouter, not by anything testable in this sandbox.

## M5 confirmed working end-to-end (2026-09-06)
A real event was created via the full natural-language pipeline: `add
an event at 12pm titled studying session` -> extraction ->
confirmation prompt -> `calendar.create_event` executed ->
`Action ... Completed` -> a genuine Google Calendar event
(`cf3h47g04ne7d97844amobh6ts`, `calendarId: primary`,
`accountId: work`) with a real `htmlLink`, visually confirmed by the
person in their actual Google Calendar. This is the milestone's actual
goal, working.

One last small bug surfaced by finally having a real response to check
against: `_create_event`'s `event_id`/`html_link` extraction assumed a
flat response shape (`{"id": ..., ...}`) — the real server nests the
event under an `"event"` key (`{"event": {"id": ..., "htmlLink": ...,
...}}`). Fixed to check the nested shape first, falling back to flat
if absent, so the earlier flat-shape unit-test fixture in
`tests/test_m5_capabilities.py` still passes unchanged (verified
directly — both shapes produce the correct `event_id`). Also now
returns `html_link` in the executor's output, not just `event_id` —
a real, clickable confirmation link is more useful to surface than a
bare ID.

## Post-ship fix, found during live setup (2026-09-06): a completed calendar action's real result never reached the reply
Squarely M5's own bug — the deepest one found this session, and the
actual explanation for "Jarvis said it can't read my calendar" right
after a real, successful `calendar.read_events` call.

**Symptom**: `python3 test_list_events.py` (a direct, Jarvis-independent
script) proved the person's real Google Calendar event genuinely
existed and was genuinely retrievable. Yet asking Jarvis "what's on my
calendar today" — which the logs confirmed actually ran
`calendar.read_events` to `Completed` — got the reply "I don't have
access to your calendar right now."

**Root cause**: `capability_invocation/dispatch.py`'s `resolve_and_
dispatch()`, in its `"completed"` branch, built the note handed to the
reply-generation model as a bare template:
```python
return f"Ran {capability.name}{target}.", None, []
```
`payload` — `orchestrator.api._handle_action()`'s real return value,
i.e. the actual `CapabilityResult` with `.output` containing the real
events list (or the real `event_id`/`html_link` for a write) — was
fetched but never read in this branch. So the reply model was
literally never given the data it needed to answer with; "I don't have
that" was an honest reflection of an empty prompt, not a hallucination
this time.

**Fix**: added `_completed_detail(capability_id, output)`, called from
the `"completed"` branch and folded into the note text. Scoped to the
two calendar capabilities only (same `if capability_id in (...)`
discipline this file already uses elsewhere for calendar-specific
handling, not a generic "summarize every capability's output"
mechanism — no second data point yet to design that generally).
Verified directly against the person's own real, live response data
(the exact JSON `test_list_events.py` returned) via a standalone
script — correct event title/time relayed for reads, correct
`html_link` relayed for writes, an honest "no events found" for an
empty result, and a confirmed no-op for unrelated capabilities like
`goals.advance`. Four new pure-function unit tests added
(`test_completed_detail_*`) plus one DB-backed end-to-end test
(`test_calendar_read_events_relays_real_event_data_end_to_end`) — the
DB-backed one could not be run in this sandbox (no Postgres here, same
standing limitation as the rest of this suite); the pure-function ones
were verified both via pytest source review and a standalone
reproduction script matching the real data exactly.

## Note on this session's repeated file-loss incidents
Across this milestone's live setup, this exact `jarvis-M5` working
directory lost several files at least twice — once affecting
`capability_invocation/dispatch.py`, `capability_invocation/extraction.py`,
`contracts/capability_invocation_candidate.py`, `infra/mcp_client.py`,
and three test files simultaneously; separately, `data/google_calendar/`'s
OAuth credentials disappeared on its own at least twice more. The cause
was never conclusively identified (the working directory was, at the
time, not a git repository at all — ruling out the initial "git
checkout reverted it" theory once actually checked). Whatever the
mechanism, the practical fix applied was turning this directory into a
real git repository with a verified-clean `.gitignore` (confirmed via
`git status` that `.env` and `data/google_calendar/*` never appear
staged) and an initial commit, plus a personal backup of the OAuth
client JSON kept outside the repo entirely (gitignored files get no
protection from git regardless of repo state). Flagging this plainly
rather than pretending it's resolved: the root cause is still unknown,
and this record should not be read as ruling out recurrence.

## Post-ship fix, found during live setup (2026-08-31): `docker-compose.yml`'s `calendar-mcp` networking
Also outside M5's own scope by the time it surfaced (this was a
deployment/networking problem, not a code problem), logged for the
same reason as the `llm_router.py` fix above.

**Symptom**: Google rejected the OAuth callback with
`Error 400: invalid_request` — "error details" showed the actual
`redirect_uri` calendar-mcp sent was `http://0.0.0.0:3000/...`, which
Google correctly refuses as not a valid destination.

**Root cause**: verified against the actual v2.6.1 source
(`src/transports/http.ts`, `src/config/TransportConfig.ts`) — the
server reuses one `host` value both to bind its own listener and to
build the OAuth redirect URL it hands to Google, with no separate
"public callback host" setting. `HOST=0.0.0.0` (needed for the
container's published port to be reachable at all under the original
bridge-network setup) therefore leaked into the redirect_uri verbatim.

**Fix**: switched `calendar-mcp` to `network_mode: "host"` (the
person's machine is native Linux, where this is safe/appropriate) and
`HOST=127.0.0.1`, and dropped the `ports:` mapping entirely — under
host networking the container's `127.0.0.1` *is* the machine's real
loopback, so one value is now simultaneously correct for both binding
and the redirect_uri. **Not portable to Docker Desktop (macOS/Windows)**
— flagged in the compose file's own comment; would need an explicit
`ports:` mapping and a real routable `HOST` value there instead.

Separately (not a calendar-mcp problem at all, but hit during the same
`docker compose up` troubleshooting): the person's machine already had
a native, non-Docker Postgres bound to port 5432, colliding with this
project's own Postgres service. Fixed via `.env`'s existing
`POSTGRES_PORT` override (`docker-compose.yml`'s
`"${POSTGRES_PORT:-5432}:5432"` already supported this — no compose
change needed, just an env value: `POSTGRES_PORT=5433`).

## Post-ship fix, found during live setup (2026-09-01): `calendar.create_event`'s baseline risk level was wrong
This one is squarely M5's own bug, not adjacent infra — flagged
separately from the two above because it's a mistake in this
milestone's own design, not something outside its boundary.

**Symptom**: a real `calendar.create_event` invocation (via the actual
NL pipeline, extraction succeeding this time) came back
`risk=Critical` and was silently denied (Action `Pending -> Cancelled`)
instead of asking for confirmation.

**Root cause**: "Risk levels, re-derived" above originally set
`calendar.create_event`'s baseline to **MEDIUM**, on the stated
reasoning that MEDIUM + WRITE(+1) + no-undo(+1) "computes to HIGH." It
doesn't. `permission/risk_calculator.py`'s scale is
`{LOW:0, MEDIUM:1, HIGH:2, CRITICAL:3}`, additive, clamped at 3:
`MEDIUM(1) + 1 + 1 = 3 = CRITICAL`, and `permission/api.py` maps
CRITICAL to an outright `DENIED`, not `CONFIRMATION_REQUIRED` — a plain
arithmetic error when this milestone's risk levels were first proposed
and confirmed, not caught by any test at the time (the earlier
`test_calendar_create_event_requires_confirmation_then_executes` test
mocked the capability's own `baseline_risk_level` implicitly via the
real registered Capability, and DID pass — because it was written and
run against the same wrong value, so it couldn't catch its own
premise being wrong).

**Fix**: baseline changed to **LOW**.
`LOW(0) + WRITE(+1) + no-undo(+1) = 2 = HIGH -> CONFIRMATION_REQUIRED`,
the actually-intended behavior. Verified directly against the live
`permission.risk_calculator.compute_risk()` function (not just
re-derived by hand a second time) — see this record's git history /
the person's own session for the exact check. `tests/test_capability_
invocation_extraction.py`'s calendar fixture had the same wrong MEDIUM
value baked in and was corrected alongside it.

**Why this matters beyond the one bug**: `calendar.read_events` has no
such risk math to get wrong (LOW baseline, READ never bumps, nothing
to add), so this class of error was only possible on the one capability
with a genuine risk decision behind it — worth double-checking any
future capability's stated risk level against `_ORDINAL`'s actual
values directly rather than by hand, given this already happened once.

## Post-ship fix, found during live setup (2026-09-01): `infra/mcp_client.py` swallowed the real connection error
Confirming the risk-level fix above (person re-ran and got `risk=High`
correctly, confirmed the action) immediately surfaced the actual live
attempt to reach calendar-mcp — which failed with `unhandled errors in
a TaskGroup (1 sub-exception)`, a real error but a useless message.

**Root cause**: the `mcp` SDK's streamable-HTTP transport uses
`asyncio.TaskGroup` internally for its background read/write tasks.
Any failure inside it (connection refused, protocol error, whatever
the real cause turns out to be) gets wrapped in an `ExceptionGroup`,
whose default `str()` is exactly that generic message —
`call_calendar_tool()` let it propagate unmodified, so the real cause
never reached the log.

**Fix**: added `_unwrap_exception_group()` (drills into
`BaseExceptionGroup.exceptions` for the first leaf exception) and an
`except*` clause around the connect/session block in
`call_calendar_tool()`, re-raising as a `CalendarMCPError` with the
real exception's type and message. Verified two ways: existing
`tests/test_mcp_client.py` unit tests still pass unchanged (this
doesn't touch `_parse_tool_result`), and — more importantly — a real
`asyncio.TaskGroup` was deliberately made to fail with
`ConnectionRefusedError` in this sandbox and confirmed the unwrap
correctly surfaces `ConnectionRefusedError: [Errno 111] Connection
refused` instead of the generic wrapper text. **This fix makes the
next failure's message readable — it does not, by itself, fix
whatever is actually failing.** The person's session hadn't
re-attempted the real create-event call with this fix in place as of
this entry; whatever the unwrapped message says next is the actual
lead to chase (connection refused would point at the container/port;
something else would point elsewhere).

## Design addition, decided with the person (2026-09-08): generic per-capability extra-parameter mechanism, `recurrence` as first use

**Problem raised during dogfooding**: the person asked for recurring
calendar events. M5 (by design) has no `attendees`/`recurrence`/etc. —
every new Google Calendar feature would otherwise need its own
hand-written field across `capability_invocation/extraction.py`'s
prompt+schema+`_validate()` and `dispatch.py`'s clarify-gate, same
pattern `title`/`start`/`end`/`account_ref` already followed. Flagged
to the person as a real scaling problem ("~20 Google Calendar features"
= ~20 hand-written blocks), not something to just fix quietly — three
options laid out (keep the per-field pattern / bolt `recurrence` on as
one more hardcoded field / build a generic data-driven mechanism now).
Person chose the generic mechanism, scoped as **additive only** — the
existing hardcoded fields (`goal_ref`/`path_ref`/`content`/`title`/
`start`/`end`/`account_ref`) are untouched; migrating those to the new
shape is explicitly deferred, not attempted here (strangler pattern).

**What was built** (spans `contracts/`, `capability_invocation/`, and
`capabilities/primitives/calendar_ops.py` — cross-cutting, hence
recorded here rather than only in a per-file diff):
- `contracts/enums.py`: `ParamType` (`STRING`/`ENUM`/`DATETIME`/`BOOL`).
- `contracts/capability.py`: `ParamSpec` (type/required/description/
  choices) and `Capability.extra_parameters: dict[str, ParamSpec]`
  (default `{}` — every existing capability's behavior is byte-for-byte
  unchanged unless it opts in).
- `contracts/capability_invocation_candidate.py`: `extra: dict[str, Any]`
  (default `{}`), populated only for capabilities that declare
  `extra_parameters`.
- `capability_invocation/extraction.py`: `_collect_extra_param_specs()`
  builds the union of every registered capability's extra fields for
  the prompt; the JSON schema grows one generic `"extra": {...}` object
  instead of a new named top-level field per capability;
  `_validate_extra()` type-checks each value against the *selected*
  capability's own spec (STRING/ENUM/DATETIME/BOOL), silently dropping
  anything out-of-choices, wrong-shaped, or belonging to a capability
  that wasn't actually selected — same fail-closed discipline as the
  existing `start`/`end` ISO-format check.
- `capability_invocation/dispatch.py`: one generic loop over
  `capability.extra_parameters` in the clarify-gate (asks
  `spec.description` if a required field is missing) replaces what
  would otherwise be a new hardcoded `if capability_id == ...` branch
  per feature; `candidate.extra` merges straight into the Action's
  `parameters` dict.
- `capabilities/primitives/calendar_ops.py`: `calendar.create_event`
  declares `recurrence: ParamSpec(ENUM, required=False, choices=
  ["daily","weekly","none"])` — the first real (non-test-double) use of
  the mechanism. `_recurrence_kwargs()` maps it to Google Calendar's own
  RRULE shape (`{"recurrence": ["RRULE:FREQ=DAILY"]}` /
  `FREQ=WEEKLY`), omitted entirely for `"none"`/absent, same
  "omission is a valid, meaningful choice" precedent `_account_kwargs()`
  already established. Monthly/yearly/custom-interval recurrence is a
  follow-up (`choices` + one more `_RRULE_BY_RECURRENCE` entry), not a
  new mechanism.

**Not verified against a live calendar-mcp server** — same caveat as
every other calendar_ops.py shape in this record: the RRULE argument
name/shape is taken from upstream's own Google Calendar API convention
that calendar-mcp is documented to proxy, not observed against a
running server. Re-check once one is reachable, same as `create-event`'s
response shape was.

**Tests**: `tests/test_capability_invocation_dispatch.py` (2 new —
generic required-extra-param clarify-gate, via a throwaway test-double
capability, same pattern as the existing `fake_high_risk_capability`
fixture), `tests/test_capability_invocation_extraction.py` (5 new —
valid enum kept, out-of-choices value dropped, cross-capability field
filtered out, missing `extra` object defaults to `{}`, and one against
the real registered `calendar.create_event` capability), and
`tests/test_m5_capabilities.py` (2 new — `recurrence: "daily"` sends
the correct RRULE args end-to-end through `run_action`, `recurrence:
"none"` sends the exact same args dict as if `recurrence` were omitted
entirely). Full suite: `443 passed` before this change, `452 passed`
after (9 new, 0 broken), run against a real local Postgres 16 +
pgvector (built from source, `pgvector==0.8.6`), not mocked out.

## Infra addition, decided with the person (2026-09-08): three new free-tier LLM providers, chat/hard/coding routes rebuilt

**Problem**: live dogfooding showed Gemini permanently 403'd (account access
denied, not fixable in code) and Groq's 8K TPM cap blown almost every turn
(3 internal LLM calls/turn against a 2-provider chat chain). Person is
cashless (can't buy a paid Groq tier), so the fix is more free capacity in
the chain, not code logic — researched prior art (LiteLLM's Router,
freeflow-llm/llmbuffet's free-tier-chaining pattern) confirming "try each
candidate once, chain several free providers, cool down a failed one
instead of hammering it" as the standard approach.

**Providers added** (`infra/llm_router.py`), each a same-shape sibling of
the existing `GroqProvider` — plain OpenAI-compatible `chat/completions`,
verified against each provider's own docs on 2026-09-08, not against a
live call (no network route to these hosts from the dev sandbox — same
caveat as calendar-mcp):
- `ZaiProvider` — `https://api.z.ai/api/paas/v4/chat/completions`,
  `ZAI_API_KEY`. Person's key is z.ai-direct; **GLM-5.2/5.3 are metered
  even on a direct key** ($1.40/$4.40 per M) — only the `*-flash` tier
  (`glm-4.5/4.6v/4.7-flash`) is genuinely free/unlimited, so routes point
  at `glm-4.7-flash`, not what the person originally asked for by name.
- `CodeCraftProvider` — `https://codecraftapi.com/v1/chat/completions`,
  `CODECRAFT_API_KEY`. Free plan is a **1M-token/month allowance**, not
  unlimited (60 rpm cap too) — model chosen for the coding route is
  `deepseek-v4-flash-0731` (cheap, 1.048M context, reasoning+tools) so the
  allowance stretches further than a pricier model would.
- `GitHubModelsProvider` — `https://models.github.ai/inference/chat/completions`,
  `GITHUB_TOKEN` (any GitHub PAT with the `models` scope — not a
  service-specific key). Free for any GitHub account.

**`classify_error()`**: added `402 -> ErrorCategory.AUTH` (previously fell
to `UNKNOWN`'s short exponential backoff). A monthly quota exhaustion
(CodeCraft's actual failure mode once its 1M allowance runs out) won't
resolve within a 600s backoff window either way, so it gets the same
"skip to next candidate, longer cooldown" treatment as an auth failure —
applies to any provider that returns 402 for this reason, not just
CodeCraft.

**Routes rebuilt** (`config/default.yaml`) — each chain now leads with a
candidate that actually has free headroom, keeping Gemini/Groq further
down rather than removing them (in case the account issue or TPM cap ever
clears):
- `chat`: zai `glm-4.7-flash` → github `openai/gpt-4o-mini` → groq
  `openai/gpt-oss-20b` → gemini `gemini-3.5-flash-lite`.
- `hard`: github `openai/gpt-4.1` → github `deepseek/DeepSeek-R1` → groq
  `openai/gpt-oss-120b` → gemini `gemini-3.6-flash`. (Dropped
  `groq/qwen3.6-27b`'s old third rung — DeepSeek-R1 is a stronger free
  dedicated-reasoning replacement.)
- `coding`: codecraft `deepseek-v4-flash-0731` → github
  `mistral-ai/Codestral-2501` → openrouter `qwen/qwen3-coder:free` →
  gemini `gemini-3.6-flash`.
- Emergency pool unchanged (`openrouter/free`).

**Not addressed here (flagged, not investigated)**: while reading a
dogfooding transcript for this work, Gemini's 403 appeared to get
re-attempted well inside its own 900s AUTH cooldown window on one turn —
an isolated repro of the cooldown mechanism itself (mocked failures,
same process, same event loop) worked correctly, so this doesn't
reproduce outside the live session and wasn't chased further under this
change's scope. Worth another look with a live transcript timestamped
across that exact gap if it recurs.

**Tests**: `tests/test_llm_router.py` — every chat/hard/coding
route-order test rewritten for the new chains (`test_chat_primary_zai_
succeeds`, `test_hard_primary_github_gpt41_succeeds`, `test_coding_
codecraft_succeeds`, etc.), `402 -> AUTH` added to the classify_error
parametrize table, and 7 new provider-body tests for Zai/CodeCraft/
GitHubModels (truncation-retry + not-configured, mirroring Groq's
existing coverage) plus one for the 402-as-AUTH classification against a
real CodeCraft-shaped response. Full suite: `452 passed` before this
change, `462 passed` after, run against the same real local Postgres 16 +
pgvector.

## Dogfooding fix #1 (2026-09-08): MemoryType.RELATIONSHIP/CONSTRAINT lost during the generic-parameter-mechanism edit — crashed live on `'Relationship' is not a valid MemoryType`

**Symptom**: live session crashed mid-turn with a bare
`Error: 'Relationship' is not a valid MemoryType` right after a
calendar.create_event action reached permission-check — killed the
whole turn.

**Root cause — mine, from this same day's earlier generic-parameter-
mechanism change**: the `str_replace` that inserted the new `ParamType`
enum used an `old_str` ending at `MemoryType.SKILL`, not realizing
`MemoryType` had two more members (`RELATIONSHIP`, `CONSTRAINT`) below
that. The match still succeeded (as a substring), which silently
dedented the rest of `MemoryType`'s body into what became `ParamType`
instead — valid Python, wrong enum, no import error, nothing to catch
it. `MemoryType` was left with only PREFERENCE/FACT/SKILL, while
`ParamType` picked up two nonsensical extra members it never should
have had. `memory/extraction.py`'s own prompt still listed
"Relationship" as a valid type for the LLM to choose (never touched by
that edit), so the LLM picked it, and reading any memory row already
stored with that type (`memory/api.py`'s `MemoryType(row["type"])`)
raised uncaught. Confirmed by inspection, not guesswork —
`grep -rn "Relationship"` across the repo turned up every prompt/doc
still expecting a 5-member enum against the actual 3-member one.

**Why 452/462 passing tests didn't catch it**: nothing in the suite
ever exercised `MemoryType.RELATIONSHIP` or asserted the enum's full
member set — a gap now closed, not just a fixed instance.

**Fix**: restored `RELATIONSHIP`/`CONSTRAINT` to `MemoryType`; removed
them from `ParamType` (they never belonged there).

**Regression test**: new `tests/test_contracts_enums.py` — pins
`MemoryType`'s exact 5-member set and `ParamType`'s exact 4-member set
against each other, so this exact "sibling members silently migrate to
the wrong enum" failure mode fails a test immediately instead of
surfacing on whatever real data hits it first. Full suite: `462 passed`
before, `464 passed` after.

## Dogfooding fix #2 (2026-09-08, same session): GitHub Models is permanently retired (2026-07-30) — pulled from every route

**Symptom**: same transcript, GitHub Models returned
`410 Gone — github_models_retirement_brownout` for `openai/gpt-4o-mini`.

**Root cause — mine, from the provider-addition work earlier the same
day**: GitHub Models was fully, permanently shut down on 2026-07-30
(confirmed via GitHub's own changelog/support docs) — not a transient
outage despite the error text's "brownout" wording. Recommending it as
a second-tier candidate across all three routes (chat/hard/coding) was
based on training-era knowledge that should have been checked against
current status before being wired into config, the same "verify before
you rely on it" discipline this whole record is built on elsewhere.

**Fix**: removed every `github` route entry from `config/default.yaml`.
`GitHubModelsProvider` itself is left in `infra/llm_router.py`, unused,
rather than deleted — harmless to keep in case a similar free catalog
reappears under the same OpenAI-compatible shape. Rebuilt chains:
- `chat`: zai `glm-4.7-flash` → groq `openai/gpt-oss-20b` → gemini
  `gemini-3.5-flash-lite`.
- `hard`: codecraft `deepseek-v4-flash-0731` → groq `openai/gpt-oss-120b`
  → gemini `gemini-3.6-flash`. (CodeCraft now shares its 1M-token/month
  allowance across both `hard` and `coding` — simplest fix without
  onboarding a fourth provider; worth revisiting if that allowance turns
  out to get exhausted fast in practice.)
- `coding`: codecraft `deepseek-v4-flash-0731` → openrouter
  `qwen/qwen3-coder:free` → gemini `gemini-3.6-flash`.
- Emergency pool unchanged.

**Tests**: `tests/test_llm_router.py` — every route-order test
touching `github` rewritten for the 3-candidate chains (`test_chat_zai_
and_groq_fail_falls_back_to_gemini`, `test_hard_primary_codecraft_
succeeds`, `test_coding_codecraft_402_quota_exhausted_falls_back_to_
qwen`, etc.); the vision-capability test reverted to selecting Gemini
(the only vision-tagged candidate left on `hard`). `GitHubModelsProvider`
itself keeps its own provider-body tests (truncation-retry,
not-configured) since the class is untouched, just unused. Full suite:
`464 passed` before this fix, `462 passed` after (net -2: three
github-specific route tests removed as no longer applicable, one
capability test's assertions folded together — no coverage gap, same
scenarios re-tested against the real chain).

## Dogfooding fix #3 (2026-09-08): "12 pm" scheduled as 17:45 — no user-local-timezone concept existed anywhere in Jarvis

**Symptom**: "schedule a studying session at 12 pm" created a Google
Calendar event at 17:45 (Nepal local), not noon.

**Root cause — confirmed by reading the code, not guessed**: grepped
every "timezone" occurrence in the whole repo; every single one was
`from datetime import datetime, timezone` for a `created_at`/`updated_at`
bookkeeping field. There was no concept of the user's own local
timezone anywhere — not in config, not in any contract, not in
`contracts/user_model.py` (which won't be populated until M8 per its
own docstring). `capability_invocation/extraction.py`'s prompt gave the
model only `Current date/time (UTC): ...` and asked it to produce a
UTC-offset ISO datetime — "12 pm" has no UTC-anchored meaning on its
own, so the model wasn't hallucinating, it was solving an
underspecified problem the only way it could (treating the bare number
as already being in UTC). This came up via a person/architect
discussion proposing a much larger agentic-loop rearchitecture on the
strength of this bug as a "smoking gun" — the rearchitecture claim
wasn't accepted (see that conversation), but the specific bug was real
and is fixed here, narrowly, the same way every other fix in this
record is.

**Fix**:
- `config/default.yaml`: new `user.timezone` key (IANA name,
  `"Asia/Kathmandu"`) — static config, not a UserModel row, since
  Jarvis is single-user and that projection doesn't exist yet anyway.
- `capability_invocation/extraction.py`: new `_resolve_local_now(now,
  user_timezone)` converts the UTC `now` into that zone via stdlib
  `zoneinfo`, failing closed to UTC (with a logged warning, never a
  crash) on an unrecognized/typo'd zone name. `_build_prompt()`/
  `extract_candidate()` both gained a `user_timezone: str = "UTC"`
  parameter (default preserves the exact old behavior for any caller
  that doesn't pass it) and the prompt now shows the user's LOCAL
  date/time and its current offset alongside UTC, with instruction text
  telling the model explicitly that a bare time is local and how to
  convert it.
- `conversation/api.py`: the one real call site now passes
  `user_timezone=load_config().get("user.timezone", "UTC")`, same
  config-read pattern already used for `known_accounts` right above it.

**Not verified against a live calendar-mcp server** — same standing
caveat as every other calendar_ops.py-adjacent change in this record.
This fix is entirely on the prompt/input side (what the model is told),
not the executor side, so that caveat is narrower here than usual: the
actual ISO-datetime-with-offset the model produces still goes through
the exact same validated `start`/`end` path as before.

**Tests**: `tests/test_capability_invocation_extraction.py` — 5 new
(prompt shows both UTC and local time with the correct offset for a
known zone; unknown zone name falls back to UTC without crashing and
without leaking the bad name into the prompt; omitting `user_timezone`
entirely keeps the old UTC-only-anchor prompt text; two direct unit
tests on `_resolve_local_now`). Full suite: `464 passed` before this
fix (the two GitHub Models-retirement-adjacent fixes landed in between,
net effect documented above), `467 passed` after.
