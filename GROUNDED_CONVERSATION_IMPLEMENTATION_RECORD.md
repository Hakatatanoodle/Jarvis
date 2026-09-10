# Grounded Conversation — Implementation Record

**Status: STABLE / CLOSED as of 2026-08-24** — five revisions across
two days, then confirmed holding under real traffic (see §0 below).
Written for a cold-start agent picking this up
— assumes no prior context from chat history. Read this fully before
touching `conversation/api.py`, `reasoning/api.py`, or `cli.py`'s
`_handle_request`.

Companion docs: `ARCHITECTURE_ISSUES.md`'s 2026-08-23 entry (the design
rationale in full — this doc is the "what actually happened" record),
`V1_M3_IMPLEMENTATION_RECORD.md` (the Memory milestone this follows and
reuses conventions from), `HOW_JARVIS_THINKS.md` (the Observe →
Understand → Decide → Plan → Act loop this change more faithfully
implements than what it replaced).

**Verification standard used:** run for real against a live Postgres
instance, twice consecutively. **309/309 tests pass.** Not just
collected — actually executed. This is the same standard every fix in
`ARCHITECTURE_ISSUES.md` since 2026-08-15 has held to; if you're adding
to this codebase and only ran `pytest --collect-only`, you have not
verified anything.

---

## 0. Closing status (2026-08-24)

After five same-day-through-next-day revisions (guard false positives,
truncation, Markdown formatting — all fixed, see §3/§4 history below),
a further live session ran cleanly: a genuine multi-step exam-plan
answer came back complete (no truncation) and correctly plain-text (no
Markdown), confirming both the 2026-08-24 and 2026-08-24b fixes hold
under real traffic, not just their own tests. One cosmetic issue found
in that same session and deliberately left open, low priority — see
`ARCHITECTURE_ISSUES.md`'s 2026-08-24c entry: reply tone (not facts)
can bleed from one turn into an unrelated next one (a "goodnight"
sign-off carried into a following, unrelated question). No wrong data,
no hallucination — just an odd tonal artifact. This feature is
considered stable; further work on it should start from a new,
specific observed problem, not a general revisit.

---

## 1. What changed, in one paragraph

Any conversational message that needs real grounding (goals, mission,
memory) used to get forced through `orchestrator.intent.detect_intent()`
into exactly one of two fixed buckets (Planning/Reflection), each
producing a `Decision` from a hardcoded Python f-string template, with
an LLM enhancement step that almost always failed one of six gates and
fell back to the template — and every Planning decision silently fired
a real `goals.advance` Action for every visible goal, every time. That
entire path (`orchestrator.api.run_request()`) is now bypassed for
ordinary conversation. `conversation.api._grounded_reply()` replaces
it: same evidence-gathering, same honesty guards, but a genuine
free-form answer instead of a template, with conversation history
included so follow-ups work, and no Plan/Action ever auto-fires from
just talking. Full rationale, including the exact request that
prompted it, in `ARCHITECTURE_ISSUES.md`'s 2026-08-23 entry.

## 2. Files touched

- `reasoning/api.py` — added ONE new function,
  `gather_grounded_evidence(mission)`. Nothing else in this file
  changed. `reason()`, `_DECISION_BUILDERS`, both template builders,
  `_enhance_with_llm`, and every guard function (`_looks_garbled`,
  `_asserts_unsupported_relationship`, `_asserts_wrong_mission`,
  `_asserts_wrong_status`) are untouched, still fully real and tested.
- `reasoning/policies.py` — untouched. `gather_grounded_evidence` calls
  `reflection_policy` directly (imported, not duplicated).
- `conversation/api.py` — module docstring rewritten to describe the
  new reality. `ConversationOutcome.workflow` field removed (nothing
  sets it anymore). `handle()`'s `if route.needs_reasoning:` branch now
  calls `_grounded_reply()` instead of `run_request()`. New functions:
  `_grounded_reply()`, `_render_evidence_plainly()`,
  `_persist_grounded_decision()`, `_GROUNDED_SYSTEM` prompt constant.
  Imports: `run_request`/`WorkflowResult` removed, added
  `gather_grounded_evidence` and the four guard functions from
  `reasoning.api`, added `Decision`/`json`/`datetime` for persistence.
- `cli.py` — `_handle_request`'s Plan/Action rendering and confirmation
  loop removed (dead code — could never execute once `ConversationOutcome`
  stopped carrying `WorkflowResult`). Both routing outcomes now render
  identically (`print(response_text)`), so the branching collapsed to
  one line. `resume_action` import removed (now unused in this file;
  the underlying function in `orchestrator/api.py` is untouched).
