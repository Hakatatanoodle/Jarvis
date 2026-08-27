# V1-M3 Implementation Record — Memory (User-Controlled)

Status as of 2026-08-18 (three revisions: 2026-08-18, 2026-08-18b, 2026-08-18c). Written for a cold-start agent picking this up
— assumes no prior context from chat history. If you're that agent,
read this fully before touching code.

Companion docs, in the order they matter: `JARVIS_V1_M3_IMPLEMENTATION_INPUT.md`
(why M3 exists, the design agreed before code), `ARCHITECTURE_ISSUES.md`
(two decisions flagged below, still open), `constitution.md` (the
Rule-Based/AI-Based/User-Controlled memory-creation framework this
implements the third leg of). This doc is the "what actually happened"
record.

**Verification standard used — read this before trusting "done":**
This was actually run against a real Postgres in-container (installed
postgresql-16 + pgvector via apt, ran all 17 migrations, ran the whole
suite twice consecutively) after an external architect review correctly
refused to accept "tests collect cleanly" as equivalent to "tests
pass" — those are different claims, and only the first one was true as
of the initial M3 pass. Current state: **281/281 tests pass for real**
(280 pre-existing + 1 new regression test for the "do you know my name"
retrieval bug), including every DB-integration test across
`test_memory_api.py`, `test_conversation_api.py`, `test_reasoning_api.py`,
and the whole rest of the pre-existing suite (M1 through M7). This did surface one real
bug that static checks and unit tests missed —
`list_taxonomy_gaps()` passed asyncpg's native `UUID` object straight
into a `TaxonomyGapProposal(id=...)` field typed `str`, which every
other row-mapping function in `memory/api.py` already guards against
(`str(row["id"])`) and this one didn't, at first. That's the concrete
argument for why "collects cleanly" was never an adequate substitute —
noted here so the next agent doesn't repeat the shortcut.

**2026-08-15 revision, same day, post-dogfooding:** the first pass of
this milestone shipped with a real bug, not just a follow-up idea — see
`ARCHITECTURE_ISSUES.md`'s second 2026-08-15 entry. `memory/extraction.py`
asked a shape question ("is this short and single-clause") instead of a
worth question, and capped extraction at one candidate per message. A
live session surfaced it directly: a long statement of the user's life
goals/values was discarded outright while a throwaway one-liner was
kept. Fixed same day — `extract_candidate()` → `extract_candidates()`
(now returns a list, worth-framed prompt), propagated through what was
then `_process_memory_candidates` and `ConversationOutcome.pending_memories`
(now plural, still plural).

**2026-08-15 revision, second pass, external architect review:** two
more real issues, both fixed the same day — see
`ARCHITECTURE_ISSUES.md`'s two newest entries for full detail:
1. **Latency:** `handle()` was making a routing LLM call AND a separate
   extraction LLM call on every non-greeting message, before any answer
   generation even started — two round-trips of avoidable latency on an
   ordinary message with nothing memory-worthy in it. Fixed by merging
   them: `_needs_reasoning()` → `_route()`, one `complete_json` call
   answering both `needs_reasoning` and a new cheap
   `memory_candidate_possible` pre-filter; the (costlier) `extract_candidates()`
   call now only fires when that flag says it's worth it.
   `_process_memory_candidates` (extraction + policy together) was split
   into `_apply_memory_policy()` (policy only, takes an already-extracted
   list) so `handle()` can gate the extraction call itself.
2. **Taxonomy-gap durability:** a `TAXONOMY_GAP` outcome used to only get
   an ephemeral `log.info()` line. Now persisted via a real table
   (`infra/migrations/0017_taxonomy_gap_proposal.sql`,
   `memory.api.record_taxonomy_gap`/`list_taxonomy_gaps`,
   `/memory gaps` in the CLI) — still not a review/approval workflow
   (deliberately, matching what the review said was fine to defer for
   M3), but no longer a line that can silently rotate out of existence.

