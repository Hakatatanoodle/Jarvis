# Handover — Jarvis V1-M5: Real Google Calendar via MCP

Paste this whole file as your first message in the new chat, and
upload the tar of the current repo alongside it. **Requires V1-M4
(Natural-Language Capability Invocation, the shared foundation) to be
done first** — read `V1_M4_IMPLEMENTATION_RECORD.md` before this.

---

## Where things actually stand

`capabilities/primitives/calendar_ops.py` has real, working
`calendar.read_events` / `calendar.create_event` capabilities today —
but they read/write a `calendar_event` table in Jarvis's own Postgres
database. Nothing about that is connected to any calendar the person
actually checks day to day. They use **Google Calendar**. This
milestone replaces the local-only calendar capabilities with ones
backed by their real Google Calendar, and wires natural-language
invocation to them via V1-M4's foundation.

**This is genuinely new territory for this codebase — there is no
existing MCP client code anywhere in this repo** (`grep -rl mcp
--include=*.py .` returns nothing). Don't expect a "reuse this
existing pattern" shortcut for the protocol/client layer the way M2
and M4 could reuse each other — that shortcut exists for the
Capability/confirmation layer (V1-M4's dispatcher), not for how Jarvis
actually talks to Google Calendar.

**Two real MCP server options were surfaced during planning, neither
independently verified as production-ready by anyone on this
project yet:**
- Google's own official server (`calendarmcp.googleapis.com`) — first-
  party, OAuth 2.0, "inherits the same permissions as the user." Very
  recently launched as of this milestone's planning (documentation
  dated within the prior few weeks) — its maturity in practice is
  unknown, not assumed good or bad.
- A community server (`github.com/nspady/google-calendar-mcp`) — OAuth-
  based, multi-account support, more established/documented in public
  use. The planning conversation leaned toward starting here
  specifically because it's had more real-world use, not because it's
  definitively better — confirm this is still the right call before
  committing, since MCP server options change fast.

Whichever is chosen, Jarvis needs actual MCP **client** code — the
official Python `mcp` package is the natural starting point — since
Jarvis is a standalone Python app, not a platform (like Claude Desktop)
that speaks MCP for you already.

---

## The job

Real Google Calendar access via natural language: "what's on my
calendar this week", "add a lunch event tomorrow at noon" — actually
reading from and writing to the person's real Google Calendar, not
Jarvis's internal table. Routed and dispatched through V1-M4's shared
Capability-invocation pipeline, exactly like `goals.advance` was in
that milestone — this milestone adds a new Capability (or replaces the
existing `calendar.*` ones), not a new invocation pathway.

---

## Do not design the invocation layer from scratch — it's V1-M4's job, already done by the time you start this

The NL → Capability → `check_permission()` → execute/confirm pipeline
is V1-M4's deliverable. This milestone's actual new work is narrower
than it might look:
1. An MCP client that authenticates with Google Calendar (OAuth) and
   can call the chosen server's tools (list events, create event, and
   whatever else the server exposes — check its actual tool list,
   don't assume it matches the old local capability's exact shape).
2. New Capability registrations (`capabilities/primitives/calendar_ops.py`,
   or a new file if the shape changes enough to warrant it) whose
   executor calls the MCP client instead of the local Postgres table.
   Risk levels, required permissions, and `supports_undo` need
   re-evaluating for real Google Calendar writes — the old
   `calendar.create_event`'s MEDIUM-baseline-computed-to-HIGH
   confirmation-required behavior was calibrated for a local, low-
   stakes table; a REAL calendar write reaching someone else's actual
   schedule (if they share calendars, invite attendees, etc.) may
   warrant reconsidering that computation, not just inheriting it
   unchanged.
3. Wiring the new/replaced capability into V1-M4's extraction context
   (`list_capabilities()` output) so it's discoverable the same way
   `goals.advance` already is.

## The genuinely new, hard part: OAuth, credential storage, and token lifecycle

Nothing in this codebase has ever stored a third-party OAuth credential
before. This needs explicit decisions, not assumptions:
- Where do refresh tokens live? (`config/default.yaml` is
  human-readable/versioned per its own header comment — NOT an
  appropriate place for a live credential. A new, gitignored local
  file or the OS keychain are the realistic options; decide explicitly
  and document the choice, matching this project's "no code-embedded
  config" discipline in spirit.)
- What happens when a token expires mid-conversation? The community
  server's own docs note test-mode tokens expire after 7 days — the
  failure mode (a stale/expired token) needs a clear, honest user-
  facing message, not a confusing generic error.
- Multi-account: does this person have only one Google account to
  connect, or could they want work + personal? Confirm before building
  — it changes the Capability parameter shape (an account selector) if
  yes.

## Confirmation-risk tiers — re-derive, don't inherit

`check_permission()`'s dynamic computation still applies, but the
INPUTS (baseline risk level, required permissions) for a REAL Google
Calendar write are a genuine new judgment call, not a carry-over from
the local-table version. Propose specific levels and get them
confirmed before implementing, same discipline as every other
milestone here.

---

## Guardrails to carry forward

Same three carried into V1-M4's own handover (routing stays a single
combined call — this is a fifth flag or folded into M4's fourth one,
decide which reads more clearly once M4 exists; give the reply model
labeled context, never generic; re-check existing guards whenever
evidence changes) — read V1-M4's handover doc's own guardrails section
in full rather than having it re-derived here.

## Files to read, in order

`V1_M4_IMPLEMENTATION_RECORD.md` (once it exists) or
`M4_HANDOVER_PROMPT.md` if M4 isn't done yet — do not start this
milestone before M4 ships. Then: `capabilities/primitives/calendar_ops.py`
(what's being replaced, and why the risk-level comment in its
docstring won't directly transfer), `contracts/capability.py`,
`config/default.yaml` (existing config conventions for where new
settings like an MCP server endpoint would go), and whichever MCP
server's own documentation is chosen.

## Running in parallel with V1-M6 — read this even though M6 isn't your job

The person building this is running V1-M5 and V1-M6 in two separate
chats at the same time, both starting from the same post-V1-M4 tar,
then merging both results back together afterward (a third party does
the merge, not either of you). That changes one thing about how you
must work here:

**If you find a genuine bug, gap, or design flaw in V1-M4's shared
foundation while building this, do NOT fix it yourself.** Stop, write
down exactly what's wrong and why, and leave M4's files untouched. The
V1-M6 session, working in parallel with no visibility into this chat,
may hit the same rough edge and "fix" it differently — two incompatible
versions of shared foundation code is a design-reconciliation problem
for whoever merges these, not a text-merge problem, and it's the one
risk that can't be caught by re-running tests after the fact. A
documented, unfixed gap in M4 is far easier to reconcile than two
divergent fixes to it.

**Keep a running list of every file you touched**, including new files
created, at the top of your final handoff summary. This is the single
biggest thing that makes a three-way merge fast instead of slow.

## Before writing any code

Confirm which MCP server, the credential-storage decision, the
multi-account question, and the re-derived risk levels — all explicitly,
all before implementing. This milestone has more genuinely open,
non-precedented decisions than any V1 milestone so far; resist the
urge to assume defaults for any of them.
