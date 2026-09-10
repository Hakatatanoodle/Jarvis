# V1-M2 Implementation Record — Natural-Language State Changes / CRUD

Status as of 2026-08-29 (initial implementation 2026-08-25, five
dogfooding revision rounds 2026-08-26 through 2026-08-29). Written for
a cold-start agent picking this up — assumes no prior context from
chat history. If you're that agent, read this fully before touching
code.

Companion docs, in the order they matter: `M2_HANDOVER_PROMPT.md` (the
original brief this milestone was built from), `ARCHITECTURE_ISSUES.md`
(every entry dated 2026-08-25 through 2026-08-29 — the full "what
actually happened" trail this record only summarizes), `V1_PLANNING_INPUT.md`
§3 (where M2 was originally deferred — this milestone is that
question's final answer, after M3 answered it for Memory). This doc is
the condensed version; ARCHITECTURE_ISSUES.md has the real detail
behind every line below.

**Verification standard used — read this before trusting "done":**
every fix below was verified against a real Postgres instance (apt-
installed postgresql-16 + pgvector, all migrations applied), not just
"tests collect." For the five deterministic bugs found via live
dogfooding (not the prompt-wording ones — see below), the same
discipline was used every time: reproduce the bug against the real,
unmodified code first, confirm the new regression test fails against
it, apply the fix, confirm the same test passes, then run the full
suite. **373/373 tests pass for real** (up from 333 at initial
implementation), including every DB-integration test across the whole
pre-existing suite plus this milestone's own new files.

**Important, stated plainly:** several of this milestone's fixes are
LLM prompt-wording changes (routing examples, system-prompt
instructions, guard exemption patterns). For those, "tests pass" means
the prompt text now says the correct thing — it does NOT mean a given
model reliably obeys it. That distinction is called out explicitly in
every relevant ARCHITECTURE_ISSUES.md entry and is not a lesser
standard than the deterministic fixes; it's the honest ceiling of what
a prompt-based fix can be verified to do.

---

## What was built

Mirrors M3's own template exactly, per the original handover brief:
structured extraction (LLM proposes, Python re-validates, fail-closed),
a deterministic policy layer (zero LLM judgment), a single writer
reusing existing validated functions, confirmation UX reusing the
existing y/N shape.

- **`contracts/state_change_candidate.py`** — `StateChangeCandidate` /
  `StateChangeOperation` (create_goal, set_goal_status, set_mission).
  Own file, own enum — not merged into memory's contracts or
  `PolicyOutcome`, since the two domains' outcome sets don't overlap
  and this project keeps domains deliberately separate.
- **`state_change/extraction.py`** — one candidate per message (not a
  list, unlike memory — a Goal/Mission command is a single explicit
  instruction). Takes the real current `list_goals()` titles and active
  Mission title as context so it references existing entities instead
  of inventing them. As of 2026-08-27, also takes the session's recent
  conversation, so a bare follow-up answer to Jarvis's own clarifying
  question ("a Project", "it is a Task") can be resolved by combining
  it with the original request instead of being treated as a new,
  incomplete command.
- **`state_change/policy.py`** — `decide()`: EXECUTE / ASK_CONFIRMATION
  / ASK_CLARIFY / REJECT_INVALID_TRANSITION, entirely deterministic.
  Reuses `goals.api.ALLOWED_TRANSITIONS` (made public specifically for
  this — was `_ALLOWED_TRANSITIONS`) rather than keeping a second copy
  of the transition rules that could drift out of sync. Cancelled/
  Archived transitions always confirm even when unambiguous and valid
  (deterministic risk axis: irreversibility, independent of ambiguity).
  Mission changes always confirm once both title and statement are
  present.
- **`goals.api.create_goal` / `set_status`, `mission.api.set_mission`**
  — the only writers. No new SQL, no parallel write path — the exact
  functions the CLI's `/goal` and `/mission` commands already used and
  already had test coverage for.
- **`conversation/api.py`** — `RouteDecision.state_change_possible`
  (third flag on the same one routing call M3 established for
  `memory_candidate_possible`), `PendingStateChangeConfirmation` /
  `resolve_pending_state_change` (own dataclass, not shared with
  memory's — different payload, different domain).

---

## The five real, deterministic bugs found via live dogfooding (not prompt wording)

Full detail in their own ARCHITECTURE_ISSUES.md entries; summarized
here for scope, in the order found:

1. **2026-08-27, `reasoning/api.py`:** `gather_grounded_evidence`
   looked up goals by `mission.id` (the current Mission VERSION row's
   own id) instead of `mission.identity_id` (the stable id Goal
   actually stores). For a mission's first-ever version these happen
   to be equal, which is exactly why this survived the entire
   pre-existing test suite and predates M2 entirely — M2's mission-
   change flow is just what caused a mission to be superseded, in
   practice, for the first time. Symptom: every goal became invisible
   to reasoning-routed questions the instant a mission was ever
   changed. Fixed; two new direct tests plus an end-to-end one.
2. **2026-08-28, `reasoning/api.py`:** the mission's `statement` field
   was never serialized into evidence at all — only the title was
   (same shape as an already-documented 2026-08-10 bug that added the
   title but never got extended to the statement next to it). Symptom:
   "what's the statement?" answered with the mission's title, verbatim.
   Fixed; one field added, one regression test.
3. **2026-08-29, `reasoning/api.py`:** a goal's real valid next
   statuses were never in evidence either. Symptom, and the most
   consequential bug of this whole milestone: "what can I do with a
   cancelled goal?" got a confidently wrong answer ("we can change its
   status back to Active or Draft" — Cancelled is terminal), and two
   turns later, actually trying it was correctly rejected by
   `state_change/policy.py` reading the same real table — a visible
   self-contradiction from the user's side, even though neither
   individual turn was "lying." Fixed by serializing
   `goals.api.ALLOWED_TRANSITIONS` — the exact table the policy layer
   already enforces — into each goal's evidence. One source of truth
   for both paths now, not two independent ones that could disagree.
4. **`insight/api.py`, disabled not fixed, 2026-08-27:** a structurally
   identical `mission.id` vs `identity_id` bug in the "misaligned
   goals" weekly-report check. NOT a simple fix like #1 — the claim
   it's trying to support needs data (which Mission version was active
   when a goal was created) that doesn't exist anywhere in the schema.
   Turned into a deliberate, tested no-op per explicit instruction from
   the person running this, rather than either left always-wrong or
   silently "fixed" into a check that can never fire. Real fix needs a
   new field on Goal plus a product decision on what "misaligned"
   should even mean — logged, not decided here.
5. **`cli.py`, print ordering + missing grounding, 2026-08-27:** the
   y/N confirmation loop resolved and printed BEFORE the already-
   generated conversational reply, which had been given zero context
   that a confirmation was even happening — producing live,
   user-visible contradictions ("I can't delete goals right now" typed
   directly beneath "-> Updated 'Test' to Cancelled."). Fixed on both
   sides: `_apply_state_change_policy` now produces a `relay_note` for
   the ASK_CONFIRMATION case too (previously only ASK_CLARIFY did), and
   `cli.py` prints the reply before the confirmation loop, not after.

## Three prompt-wording guard fixes (verified as prompt content only — see standard above)

`reasoning/api.py`'s two evidence-accuracy guards
(`_asserts_wrong_status`, `_asserts_wrong_mission`) each needed
extending after the evidence gaps above were fixed — a pattern worth
naming on its own: fixing what evidence the model has access to keeps
exposing older guards that were built around the *previous, poorer*
evidence and never retested against what richer evidence makes
possible.

- **2026-08-27:** `_asserts_wrong_status` rejected an entirely accurate
  sentence ("X and Y are active, while Z is not currently active") —
  it checked whether "active" and a non-active title shared a
  sentence, never whether "active" was negated. Fixed with a negation
  exemption.
- **2026-08-28:** the mission-statement fix (bug #2 above) immediately
  exposed the same blind-spot shape in `_asserts_wrong_mission` — once
  the model could correctly discuss the Mission via its real statement
  instead of only its title, any reply that also separately named a
  goal started tripping "conflation" detection. Fixed by scoping both
  guards to one sentence at a time instead of the whole reply.
- **2026-08-29:** a third, different blind spot in
  `_asserts_wrong_status` — "we can update its status back to Active"
  is a hypothetical offer, not a claim about current status, and no
  negation word is present. Close to unavoidable for exactly the
  questions M2 invites people to ask ("what can I do with X"). Fixed
  with a modal-verb exemption. Deliberately tested and disclosed one
  remaining gap this doesn't cover ("You can see that X is active" —
  a genuine false claim that also contains "can" — still slips
  through); not chased further since it hasn't been observed in real
  traffic.

Separately, `_claims_unbacked_action` (the capability-claim guard, not
one of the two evidence guards above) had its own, unrelated false
positive on 2026-08-28: "based on what I have stored, you're..." — a
correct recall of durable memory — was flagged as an unbacked
persistence claim, because the guard's subject/verb checks run
independently over the whole reply with no grammatical relationship
required. Fixed with a targeted exemption for the "what I have/I've
... V" construction.

And a defense-in-depth pair on 2026-08-29: the plain conversational
path (no evidence access by design) answered a goal-status question
with a false blanket "I don't have the ability to modify goals" when
routing sent it there instead of to reasoning. Routing examples
extended to catch more phrasings of the question, AND
`_CONVERSATIONAL_SYSTEM` now explicitly forbids that specific false
claim even when routing still misses it — two layers rather than
betting on either alone.

---

## Explicitly NOT fixed, flagged instead of guessed at

- `memory/candidate_policy.py`'s own `ASK_CONFIRMATION` path likely has
  the identical grounding gap the 2026-08-27 cli.py fix closed for
  state changes — never hit in this milestone's actual traffic, so
  left alone rather than patched speculatively (ARCHITECTURE_ISSUES.md,
  2026-08-27 entry).
- Whether "delete" should be more transparent about actually meaning
  "Cancelled, not removed" (there is no real delete anywhere in this
  system) — a product question, not a bug, surfaced but not decided
  (ARCHITECTURE_ISSUES.md, 2026-08-27 entry).
- The `insight/api.py` misaligned-goals feature's real fix (a new
  schema field + a product decision on what "misaligned" means) — see
  bug #4 above.
- The guard layer's underlying approach (word-proximity matching with
  no real grammatical understanding) has now needed four separate
  fixes in three days for four genuinely different blind spots. Not a
  reason any single fix was wrong — a reason to weigh this whole
  layer's design if/when the architecture gets revisited, which the
  person running this system was independently already considering as
  of this milestone's close.

## Final state

354 tests at the last "M2 done" declaration (2026-08-27) grew to 373 by
the time dogfooding actually stopped finding new bugs — a difference
worth sitting with: the milestone was declared complete, kept being
tested anyway, and kept finding real, previously-undetected issues each
time. Whether that means the current 373-passing state is actually
done, or just the point where dogfooding happened to stop, is worth
the next agent's honest judgment, not an assumption inherited from this
sentence.