**2026-08-16 revision:** a live session showed "I like girls" and
"I don't like girls" stored as two separate, contradictory memories —
title-keyed dedup working correctly, but extraction had no visibility
into existing titles so a correction couldn't land on the same one.
`extract_candidates()` gained an optional `known_memories` param
(title/value/type triples); `handle()` now fetches the Active set and
passes it through, with the prompt instructing exact title+type reuse
on a correction. See `ARCHITECTURE_ISSUES.md`'s 2026-08-16 entry.

**2026-08-17 revision:** further dogfooding surfaced a routing gap —
"what do you know about me?" was classified `needs_reasoning=True` and
sent to Reflection, which only reports a memory *count*, not content —
even though the conversational path already had a correct, built
instruction for answering this exact question from durable memory. Added
an explicit example to `_ROUTING_SYSTEM`. No new plumbing — the answer
path already existed and was already correct; it just wasn't reachable
for this phrasing. See `ARCHITECTURE_ISSUES.md`'s 2026-08-17 entry,
which also documents a related-but-separate issue found in the same
transcript ("how am I doing overall?" producing a thin answer) that was
deliberately NOT fixed here — that's a Reasoning decision-template
richness question, out of scope for a routing fix.

**2026-08-17b revision, same day:** the routing fix immediately exposed
a second, worse bug — the question now correctly reached Conversation,
but `_claims_unbacked_action` blocked the (true) recall answer and
replaced it with `_CANNOT_PERSIST_REPLY`, whose text ("that capability
doesn't exist in the system right now") is flatly false post-M3. Fixed
two things, neither of which touches the guard's detection regex:
`_CONVERSATIONAL_SYSTEM` now steers the model toward present-tense
knowledge phrasing ("You're Yochan") instead of persistence-action
phrasing ("I've noted this") when recalling pre-existing memory — a
prompt fix, not a pattern-matching fix, since true-recall and false-
claim use identical surface words and intent isn't regex-recoverable;
and `_CANNOT_PERSIST_REPLY` itself no longer claims blanket
incapability. See `ARCHITECTURE_ISSUES.md`'s 2026-08-17b entry for why
loosening the verb list itself was considered and rejected (it would
have reopened the original bug this guard exists for).

**2026-08-17c revision, same day — M3 closed out here:** two findings
from continued dogfooding, handled differently on purpose:
1. **Fixed:** the user's known name sat unused in the prompt while
   Jarvis called them "boss" for several turns, until asked twice.
   `_CONVERSATIONAL_SYSTEM` now explicitly prefers the user's known name
   over a generic title. Same category as the other prompt fixes today
   — a nudge, not a guarantee; the guard mechanism itself is untouched.
2. **Deliberately NOT fixed, logged instead:** asking to rename Jarvis
   itself ("Infinity") got captured as an ordinary user-fact Memory,
   but nothing reads it back — `_CONVERSATIONAL_SYSTEM` hardcodes "You
   are Jarvis" as a literal string, so the rename has zero real effect.
   Explicit decision from the person building this (not mine to make):
   Jarvis's own identity should genuinely evolve over time — shaped by
   the user and the work done together — which is real scope for a
   future milestone, not a memory-taxonomy patch. See
   `ARCHITECTURE_ISSUES.md`'s 2026-08-17c entry — start there if you're
   picking up identity work later.

**2026-08-18 revision — the 2026-08-17c "low-impact" call was wrong,
corrected here:** the deferred `jarvis's name` category mismatch and
the same-day "prefer the user's known name" prompt fix collided live —
Jarvis called the user "Infinity" and then denied knowing their real
name, with `user's name: Yochan` sitting active the whole time in the
same undifferentiated memory list. The person building this called
this out directly and correctly: fixing one thing shouldn't break
another, and this interaction should have been checked for in advance,
not dismissed. Two fixes: `_EXTRACTION_SYSTEM` now explicitly excludes
facts about Jarvis itself (its own name/persona) — the general
"unrelated to the user" clause existed but wasn't specific enough to
reliably catch a rename request, since it's phrased in direct address
to Jarvis and superficially resembles a normal explicit-statement
candidate; `_CONVERSATIONAL_SYSTEM` now explicitly tells the model a
"Jarvis's name"-shaped fact is never the user's name, as defense in
depth for data that predates the extraction fix and can't be
retroactively cleaned by code (that's a manual `/memory forget`,
consistent with this project's existing stance against auto-deleting
guessed-at rows). Full detail, including the corrected 2026-08-17c
assessment, in `ARCHITECTURE_ISSUES.md`'s 2026-08-18 entry.

**2026-08-18b revision, same day — the above did NOT work, corrected
here:** live retest showed the 2026-08-18 disambiguation fix failed —
"your name is Infinity" persisted both before AND after the
contaminated row was manually forgotten, the second case being a
distinct, worse bug: the model defending its own earlier wrong answer
from session history rather than re-checking the (by-then-correct)
memory section. Rewrote the block into one explicit priority rule
("What you know about the user" is the sole source of truth, wins over
anything said earlier in the session including by Jarvis itself,
self-correct rather than repeat a prior wrong statement) instead of
appending a fourth patch on an already-dense prompt. **Genuinely
unresolved as of this writing** — this is the first fix in this whole
sequence flagged with an actual "Needs architect decision: yes" in
`ARCHITECTURE_ISSUES.md`'s 2026-08-18b entry, since two consecutive
prompt-only attempts failing on direct observation is a real signal
about the reliability ceiling of steering a fast/cheap model through
wording alone, not just a wording problem to keep iterating on.

**2026-08-18c revision — the ACTUAL root cause found and fixed:** both
prior fixes (2026-08-18 and 2026-08-18b) treated this as a model
phrasing problem and added prompt instructions. Neither worked on direct
retest. Root cause found by inspecting the code: `_format_known_memories()`
in `conversation/api.py` was calling `SimpleMemoryRetriever.retrieve(limit=5)`
— an importance-then-recency-ranked top-5. With 10+ Active memories all
at Medium importance (default), the oldest fact (`user's name`) was
ranked out of the window and NEVER reached the model's context. No prompt
wording can fix a bug where the fact was never shown.

This is the SAME failure mode already fixed once before (2026-08-16 entry)
for `memory/extraction.py`'s `known_memories` param — `_format_known_memories()`
just never got the equivalent fix.

**Fix applied:** `_format_known_memories()` now reads the full Active set
via `list_memories(status=MemoryStatus.ACTIVE)` (ordered by `created_at ASC`,
oldest first) with a generous sanity cap of 50, not 5. Removed the
`_memory_retriever` instance and `SimpleMemoryRetriever` import from
`conversation/api.py` (the retriever class stays in `memory/retrieval.py`).

**Added regression test:** `test_format_known_memories_includes_oldest_fact_when_many_exist`
creates 11 Active memories where `user's name` is the OLDEST, all Medium
importance, and verifies it still appears in the formatted output. A test
with only 1-2 memories would NOT reproduce this bug — it only manifests
once enough other memories exist to push the target out of a small top-N
window.

**Test count update:** 281 tests pass for real (280 pre-existing + 1 new
regression test), all against live Postgres, run consecutively.

V1-M3's core mechanics (extraction, policy, write path, retrieval,
confirmation flow, taxonomy-gap trail, routing) are feature-complete
and hold up under real traffic. The grounding fix for "what's my name"
is now ARCHITECTURAL (retrieval strategy), not prompt-engineering — the
facts actually reach the model.

