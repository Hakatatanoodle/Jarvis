# V1-M6 Implementation Record — Real Filesystem Navigation

## What shipped
Natural-language navigation and read/write of files within
person-designated allow-listed root folders ("go to Projects, then
jarvis, read the README" / "write these notes to notes.txt"),
dispatched through V1-M4's shared Capability-invocation pipeline
unchanged. Replaces `file_ops.py`'s fixed `data/files/` sandbox
entirely. No delete capability (settled requirement #2). Every write
requires y/N confirmation, unconditionally (settled requirement #3).
Built independently of M5 (Calendar), which is running in parallel in
a separate session per the handover — nothing here assumes M5 exists,
and nothing in M4's shared foundation was changed except the one
additive field described below.

## New files
- `capabilities/primitives/_fs_safety.py` — `resolve_and_authorize()`:
  the one function every path from a user's words to disk must go
  through. Resolves the real, symlink-resolved absolute path
  (`Path.resolve()`, never string concatenation), verifies it's a
  descendant of an allow-listed root, then separately checks it
  against a glob blocklist (checked against every path segment and
  segment-tail, not just the filename, so a blocklisted directory
  component anywhere in the path trips it — `.ssh` inside an allowed
  root is blocked even though the root itself is fine). 13 unit tests
  in `tests/test_fs_safety.py`, no DB needed: relative navigation,
  nested navigation, `..` escaping the root, `..` that resolves back
  inside (allowed), absolute path outside every root, absolute path
  inside a root, a symlink that escapes a root, the root itself,
  blocklisted dotfile, blocklisted directory component mid-path, glob
  match on a key file, a non-blocklisted file passing, and `~`
  expansion in `load_allowed_roots`.
- `capabilities/primitives/fs_ops.py` — `fs.read` (LOW baseline,
  reads a file's content or lists a directory) and `fs.write` (MEDIUM
  baseline, `supports_undo=False`, `force_confirmation=True`).
  Deliberately re-runs `resolve_and_authorize` inside the executors
  themselves, not just in dispatch — defense-in-depth for an Action
  created directly rather than through
  `capability_invocation/dispatch.py` (same shape
  `tests/test_m5_capabilities.py`'s `_make_plan_action` helper already
  uses to bypass the NL pipeline). `configure_for_test()` lets tests
  point it at a `tmp_path` sandbox without touching
  `config/default.yaml`.

## The one shared-file change: `Capability.force_confirmation`
Settled requirement #3 ("every write/overwrite requires y/N
confirmation, unconditionally... not risk-computed") cannot be
satisfied by tuning `baseline_risk_level` — pushing it to HIGH so the
formula lands on CONFIRMATION_REQUIRED is fragile (combined with the
WRITE-type +1 bump, it lands on CRITICAL → DENIED instead, the
opposite of what's needed) and it's still "risk-computed" in spirit,
which the requirement explicitly rules out.

Instead: `contracts/capability.py` gained
`force_confirmation: bool = False` (additive, defaults false, every
existing capability's behavior is byte-for-byte unchanged).
`permission/api.py`'s `check_permission()` checks it FIRST, before
`compute_risk()`'s branches — true short-circuits straight to
CONFIRMATION_REQUIRED, bypassing the ordinal math entirely rather than
feeding into it. This is the one shared-file touch outside new files
this milestone made. **Flagging for the merge**: if M5 also touched
`permission/api.py` or `contracts/capability.py`, this needs a real
three-way look, not a text merge — the change is additive/isolated
(one new field, one `if` at the top of one function) so it should
merge cleanly against most other changes, but check.

## Path resolution happens in dispatch, before the Action exists
`capability_invocation/dispatch.py`'s `resolve_and_dispatch()` now
resolves+authorizes `candidate.path_ref` before
`_persist_decision_plan_action` is ever called — a disallowed or
blocklisted path never becomes an Action, Decision, or Plan row at
all; it's an immediate relay note, same ASK_CLARIFY shape the
goal_ref branch already established. The resolved path is what's
written into the Action's `parameters["path"]` (never the raw
`path_ref`), and it's what the confirmation prompt and relay text show
(`PendingCapabilityConfirmation.reason` includes the real resolved
path string, never the person's shorthand) — this is the direct
implementation of the handover's "give the reply model the right
labeled context" lesson applied to what the PERSON sees before
approving a write.

## Session "current directory" — a module-level global, deliberately
`capability_invocation/dispatch.py` added `_current_directory`
(module-level, `None` until first use, then defaults to the first
allowed root) and `get_current_directory()` /
`_update_current_directory()`. Same shape as `conversation.api.py`'s
`_SESSION_ID` — this system has no multi-tenant session table, one
process is one session, so a module global is the honest
representation of that, not a workaround. Updated only after a
SUCCESSFUL fs action (`bucket == "completed"`) — never on a
clarify/refuse/pending outcome — to the resolved path if it's a
directory, else its parent. Passed into
`capability_invocation/extraction.py`'s prompt as
`current_directory` context, same pattern as `recent_context`: the LLM
combines "then inside that find jarvis" with the shown current
directory into a single `path_ref` string; Python never does
incremental directory-walking itself, it only resolves+authorizes
whatever single path string extraction produced. This keeps the hard
adversarial-safety logic in one deterministic place
(`_fs_safety.py`) rather than spreading it across a stateful
multi-turn walker.

## Read confirmation — resolved: never
Reads never require confirmation, at all, ever — not on first use of a
new root, not otherwise. Reasoning locked in with the person building
this: allow-listed roots are already pre-scoped by the person up
front, the blocklist independently catches the sensitive-file case
regardless of "first time in this root," and a first-use-per-root
confirmation would add session state to track (which roots has this
session "seen") for marginal benefit over what the allow-list +
blocklist already provide. `fs.read`'s `Capability` has no
`force_confirmation` and a LOW baseline; `permission/risk_calculator.py`
is untouched and still applies its normal formula, which further never
produces confirmation for a LOW-baseline READ action anyway.

## `contracts/capability_invocation_candidate.py` / extraction changes
Added `path_ref` (raw, as-stated path text — resolution happens only
in dispatch, per the "never trusted to the model's own judgment" rule)
and `content` (fs.write's literal text, extracted verbatim, never
invented). `capability_invocation/extraction.py`'s prompt/validate
extended in place, same shape M4's docstring said M5/M6 should use
rather than a redesign — new optional JSON fields, re-validated the
same fail-closed way as everything else in that module.

## `_ACTION_TYPE_BY_CAPABILITY`
`fs.read` → `ActionType.READ`, `fs.write` → `ActionType.WRITE`, same
explicit per-capability table `goals.advance` already established (no
generic inference rule).

## Guardrails followed
No M4 bugs or gaps found while building this — nothing left
undocumented. M5 was not touched, read, or assumed to exist; the one
shared-file change (`force_confirmation`) is additive and isolated
enough that it should not collide with plausible M5 work, but is
flagged above for the merge regardless.

## Dogfooding fix (2026-08-31): fs.read's success message discarded the actual result
**Found live, in the person's own terminal.** They asked "can you read
the files that are currently in this directory?" — the NL pipeline
worked correctly end-to-end (extraction → path resolution → Action →
GRANTED → executed → Completed, all in the logs) — but the reply text
said *"I'm not able to read files from your system right now"* and
then invented a fake `/fs.read /path` slash command that doesn't
exist in this CLI.

Root cause: `resolve_and_dispatch`'s `"completed"` branch called
`_handle_action` and got back `(bucket, payload)`, but only ever used
`bucket` — `payload` (a `CapabilityResult` with the actual
`output` — the directory listing or file content) was silently
dropped, and the branch returned a content-free `"Ran fs.read on
{path}."` string. That string becomes the `memory_grounding` fact fed
into `_conversational_reply`'s prompt (see
`conversation/api.py`'s `_conversational_reply`, unchanged/shared) —
told "you just did this" with zero data to back it up, the reply model
had nothing to relay and fabricated something instead. This was never
caught by the existing tests because they only asserted
`result.success is True` / `result.output["content"] == "hello"` on
the `CapabilityResult` directly — none of them checked what
`resolve_and_dispatch` puts in the *executed message string* that
actually reaches the reply model.

**Fix**: the `"completed"` branch now reads `payload.output` and
folds the real result into the executed message — a directory's entry
names for `fs.read` on a directory, the file's text for `fs.read` on a
file (capped at 3000 chars with a truncation note, so a large file
doesn't crowd out the rest of the reply-generation prompt), an
"(empty)" note for an empty directory. `fs.write`'s completed branch
is unaffected (it always goes through confirmation first, per settled
requirement #3, so `"completed"` for `fs.write` is only reached after
`resolve_pending_capability_invocation`'s own separate `Completed
({outcome.output}).` formatting — not this branch at all).

Added 4 regression tests to `tests/test_capability_invocation_dispatch.py`:
directory listing appears in the executed message, file content
appears, large file content is truncated, empty directory says so.
Full suite re-run end-to-end against live Postgres after the fix:
**414 passed** (410 + these 4).

**Not touched, flagged only**: `_conversational_reply`'s prompt
template (`conversation/api.py`) itself doesn't tell the reply model
that capabilities are invoked via natural language, not slash
commands — plausibly part of why it grabbed for a fake `/fs.read`
syntax when it had nothing else to say. That's shared M4/pre-M4
conversation infrastructure, not something this milestone's files own,
so it wasn't touched here per the "don't fix shared-foundation issues
found while building this, document them" guardrail. Worth a look
during the merge or a follow-up, but the concrete bug (missing data)
is what actually caused this instance and is fixed.

## Dogfooding fix #2 (2026-08-31): current-directory default was the
## configured root, not where the process actually runs from
**Found live, second round.** After fix #1, the person set a Mission
and then said "read the cli.py file in the current repo" while
running `python3 cli.py` from `~/Projects/Jarvis/jarvis-M6` (config's
`allowed_roots` is `~/Projects`). The read failed —
`/home/hakatatanoodle/Projects/cli.py does not exist` — because
`get_current_directory()` defaulted to `fs_ops._ALLOWED_ROOTS[0]`
(`~/Projects`, the top of the configured root) rather than the
directory the person was actually sitting in
(`~/Projects/Jarvis/jarvis-M6`). "The current repo" / "this
directory" resolved against the wrong base entirely — a plain wrong
default, not a pipeline problem (path resolution, the allow-list
check, and the retry/failure reporting all worked correctly given the
bad input).

**Fix**: `get_current_directory()` now prefers the real
`Path.cwd()` — the directory Jarvis was actually launched from — as
the session's starting current directory, falling back to the first
allowed root (the old behavior) only if the real cwd isn't itself
under any allowed root at all. This matches what a person actually
means by "current directory"/"current repo" the first time they
mention a filesystem thing in a session, without changing anything
about navigation once a session has explicitly moved somewhere (that
still updates `_current_directory` after every successful fs action,
unchanged from the original design).

Added 2 regression tests to `tests/test_capability_invocation_dispatch.py`:
real cwd under an allowed root wins over root order; falls back to the
first root when cwd is genuinely outside every root. Full suite
re-run: **416 passed**.

## Dogfooding fix #3 (2026-08-31): file-content preview overflowed the reply's 200-token budget, cutting replies off mid-sentence
**Found live, third round.** After fix #2, reading `cli.py` worked —
correct file, correct content — but the reply was cut off mid-docstring:
`"...(architect decision, 202"`. Root cause: `_conversational_reply`
(`conversation/api.py`, shared, untouched) has a hard
`max_tokens=200` for its actual generated reply. Fix #1's content
preview was capped at 3000 chars and phrased as "say so as something
you just did for them" — for a real source file, that invites the
model to quote/reproduce the content, and 3000 chars of source code
cannot fit in a ~150-word reply under any phrasing. The model started
quoting the file and ran out of budget mid-quote.

This is a sizing bug in code this milestone owns
(`capability_invocation/dispatch.py`), not a case for touching the
shared 200-token cap (which plausibly exists for good reason
elsewhere in the conversational path and isn't this milestone's to
change).

**Fix**: the file-content preview folded into the grounding fact is
now capped at 600 chars (down from 3000), and the wording explicitly
tells the model to describe/summarize what the file contains rather
than quote it at length, since the reply has to stay short. Updated
the existing large-file regression test to assert the message
actually fits a small reply budget (`len(executed) < 1000`), not just
"shorter than the raw file." Full suite re-run: **416 passed**
(same count as fix #2 — this changed an existing test's assertions,
not the test count).

## Synced from V1-M5 (2026-08-31): Groq truncation-retry ordering bug in `infra/llm_router.py`
Not something found while building M6 — the person shared M5's
current `infra/llm_router.py` after M5 hit and fixed this, and asked
to sync it into M6 ahead of the eventual merge, since both milestones
depend on the same shared LLM-router file and both are hitting Groq
constantly as the fallback provider. Diffed the two files directly to
confirm scope before touching anything.

**What the diff showed:** `GeminiProvider` (the AQ-format-key /
`x-goog-api-key` header / Interactions-API fix that prompted this
conversation) was already byte-identical between the two branches —
that fix predates both M5 and M6, it's part of the shared M4 baseline
both branches inherited, not an M5-only patch. Nothing to port there.
The actual difference was entirely in `GroqProvider.call()`: M5 found
that the `finish_reason == "length"` truncation-retry block ran
*after* the `if not content: raise LLMUnavailableError(...)` check,
which meant a Groq reasoning model (gpt-oss-20b/120b) that spent its
entire token budget on hidden reasoning tokens — leaving `content`
empty but `finish_reason == "length"` — hit the empty-content raise
before the retry logic ever got a chance to run, even though that's
exactly the scenario the retry exists for. Fix: reordered so the
truncation retry runs first, and the empty-content check runs after,
now only reachable once the retry has already had its shot.

Applied the identical reordering (not a reimplementation — copied the
exact logic and comment content, adding one line noting the sync
source) to my `infra/llm_router.py`. Re-diffed after: the two files'
`GroqProvider.call()` are now logic-identical (a comment-only
difference remains, noting this was a cross-branch sync). Full suite
re-run: **416 passed**, no change in count — this is shared-file
infrastructure, not something with M6-specific tests of its own.

**For the merge**: `infra/llm_router.py` was already going to need a
three-way look regardless (both branches touch it, or at least
depend on it identically), but this sync means M6's copy shouldn't
introduce a conflict on this specific block — it's now the same code
M5 has. Worth double-checking at merge time that neither branch made
further independent changes to this file after this sync point.

## Dogfooding fix #4 (2026-09-01): blanket "you have no capability access" claim contradicted same-turn completed-action facts
**Found live, fourth round, user's own diagnosis.** After fix #3,
reading `cli.py` produced correct content, correct path — but the
reply said *"I don't have the ability to read the file right now, but
from the snippet you shared, it's..."* on the FIRST message of a
fresh session (no history to blame). Root cause, in shared
`conversation/api.py`, not new to M6: `_capability_grounding()` — fed
into every `_conversational_reply()` prompt — was a single
**unconditional** sentence:

> "Capabilities that exist in this system: {names}. None of these are
> available to you in this conversational reply — you are generating
> text only, with no ability to call any capability, save anything, or
> take any action right now."

That fires every turn regardless of what actually happened, directly
contradicting the very next block in the same prompt when a capability
DID execute this turn ("Action just taken this turn... say so as
something you just did for them: {memory_grounding}"). Two flatly
opposed claims in one prompt; the model believed the unconditional
one. This is not fs-specific — every registered capability
(`goals.advance`, M5's `calendar.*`) shares this same prompt function,
so any capability relaying a real result in the same turn it executes
hits the identical contradiction. Round 1's "invented a `/fs.read`
slash command" symptom was this same root cause wearing a different
face — it just happened to look survivable then because there was no
real data being contradicted yet.

**Fix (person's proposed design, implemented as described):**
`_capability_grounding()` no longer makes one blanket claim about all
capabilities — it now takes the turn's actual outcome
(`executed_capability_id` / `pending_capability_id` /
`relay_capability_id`, all `Optional[str]`, computed in `handle()`
from signals already available there — `capability_candidate`,
`capability_executed`, `pending_capability_invocations`,
`capability_relay`) and emits one honest, capability-specific status
line per registered capability: `"JUST EXECUTED this turn"`,
`"awaiting the user's y/N confirmation... not done yet"`, `"invoked
this turn but not finished yet... not something you lack the ability
to do"`, or the honest default, `"available via natural language in
this system, just not invoked this turn"` — never a categorical
"you can't." Threaded through both `_conversational_reply()` (which
already had capability grounding) and `_grounded_reply()` (which,
found in the process, never had `_capability_grounding()` in its
prompt at all — added for the same reason: `needs_reasoning=True`
capability turns deserve the same accurate status, not silence).

Added 2 regression tests to `tests/test_conversation_api.py`: the
blanket phrases (`"no ability to call any capability"`, `"None of
these"`) are asserted absent entirely (not just overridden) when a
capability executes, the executing capability gets `"JUST EXECUTED
this turn"` while an unrelated one gets `"not invoked this turn"`, and
a pending-confirmation capability (`fs.write`) gets the distinct
pending wording, never the executed one. Full suite re-run: **418
passed** (416 + these 2).

**Scope note for the merge:** this changes shared
`conversation/api.py` behavior for every capability, not just M6's.
If M5 is independently hitting/fixing the same blanket-claim
contradiction for a calendar action, the two fixes should converge
easily since this one is additive (new optional params, defaulting to
the old no-context behavior's honest equivalent) rather than a
rewrite — but worth a direct look at merge time regardless, same as
the `infra/llm_router.py` sync above.

## Files touched
(Includes the dogfooding fix above — no new files from it, just
`capability_invocation/dispatch.py` and the added tests.)

**Modified:** `contracts/capability.py`, `permission/api.py`,
`contracts/capability_invocation_candidate.py`,
`capability_invocation/extraction.py`,
`capability_invocation/dispatch.py`, `conversation/api.py`,
`capabilities/bootstrap.py`, `config/default.yaml`,
`tests/test_m5_capabilities.py`.
**New:** `capabilities/primitives/fs_ops.py`,
`capabilities/primitives/_fs_safety.py`, `tests/test_fs_safety.py`,
new fs-specific tests appended to
`tests/test_capability_invocation_dispatch.py`.
**Deleted:** `capabilities/primitives/file_ops.py`.

## Test results
Full suite: `416 passed` (410 at first ship + 4 from dogfooding fix
#1 + 2 from dogfooding fix #2), run against a real Postgres instance (pgvector installed) — not mocked out, consistent with how
this project's DB-backed tests have always been verified. Breakdown of
the fs-specific additions: `tests/test_fs_safety.py` 13/13,
`tests/test_capability_invocation_dispatch.py` fs cases (11
pre-existing M4 + 13 new M6 across the initial ship and the dogfooding
fix — see the file directly), `tests/test_capability_invocation_extraction.py`
7/7 (unchanged, confirms the path_ref/content additions didn't
regress the existing goals.advance extraction path),
`tests/test_m5_capabilities.py` updated for `fs.read`/`fs.write` in
place of `file.read`/`file.write`.
