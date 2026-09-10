# Handover — Jarvis V1-M6: Real Filesystem Navigation

Paste this whole file as your first message in the new chat, and
upload the tar of the current repo alongside it. **Requires V1-M4
(Natural-Language Capability Invocation, the shared foundation) to be
done first** — read `V1_M4_IMPLEMENTATION_RECORD.md` before this. Fully
independent of V1-M5 (Calendar) — build in either order, or in
parallel.

---

## Where things actually stand

`capabilities/primitives/file_ops.py` has real, working `file.read` /
`file.write` capabilities today, hard-sandboxed to one fixed folder
(`data/files/`, inside the Jarvis project itself) with explicit
path-traversal blocking (`_safe_path` rejects any `/`, `\`, or `..` in
the filename). This milestone replaces that with real navigation of
the person's actual filesystem — the explicit request was "go to the
root directory, then inside project find the jarvis project" — a
materially bigger surface than the current design, on the person's
real computer, not a contained sandbox.

**This was discussed carefully during planning, specifically because
of that.** The person running this system was direct about wanting
this to be "big" — real navigation, not a fixed folder — and was
equally direct, once the risk was named plainly, that they wanted real
boundaries designed in rather than trusting the model to behave. The
three decisions below came out of that conversation and should be
treated as settled requirements, not suggestions to reconsider from
scratch:

1. **Roam scope: specific folders named up front, not the whole
   filesystem and not the whole home directory.** An allow-listed set
   of root paths (config-driven, e.g. `~/Projects`), not a hardcoded
   single folder like today, but not unrestricted either. Navigation
   ("go to X, then inside that find Y") happens WITHIN an allowed root;
   a path that resolves outside every allowed root is refused, not
   silently clamped or reinterpreted.
2. **No delete capability in this milestone.** Read and write/create
   only. Deletion is easy to add later once the read/write path has
   real usage behind it; starting without it removes an entire class
   of "oops" from the first version.
3. **Every write/overwrite requires y/N confirmation, unconditionally**
   — not risk-computed, not skippable, regardless of what
   `check_permission()`'s dynamic computation would otherwise say for
   a LOW-baseline-risk write. This is a deliberate override of the
   general pattern the rest of this system uses (where risk is
   computed dynamically) because real filesystem writes on someone's
   actual computer warrant a stricter floor than the existing
   computation was designed around.

---

## The job

Natural-language filesystem navigation and read/write within
person-designated root folders — "go to Projects, then jarvis, read
the README", "write these notes to a file in Projects/jarvis/notes.txt"
— dispatched through V1-M4's shared Capability-invocation pipeline.

---

## Do not design the invocation layer from scratch — same as M5

V1-M4's NL → Capability → `check_permission()` → execute/confirm
pipeline is the entry point. This milestone's real work is: (1) a
safety layer sitting BELOW the Capability, enforcing the allow-listed
roots and no-delete rule regardless of what extraction produces, and
(2) new Capability registrations reflecting the wider (but still
bounded) scope.

## The genuinely new, hard part: path resolution and the allow-list boundary, not entity resolution

Unlike M2 (resolving a goal title) or even M5 (translating NL into
calendar fields), this milestone's hard problem is adversarial-safe
path resolution:
1. **Multi-step conversational navigation.** "Go to the root directory,
   then inside project find the jarvis project" is not one message —
   it may be several turns building up a path, or one message
   describing a multi-level walk. Design how the extraction step
   tracks "current working directory" across a conversation (likely:
   pass the resolved current path as context into extraction, same
   spirit as V1-M2's recent-conversation-context fix for multi-turn
   clarification — read that fix in `state_change/extraction.py`
   before designing this from scratch).
2. **The allow-list boundary must be enforced in Python, deterministically,
   after path resolution, never trusted to the model's own judgment.**
   Resolve the FULL, real, symlink-resolved absolute path
   (`Path.resolve()`, not string concatenation — a naive path join is
   exactly how `../../../etc/passwd`-style traversal defeats a
   folder-name-only check) and verify it is actually a descendant of
   one of the allow-listed roots before any read or write touches disk.
   `file_ops.py`'s existing `_safe_path` is a good precedent for "reject
   suspicious input deterministically", but its specific check (no
   `/`, `\`, `..` in a bare filename) doesn't generalize to real
   multi-segment paths — it needs a real redesign, not an extension.
3. **A blocklist inside the allow-list, for known-sensitive patterns**
   even within an allowed root — `.ssh/`, `.aws/`, `.env` files,
   browser profile directories, credential stores, anything matching
   common secret-file naming conventions. An allowed root being, say,
   `~/Projects` doesn't guarantee nothing sensitive ever ends up in it
   (a `.env` file in a project directory is extremely common) — the
   blocklist check happens in addition to, not instead of, the
   allow-list check.
4. **Reads and writes need different treatment.** A read that touches
   a blocklisted pattern should probably just refuse, quietly and
   clearly. A write is already always-confirmed per the settled
   requirement above, but the confirmation prompt should say the REAL
   resolved path, not the person's shorthand — so a confused
   navigation step is visible to them before they approve it, not
   hidden behind a friendly restatement.

## Confirmation — mostly settled already, one open question

Writes always confirm, unconditionally, per the settled requirement.
The one real remaining question: should reads ALSO ever require
confirmation (e.g., the first time a new root is used in a session, or
never, since read-only access to files the person already explicitly
scoped in is comparatively low-stakes)? Propose an answer and confirm
it rather than assuming either way.

---

## Guardrails to carry forward

Same as M5's handover — read V1-M4's guardrails section in full. One
addition specific to this milestone: the confirmation prompt shown to
the person must show the REAL resolved absolute path, never a
re-summarized or paraphrased one — this is the filesystem-specific
version of the "give the reply model the right labeled context"
lesson, applied to what the PERSON sees before approving a write, not
just what the model is told.

## Files to read, in order

`V1_M4_IMPLEMENTATION_RECORD.md` (once it exists) or
`M4_HANDOVER_PROMPT.md` if M4 isn't done yet — do not start this
milestone before M4 ships. Then: `capabilities/primitives/file_ops.py`
(what's being replaced — read `_safe_path` closely as the precedent for
"reject deterministically", even though it needs redesigning, not
extending), `state_change/extraction.py` (the recent-conversation-
context pattern for multi-turn resolution), `config/default.yaml`
(where the allow-listed roots config would go — this is exactly the
kind of "config edit, not code change" setting this file's own header
comment describes wanting).

## Running in parallel with V1-M5 — read this even though M5 isn't your job

The person building this is running V1-M5 and V1-M6 in two separate
chats at the same time, both starting from the same post-V1-M4 tar,
then merging both results back together afterward (a third party does
the merge, not either of you). That changes one thing about how you
must work here:

**If you find a genuine bug, gap, or design flaw in V1-M4's shared
foundation while building this, do NOT fix it yourself.** Stop, write
down exactly what's wrong and why, and leave M4's files untouched. The
V1-M5 session, working in parallel with no visibility into this chat,
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

Write a concrete plan covering: the exact path-resolution and
allow-list-enforcement algorithm (with test cases for real traversal
attempts, not just happy-path navigation), the sensitive-pattern
blocklist's actual contents, and the read-confirmation open question
above — and get it reviewed before implementing. Given this milestone
touches the person's real computer, treat "reviewed before
implementing" as a harder requirement here than anywhere else in this
project so far, not a formality to move past quickly.