Everything below reflects the current, fixed state. If you find code or
docs elsewhere still saying `extract_candidate` singular,
`_process_memory_candidates`, or `_needs_reasoning`, that's stale —
those names no longer exist in the code.

---

## 1. What M3 is, in one paragraph

Implements the "User-Controlled (Authoritative)" leg of
`constitution.md`'s three memory-creation categories: an explicit user
statement in ordinary conversation ("My name is Yochan", "I prefer
X") can become a real, persisted `Memory` — through a deterministic
policy gate, not an LLM's unchecked say-so. New: `contracts/memory_candidate.py`,
`memory/extraction.py`, `memory/candidate_policy.py`, `memory/retrieval.py`.
Extended: `memory/api.py` (new `remember_from_candidate`),
`conversation/api.py` (candidate detection wired into `handle()`,
durable-memory retrieval fed into the reply prompt, a confirmation
round-trip for ambiguous candidates), `cli.py` (y/N confirmation loop,
`/memory forget`). No schema migration — `Memory` itself is untouched;
a candidate is transient and never persisted on its own.

Also found, not built: `reasoning/policies.py` already pulled Active
memories into Planning/Reflection evidence before M3 started (V1-M3
acceptance criterion J was structurally already true) — M3 only added
`tests/test_reasoning_api.py` coverage proving it.

---

## 2. Files that exist because of M3

- `contracts/memory_candidate.py` — `MemoryCandidate`, `MemoryScope`
  (durable/session/ambiguous), `Sensitivity` (safe/sensitive). Kept
  separate from `contracts.memory.Memory` on purpose (see file
  docstring) and separate from `contracts/enums.py` (that file is
  shared across every persisted §6 contract; scope/sensitivity are
  candidate-only, no column on `memory`).
- `memory/extraction.py` — `extract_candidates(user_text, known_memories=None)`:
  one `complete_json` call returning a *list* of candidates (0 to
  `_MAX_CANDIDATES`), fully re-validated per-entry in Python (a bad
  entry only drops that entry), fail-closed to `[]` on anything
  malformed/unavailable. Never trusted for the sensitivity/scope
  *decision* — only proposes. Framed around "would knowing this help
  Jarvis understand this person" (worth), not statement shape — see §3
  and the 2026-08-15 revision note above. `known_memories` (added
  2026-08-16, see §3) lets the prompt show existing titles/types so a
  correction/restatement can reuse one exactly instead of drifting into
  a new, non-deduping title.
- `memory/candidate_policy.py` — `decide(candidate) -> PolicyDecision`,
  the only place sensitivity is actually decided
  (`assess_sensitivity`, regex over title/value/raw text — see §3).
  Four outcomes: `WRITE_DURABLE`, `ASK_CONFIRMATION`, `BLOCK_SENSITIVE`,
  `TAXONOMY_GAP`, `NO_OP`.
- `memory/retrieval.py` — `MemoryRetriever` Protocol +
  `SimpleMemoryRetriever` (importance-then-recency, optional substring
  match). Deliberately dumb; pgvector (INF-02) can back a real
  `SemanticMemoryRetriever` later behind the same interface.
- `memory/api.py` — added `remember_from_candidate(candidate,
  source_id)`. Dedup/update key is `(type, lower(title))`: same key on
  an ACTIVE memory updates it (versioned, via the existing
  `_snapshot_and_update`); different title creates a new memory. No
  semantic/fuzzy matching across titles — see §3. Also added
  `record_taxonomy_gap(candidate)` / `list_taxonomy_gaps()` — persists
  `TAXONOMY_GAP` outcomes as real rows (2026-08-15 architect review;
  see §3 and `ARCHITECTURE_ISSUES.md`), backed by
  `infra/migrations/0017_taxonomy_gap_proposal.sql` and
  `contracts/taxonomy_gap_proposal.py` (`TaxonomyGapProposal`).
- `conversation/api.py` — `PendingMemoryConfirmation` dataclass,
  `ConversationOutcome.pending_memories` field (a list — one message
  can yield several), `RouteDecision` + `_route()` (one `complete_json`
  call answering both `needs_reasoning` and `memory_candidate_possible`
  — replaces the old separate `_needs_reasoning()`),
  `_apply_memory_policy()` (policy application over an already-
  extracted candidate list — no LLM call of its own;
  `extract_candidates()` is only invoked by `handle()` when
  `route.memory_candidate_possible` is true), `resolve_pending_memory()`
  (the confirmation callback, still one at a time), a new durable-
  memory section in `_conversational_reply`'s prompt, and one narrow
  exception added to the `_claims_unbacked_action` guard (see §3, "the
  guard that almost broke"). See `ARCHITECTURE_ISSUES.md`'s
  2026-08-15 latency entry for why the call-merging happened.
- `cli.py` — `_handle_request` now checks
  `conv_outcome.pending_memories` (a list) and prompts `input(...) [y/N]`
  per candidate, same shape as the existing Action-confirmation loop
  just below it. `/memory forget <id>` and `/memory gaps` added.
  `_resolve_short_id`'s cold-cache fallback extended to also search
  `list_memories()` (it only checked Goals before — same bug shape as
  the 2026-08-06 fix
  noted in that function, just not yet hit for Memory since
  `/memory forget` didn't exist until now).
- Tests: `tests/test_memory_candidate_policy.py` (10, pure),
  `tests/test_memory_extraction.py` (10, pure — rewritten 2026-08-15 for
  the list-returning API), `tests/test_memory_api.py` (+9, includes
  taxonomy-gap persistence), `tests/test_conversation_api.py` (+17,
  includes the latency-fix call-count tests, updated taxonomy-gap test,
  AND the new regression test for the retrieval bug), `tests/test_reasoning_api.py` (+3) — **all 281 tests in the
  repo pass for real** against a live Postgres, run consecutively
  (see verification note above, top of doc).

---

## 3. Decisions worth understanding before changing anything

**Sensitivity is Python, never the LLM's opinion.** `extract_candidates`
never sets `sensitivity` to anything but the default; `candidate_policy.decide()`
re-derives it from scratch via regex, every time, regardless of what
extraction claimed. This is Decision 3 from the input doc taken
literally: "the validation/policy layer must own this decision."

**Sensitivity detection is a named minimum, not a PII classifier.**
Only credential-shaped keywords (password, API key, secret/access/auth
token, private key, credit card, SSN, bank account, PIN, seed phrase) —
matched against title, value, *and* the raw utterance, so a key hidden
only in the raw text still gets caught. Broader "sensitive personal
information" was deliberately not attempted (false-positive/false-
negative tradeoff too poor for a keyword list). Flagged in
`ARCHITECTURE_ISSUES.md` 2026-08-15.

**Dedup is title-keyed, not semantic.** The input doc explicitly
permits this ("do not invent a universal semantic conflict resolver in
M3"). `memory/extraction.py`'s prompt asks for stable, canonical-style
titles ("user's preferred code editor", not "likes VS Code") precisely
so repeated statements about the same fact naturally produce the same
title and collide in `remember_from_candidate`. A differently-worded
restatement of the same fact will NOT merge — accepted tradeoff, not a
bug.

**...but "accepted tradeoff" turned into a live, visible contradiction
(2026-08-16), so it got a targeted fix.** "I like girls" and "I don't
like girls" ended up as two separate Active memories in a real session
— see `ARCHITECTURE_ISSUES.md`'s 2026-08-16 entry for the full story.
Fix: `extract_candidates()` now takes an optional `known_memories`
param (title, value, type triples); `handle()` fetches the full Active
set and passes it through whenever `memory_candidate_possible` is true,
and the prompt instructs the model to reuse an exact existing title+type
when a message is a correction/update. This is NOT the semantic
resolver the input doc ruled out — it doesn't reconcile two different
titles after the fact, it just gives extraction a fair shot at
generating the SAME title twice for the same fact, which is what the
title-keyed dedup already needed and previously had no way to get
reliably. Doesn't guarantee convergence (model can still drift, list is
capped at `_MAX_KNOWN_MEMORIES_SHOWN` = 20), but meaningfully improves
the odds — see the architecture log entry for exactly what's still not
covered.

**Extraction asks worth, not shape, and returns a list.** See the
2026-08-15 revision note at the top of this doc and
`ARCHITECTURE_ISSUES.md`'s matching entry — this was a real bug (a
stated life-goal/values statement was silently discarded), not a
refinement. If you're tuning this prompt further, keep the framing:
the question is "does this help Jarvis understand the person," not
"does this match a known pattern."

**Routing and memory-plausibility are one LLM call, not two.**
`_route()` returns `RouteDecision(needs_reasoning, memory_candidate_possible)`
from a single `complete_json` call; `extract_candidates()` (the
expensive one) only runs when the second field is true. If you add a
third thing `handle()` needs an LLM's opinion on before it can proceed,
strongly prefer folding it into this same call over adding a fourth
round-trip — that's the whole point of the 2026-08-15 latency fix (see
`ARCHITECTURE_ISSUES.md`). `memory_candidate_possible` fails OPEN
(defaults True) on a bad/missing routing response, unlike
`needs_reasoning` which still fails closed (True, same as always) — the
asymmetry is deliberate, see that field's own comment in
`conversation/api.py`.