- `orchestrator/api.py`, `orchestrator/intent.py` — **completely
  untouched.** `run_request()`, `resume_action()`, `detect_intent()`
  all still exist, still work, still have their own passing tests. They
  are simply no longer called from the CLI's normal flow — confirmed via
  a full-repo grep before removing the only real call site
  (`conversation/api.py`'s old reasoning branch) that they have no
  other caller outside `tests/`.
- Tests: `tests/test_conversation_api.py` — two existing tests rewritten
  (`test_greeting_never_reaches_reasoning`,
  `test_planning_question_still_routes_to_reasoning` — both were
  asserting on the now-removed `.workflow` field or the now-unused
  `run_request` mock target), seven new tests added covering the actual
  new mechanism: LLM answer used when it passes guards, fallback when a
  guard rejects an invented relationship, no Plan/Action ever created,
  a Decision persisted with `requires_plan=False`, decision history
  labeled `intent="Conversation"`, recent-conversation context reaching
  the follow-up prompt, and the plain-evidence fallback when the LLM is
  genuinely unavailable.

## 3. Decisions worth understanding before changing anything

**Why a new function instead of modifying `reason()`.** `reason()` and
everything it calls (`_DECISION_BUILDERS`, `RETRIEVAL_POLICIES`,
`_enhance_with_llm`) is real, tested, working infrastructure that might
still matter later — e.g. a future capability that genuinely needs a
structured, action-triggering Decision rather than a conversational
answer. Touching it to serve this new purpose would have risked the
existing Planning/Reflection test suite for no reason; adding one new
function that reuses its pieces (`reflection_policy`, `score_and_rank`,
`apply_budget`, `_build_structured_evidence`) costs nothing and keeps
both paths independently correct.

**Why `reflection_policy` and not a new unified policy, and not
`planning_policy`.** `planning_policy` excludes Completed/Archived/
Cancelled goals — fine for a narrow "what's active" question, wrong for
a free-form answer that might legitimately need to discuss finished
work too ("how did the last project go" is exactly as plausible a
conversational ask as "what should I do today", and there's
deliberately no intent classifier upstream anymore to route between
them). `reflection_policy`'s superset (every status, weighted by
relevance) is the correct one for something that has to handle any
shape of goal-related question without pre-committing to which subset
of data is "the right one" for a hardcoded category — which is the
entire point of this change.

**Why the same four guard functions, imported directly rather than
reimplemented.** They're each named after a real incident that already
happened once (read their docstrings in `reasoning/api.py`) — an
invented goal relationship, a Goal mislabeled as the Mission, a status
claimed that doesn't match reality. Nothing about wanting a richer,
more conversational answer means those guarantees should weaken. They
were written to check text against structured evidence, which works
identically whether the text is a two-sentence template enhancement or
a full free-form paragraph — no adaptation needed, just reuse.

**Why the plain-evidence fallback is deterministic text, not another
LLM call or a retry.** A retry-on-failure just delays the same set of
failure modes; a second LLM call to "clean up" the first one is a
seventh gate on top of the existing four, not a fix. `_render_evidence_plainly()`
is built directly from the same `context_items` the guards already
checked against — it lists real goal titles and statuses from actual
Pydantic objects, so it structurally cannot be inaccurate the way
generated text can. This is the same principle the old rigid template
guaranteed; the new path just isn't forced into that shape for the
normal case.

