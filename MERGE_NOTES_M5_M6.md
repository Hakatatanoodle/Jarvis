# Merge notes — V1-M5 (Calendar) + V1-M6 (Filesystem)

Merged from three tars: `jarvis_v1_m4_complete` (common ancestor),
`jarvis_v1_m5_FINAL`, `jarvis_v1_m6_complete`. Started from M6's tree
(already fully dogfooded — see V1_M6_IMPLEMENTATION_RECORD.md's four
fix rounds) and applied M5's changes on top, reconciling every file
both branches touched by hand rather than a blind text merge. Full
suite passes end-to-end against a live Postgres: **443 passed, 0
failed**.

## Files taken wholesale from one branch (no overlap, no reconciliation needed)

From M5: `infra/mcp_client.py`, `capabilities/primitives/calendar_ops.py`,
`tests/test_mcp_client.py`, `data/google_calendar/.gitkeep`,
`docker-compose.yml`, `.gitignore`, `requirements.txt`,
`V1_M5_IMPLEMENTATION_RECORD.md`, `tests/test_capability_invocation_extraction.py`
(M6 never touched this file at all — M5's version replaces it wholesale).

From M6 (kept as-is, M5 never touched these): `capabilities/bootstrap.py`,
`capabilities/primitives/_fs_safety.py`, `capabilities/primitives/fs_ops.py`,
`contracts/capability.py` (`force_confirmation` field),
`permission/api.py` (force_confirmation check), `tests/test_fs_safety.py`,
`V1_M6_IMPLEMENTATION_RECORD.md`. `capabilities/primitives/file_ops.py`
stays deleted (M6 replaced it; confirmed no remaining references).

## Files both branches touched — reconciled by hand

- **`contracts/capability_invocation_candidate.py`** — additive fields
  from both: M6's `path_ref`/`content`, M5's `title`/`start`/`end`/
  `account_ref`/`resolved_account`/`account_match_count`. No overlap.

- **`capability_invocation/extraction.py`** — merged the system
  prompt, JSON schema, `_validate()`, and `extract_candidate()`
  signature so both fs and calendar fields extract correctly in the
  same LLM call. `_build_prompt()`'s signature is
  `(user_text, capabilities, known_goals, known_accounts, now,
  recent_context=None, current_directory=None)` — M5's two new
  required-shape params placed before M6's two optional ones; no
  caller uses positional args past `known_goals`, so this was a safe
  ordering choice, not a forced one.

- **`capability_invocation/dispatch.py`** — the biggest reconciliation.
  Combined M6's fs dispatch branch (path resolution, CWD tracking,
  the fs.read/fs.write completed-message construction from M6's
  dogfooding fixes #1/#3) with M5's calendar dispatch branch (account/
  time-range clarify gates, parameter assembly, `_completed_detail()`).
  **Notable: both branches independently found and fixed the same
  class of bug** — a completed capability's real result data being
  thrown away instead of relayed to the reply model (M6's dogfooding
  fix #1 for fs.read; M5's 2026-09-06 fix, `_completed_detail()`, for
  calendar.read_events/calendar.create_event). Both fixes are now
  present side by side in the same "completed" branch, each scoped to
  its own capabilities, exactly as each branch originally intended.

- **`config/default.yaml`** — both branches inserted a new top-level
  block (`filesystem:` from M6, `calendar:` from M5) at almost the same
  location; both kept, no actual conflict.

- **`conversation/api.py`** — M5's routing-prompt calendar-vs-goal
  disambiguation wording and `known_accounts=load_config()...` wiring
  added alongside M6's `_capability_grounding()` per-capability-status
  fix (dogfooding fix #4). These touch different sections of the file
  and don't interact — confirmed **M5 never had the blanket
  "you have no ability to do anything" bug fixed on its own side**, so
  this merge is the first point M5's calendar capabilities actually
  benefit from that fix too (a calendar.read_events completing and
  relaying real events in the same turn would have hit the identical
  contradiction fs.read did, unfixed, prior to this merge).

- **`tests/test_capability_invocation_dispatch.py`** — M5's calendar
  clarify-gate and `_completed_detail()` tests appended after M6's fs
  tests; independent functions, no shared lines.

- **`tests/test_m5_capabilities.py`** — the one genuine single-line
  textual conflict in the whole merge: `test_five_primitives_registered`'s
  `ids == {...}` assertion. M5 left it as `{"file.read", "file.write",
  ...}` (never needed to touch it), M6 changed it to `{"fs.read",
  "fs.write", ...}` (file_ops.py no longer exists). Kept M6's version —
  the only one still correct. M5's calendar test updates (mocking
  `call_calendar_tool`, two new tests) applied on top of that.

## One pre-existing test broken as a side effect, not a merge conflict

`tests/test_m6_orchestrator.py::test_confirmation_required_action_pauses_and_resumes`
predates both M5 and M6 (present, byte-identical, in all three tars)
and used `calendar.create_event` purely as an arbitrary stand-in for
"some registered capability with higher-than-LOW risk," to exercise
Orchestrator's own confirm/resume gating — back when that capability
had a network-free placeholder implementation. M5's real
calendar-mcp implementation made this test try to reach an actual
`127.0.0.1:3000` server that doesn't exist in this (or most CI)
environments, and it failed with a connection error. Fixed by mocking
`calendar_ops.call_calendar_tool`, same pattern M5's own calendar
tests already use — the test's original intent (prove Orchestrator's
gating, not calendar-mcp's) is unchanged.

## Runtime note, not a source concern
M5's calendar-mcp Docker service is flagged in `docker-compose.yml` as
**known broken** as of 2026-09-05 (an upstream SDK regression) — the
tested path is running the server natively (`node build/index.js`),
per that file's own comment and `V1_M5_IMPLEMENTATION_RECORD.md`. Not
something this merge can or should fix; it's an external dependency's
bug, not Jarvis code.

## Test results
**443 passed, 0 failed** — full suite, live Postgres + pgvector, `mcp`
package installed per `requirements.txt`'s pin (`mcp>=1.9,<3.0`;
resolved `mcp.client.streamable_http.streamable_http_client` cleanly
in this environment, the newer of the two spellings
`infra/mcp_client.py`'s docstring flags as unverified).