**Taxonomy gaps are persisted, not just logged, but still not a
workflow.** `record_taxonomy_gap()`/`list_taxonomy_gaps()` write to a
real table (`infra/migrations/0017_taxonomy_gap_proposal.sql`) so the
trail survives log rotation — required by the 2026-08-15 architect
review. There's still no status/approval column and no path from a
proposal to a real `MemoryType` — that remains a human/architect call,
deliberately out of scope for M3.

**Confirmation reuses the CLI's y/N *pattern*, not the Permission/Action
subsystem.** The real `PermissionCheckResult` machinery is
Action/Plan/Capability-shaped; routing Memory through it would mean
inventing a fake Action, which the input doc's own non-goals rule out.
Full reasoning in `ARCHITECTURE_ISSUES.md` 2026-08-15 — flagged for
architect review, not silently decided.

**The guard that almost broke:** `_claims_unbacked_action` existed in
M1 to catch the LLM falsely claiming "I've saved that" when the
conversational path was, by construction, side-effect-free. M3 makes
that false in exactly one case — a real `WRITE_DURABLE`/
`BLOCK_SENSITIVE` decision this same turn. Fix was one added condition
at the single call site (`and not memory_grounding`), not a loosening
of the detector itself — every other false persistence claim is still
caught exactly as before. If you touch this guard, re-read
`test_conversational_reply_claiming_memory_gets_replaced_with_honesty`
first; it's still the regression test for the original bug and should
still pass unmodified (verified: it does, since that test's message
triggers extraction but real network is unreachable in test env →
`extract_candidates` fails closed to `[]` → `memory_grounding` stays
`None` → guard fires exactly as it did pre-M3).