**Why a `Decision` still gets persisted for every grounded reply.**
Not requested — added because removing it would have silently broken
`/decision` history for exactly the category of question people ask it
about most, and because `PROJECT.md`'s explainability principle
("Jarvis should always be able to explain why it made a decision, what
information it used") doesn't stop applying just because the output
format changed. `intent="Conversation"` is a new free-text value (the
`decision.intent` DB column has no CHECK constraint) — distinguishes
these rows from `reason()`'s still-possible Planning/Reflection ones in
`/decision` output without needing a schema change.

**Why Plan/Action auto-firing was removed, not made conditional.**
Discussed explicitly with the person building this before writing any
code (see the chat, and `ARCHITECTURE_ISSUES.md`'s entry). The old
behavior — every casual planning-adjacent question silently creating a
real Action per visible goal — was a genuine surprise, not a deliberate
design choice anyone had signed off on; it existed because
`_template_planning_decision` unconditionally set `requires_plan=True`.
The new default is off; if a real need for conversation to trigger an
Action resurfaces, that should be a deliberate, visible capability
invocation, not an automatic side effect of asking a question.

## 4. Known gaps — not fixed here, worth knowing about

**2026-08-23b same-day revision:** the first live retest immediately
surfaced two bugs, both fixed same day — see `ARCHITECTURE_ISSUES.md`'s
2026-08-23b entry. (1) Free-form output defaulted to Markdown
formatting (`###`, `**bold**`, `---`) that just shows as literal
symbols in a raw terminal `print()` — structurally impossible with the
old Python-f-string templates, a direct consequence of this redesign.
Fixed with an explicit plain-text-terminal instruction in both
`_GROUNDED_SYSTEM` and `_REAL_INTERFACE`. (2) `max_tokens=500` (picked
somewhat arbitrarily when this was built) cut off genuine multi-step
answers like a full daily schedule mid-sentence, twice in one session —
raised to 1200. Both are prompt-only mitigations, not guarantees — same
honest limitation as everything else in this project's history of
prompt fixes.

**2026-08-23c same-day revision — a real, serious one, not cosmetic:**
the second live retest hit 3-for-3 rejections by the reused honesty
guards, every ordinary question falling back to the bare
plain-evidence rendering. Root cause: `_asserts_wrong_status` and
`_asserts_unsupported_relationship` (both reused unmodified from
`reasoning/api.py`) checked whole-document co-occurrence, not whether
the flagged words were actually predicated of each other — harmless
against the old rigid template's terse two-sentence output, essentially
guaranteed to false-positive against this redesign's genuinely
multi-sentence answers whenever exactly one goal is Active alongside
any non-Active ones (an entirely ordinary shape). Fixed to require
same-sentence co-occurrence in both guards — full detail, including why
this doesn't weaken detection of the guards' own originally-reported
bugs, in `ARCHITECTURE_ISSUES.md`'s 2026-08-23c entry. Also fixed:
`_grounded_reply`'s guard check was a single collapsed `if (A or B or C
or D)` with one generic log line, unlike `_enhance_with_llm`'s
per-guard logging — made this bug slower to diagnose than it should
have been; split into four branches with matching specific messages so
it doesn't happen again.

**2026-08-24 revision, next day — a different layer entirely:** three
of four grounded replies in one live session cut off mid-sentence,
despite `max_tokens` already having been raised the day before (that
fix was a guess, unverifiable at the time). Traced to the actual gap:
no provider adapter in `infra/llm_router.py` had ever surfaced whether
a response was truncated — `complete()` and everything above it only
ever saw bare text. Fixed at the correct layer: each provider now
detects real truncation (Groq/OpenRouter: the standard `finish_reason
== "length"` field; Gemini: a mechanical sentence-completeness
heuristic, since this codebase's own comments already admit that
endpoint's schema isn't fully pinned down) and retries once
automatically with a larger, capped budget — no signature changes, no
caller changes, verified against the real HTTP-calling code rather
than the mocked `provider.call()` every other test in this project
uses. Full detail in `ARCHITECTURE_ISSUES.md`'s 2026-08-24 entry,
including why this is the more robust fix than guessing a bigger
static number a second time.

**2026-08-24b revision, same day — the 2026-08-23b Markdown fix wasn't
actually reliable either:** live retest showed a genuine, complete
(truncation fix confirmed working) reply come back with `**bold**`
Markdown despite the explicit prompt instruction against it — third
time in this project a prompt-only fix for something mechanical proved
unreliable (extraction's explicit-only rule, the name-recall grounding
rule, now this). Added `_strip_markdown_formatting()` — a deterministic
regex pass (headers, bold, horizontal rules; deliberately not single
`*`/`_`, too ambiguous to strip safely) applied to every LLM-generated
reply on both paths, including before persisting to `/decision`
history. The prompt instruction stays as a first line of defense; this
is the backstop that makes it actually reliable rather than usually
reliable. Full detail in `ARCHITECTURE_ISSUES.md`'s 2026-08-24b entry,
including the standing principle it ends on: treat any prompt-only fix
for something mechanical as provisional until proven or backed by a
deterministic mechanism.

- **No new test proves the free-form answer is actually *better*,
  only that the plumbing and guards work correctly.** That's
  inherently something only real usage can show — same honest
  limitation as every prompt-behavior claim in `ARCHITECTURE_ISSUES.md`
  since 2026-08-15. Retest live, the way every fix in this project has
  been retested.
- **`orchestrator.api.run_request()`/`resume_action()`/
  `orchestrator.intent.detect_intent()` are now dead code from the
  CLI's perspective**, kept intentionally rather than deleted (see §3).
  If a future change makes it clear they'll never be needed again,
  that's a deliberate removal decision for later, not something this
  record is making now.
- **`_persist_grounded_decision`'s `confidence=1.0` is a placeholder,
  not a real confidence estimate** — the guards prove the reply isn't
  *inaccurate* against known evidence, they say nothing about whether
  it's a *good* answer. Matches the honesty of what it's asserting
  (guard-checked, not guessed), but worth being clear this isn't a
  calibrated score.
- **Reflection's old backward-looking framing ("N goals completed...")
  has no direct replacement** — it's now just one more thing the
  free-form answer can discuss if asked, not a guaranteed-present
  summary. If periodic, unprompted reflection turns out to be wanted
  (the Insight Engine idea already flagged as deferred in
  `V1_M3_IMPLEMENTATION_RECORD.md` §4) — that's still separate, unbuilt
  scope, unaffected by this change either way.

## 5. If you're the next agent: start here

1. 309/309 tests pass against a real Postgres as of this writing — if
   you're seeing failures, something changed since, not a pre-existing
   gap.
2. Read `ARCHITECTURE_ISSUES.md`'s 2026-08-23 entry for the full
   "why" — this doc is the "what," that entry is the reasoning and the
   exact request that drove it.
3. If you're extending `_grounded_reply`'s prompt further, keep the
   guard-reuse principle: check new claims against real structured
   evidence with a function reasoning/api.py-style, don't just add more
   prose instructions and hope. That pattern has already failed
   multiple times in this project's history (see the three consecutive
   name-recall fix attempts in `ARCHITECTURE_ISSUES.md`, 2026-08-17c
   through 2026-08-18c) before the actual root causes were found by
   reading code, not by writing more careful sentences.
