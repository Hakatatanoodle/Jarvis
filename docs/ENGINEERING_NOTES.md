# Engineering Notes

Standing practices layered on top of §8's coding standards, added when
real feedback surfaces a gap the implementation prompt didn't cover.
These aren't §6 contract decisions — `ARCHITECTURE_ISSUES.md` is for
those. This file is for "how we write the code" rules.

---

## SQL files contain structure, not business rules (added 2026-08-02, architect feedback)

`infra/migrations/*.sql` may only contain:
- Table/column definitions and types
- `PRIMARY KEY`
- `FOREIGN KEY` (referential integrity is a genuine database concern, not
  business logic — keep these)
- `NOT NULL`

`infra/migrations/*.sql` may **not** contain:
- `CHECK` constraints encoding a business rule (enum-value validation,
  non-empty-string rules, "can't be its own parent," etc.) — these belong
  in `contracts/*.py` (pydantic validators) and are enforced again, if
  needed, in the relevant `<subsystem>/api.py` before any SQL runs.
- Unique indexes / constraints that encode a domain invariant (e.g. "only
  one active Mission") rather than a genuine key uniqueness requirement.

Why: every subsystem's `api.py` is already the single writer for its
tables (RFC-014's interface boundary). If a business rule also lives in
SQL, there are now two places that can enforce it, drift out of sync, or
disagree — this is the exact failure mode
`Jarvis_Contract_Review_v1.md` already caught once with RFC-015's stale
state-machine diagrams. One rule, one place.

**The one real exception to watch for:** invariants that are
concurrency-sensitive (a plain "check then write" in application code has
a race window under concurrent writers) still need *something* stronger
than a Python `if`, they just don't need it in SQL. Use a Postgres
advisory lock (`pg_advisory_xact_lock`) scoped to the transaction inside
the repository function instead — see `mission/api.py`'s `set_mission()`
for the pattern. That keeps the guarantee in the repository layer without
smuggling it back into a table constraint.

Retrofitted into M1 via `infra/migrations/0004_move_business_rules_to_repository_layer.sql`
after the fact — flagging here so M2 onward starts from this rule instead
of needing another retrofit.

---

## `tests/conftest.py`'s `clean_db` TRUNCATE list needs updating every time a table is added (found in M2)

Cost about twenty minutes of debugging in M2: added the `memory` and
`memory_history` tables, wrote tests for them, and got bizarre
version-number mismatches that had nothing to do with the actual code —
the `clean_db` fixture's `TRUNCATE` statement still only listed the M1
tables (`goal_history, goal, mission`), so leftover rows from earlier ad
hoc debugging were silently leaking across tests. The actual bug (a
hardcoded lowercase `'active'` string literal in a query, when
`MemoryStatus.ACTIVE.value` is `"Active"`) was masked by this for a
while, since the symptom looked like a merge-logic problem, not a test-
isolation problem.

**Rule going forward:** any migration that adds a table gets a matching
one-line addition to `clean_db`'s `TRUNCATE` list in the same commit, not
as an afterthought. Worth double-checking at the start of every milestone
that adds storage.

---

## Be careful with invariants I add beyond what the spec actually states (found in M3)

M0 added a validator to `contracts/decision.py` requiring at least one
`context_item_id` — reasonable-looking at the time, and I even labeled it
"carried forward from RFC-007's validation rule" rather than pretending
it came from the canonical §6.5. It turned out to be wrong: M3's real
usage produced a legitimate zero-ContextItem Decision ("no active Goals
exist yet"), and the validator blocked it outright.

Nothing bad happened here — the test suite caught it immediately, the fix
was small, and it's logged in `ARCHITECTURE_ISSUES.md` like any other
deviation. But it's worth naming the pattern: an invariant that looks
obviously correct in isolation, before any real usage exists to test it
against, is exactly the kind of thing that's cheap to get wrong. When I
add a constraint the canonical contract doesn't literally state, I should
say so explicitly in the code (already do) *and* expect to revisit it
once the subsystem that actually exercises it gets built, rather than
treating "the tests pass at the time I wrote them" as confirmation it's
right.