**Turn IDs generated up front.** `_archive()` gained an optional
`turn_id` param so a memory write can use the same id as its
`source_id` that the `ConversationTurn` row will get — `handle()`
generates one `uuid4()` per non-fast-path turn and threads it through
both. Fast-path greetings and the onboarding message don't bother
(never memory-worthy, no candidate ever extracted for them).

---

## 4. Known open issues — NOT fixed, need a decision or further work

- **Jarvis's own identity/persona isn't configurable — explicitly
  deferred, not an oversight.** A request to rename the assistant no
  longer gets captured as a Memory at all (fixed 2026-08-18 — see
  below), so there's genuinely nowhere for such a request to go right
  now beyond being politely acknowledged and forgotten. Decided
  2026-08-17 to defer building real support for this to a future
  milestone — an evolving-identity subsystem shaped by the user and
  shared work over time, not a memory-taxonomy tweak. See
  `ARCHITECTURE_ISSUES.md`'s 2026-08-17c entry before starting any
  work here.
- **Grounding reliability for "what's my name"-type questions is
  UNCONFIRMED under real traffic, and the first two attempts to fix it
  both failed live — read `ARCHITECTURE_ISSUES.md`'s 2026-08-18b entry
  in full before touching this again.** `_CONVERSATIONAL_SYSTEM` now
  states "What you know about the user" as the sole source of truth,
  explicitly overriding anything Jarvis itself said earlier in the same
  session — but this is prompt-only steering of a fast/cheap model, and
  two prior rounds of narrower instructions on this exact question both
  failed on direct observation. Retest this specifically before
  assuming it's resolved. If it still fails, this is flagged as needing
  an actual decision (accept as a known limitation, vs. reconsider the
  conversational model tier) — not something to keep patching with
  wording tweaks alone.
- **A `jarvis's name` row from before the 2026-08-18 fix may still be
  sitting in an existing database.** Extraction no longer creates new
  ones, but code can't retroactively delete data in someone's live
  Postgres — that's a manual `/memory forget <id>` if one exists.
- **Reflection's decision templates are thin, not memory-integration's
  fault but visible through it.** With 9 memories on file, "how am I
  doing overall?" produced only "0 goal(s) completed, 1 still active, 9
  relevant memories on record" — technically accurate, not actually
  useful. The LLM-enhancement pass that would normally add narrative
  richness was correctly rejected by the hallucination guard
  (`"called a goal 'active' when its real status says otherwise"`) and
  fell back to the bare template. Pre-existing Reasoning-engine
  behavior (predates V1-M3 entirely), newly visible now that memory
  gives Reflection more material it *could* draw on but structurally
  doesn't. Not touched here — see `ARCHITECTURE_ISSUES.md`'s 2026-08-17
  entry for why this is out of scope for a routing-level fix.
- **Taxonomy-gap proposals have no review/approval workflow.**
  `record_taxonomy_gap()` persists them (fixed, was previously an
  ephemeral log line — see §3), and `/memory gaps` lets you read them,
  but nothing ever turns one into a real `MemoryType`. Deliberately
  deferred — matches what the 2026-08-15 architect review said was
  fine to leave for later.
- **No reflective/retrospective extraction pass.** Real-time extraction
  (this milestone) only ever sees one message at a time and is
  structurally bad at catching diffuse, multi-message context — a
  value or pattern that emerges across a conversation rather than being
  stated in one line won't be caught even by the 2026-08-15 worth-based
  rewrite. `HOW_JARVIS_THINKS.md` already names the right subsystem for
  this (Insight Engine — "produces reflections and reviews"); building
  it is new surface area, not a same-file patch, and was deliberately
  scoped out of V1-M3 (see `ARCHITECTURE_ISSUES.md`'s "Deliberately NOT
  done in this pass" entry).
- **`memory_candidate_possible` is an unaudited pre-filter.** It's
  cheap by design and fails open, but nobody has yet measured its false-
  negative rate (cases where it wrongly says "not memory-worthy" and
  extraction never gets a chance to disagree) against real traffic. If
  memory-worthy content starts silently going missing again, check this
  before assuming extraction itself regressed.
- **`/memory` has no way to browse Archived/Forgotten memories** — only
  `list_memories()`'s default (Active). Not requested by M3's scope,
  noted in case it comes up in dogfooding.
- **Sensitivity detection scope** (see §3) — explicitly flagged for
  architect review in `ARCHITECTURE_ISSUES.md`.
- **Confirmation-pattern choice** (see §3) — same, explicitly flagged.

---

## 5. If you're the next agent: start here

1. All 280 tests pass against a real Postgres as of this writing (see
   top of doc) — if you're seeing failures, something changed since,
   not a pre-existing unexecuted gap. Bisect from there.
2. Read the flagged `ARCHITECTURE_ISSUES.md` 2026-08-15 entries before
   extending confirmation, sensitivity, routing, or taxonomy-gap
   behavior — several real open questions there, not resolved ones.
3. `memory/candidate_policy.py` and `memory/extraction.py` are the
   safest files to extend (pure logic, actually tested here). Anything
   touching `conversation/api.py`'s guard interactions or `_route()`
   deserves the same care the §3 notes above describe — that function
   in particular now carries two callers' worth of correctness
   assumptions in one LLM call.
4. If you add a case where `handle()` needs another LLM judgment before
   it can proceed, check whether it can fold into `_route()`'s existing
   call before adding a new round-trip — see the latency-fix note in
   §3.
