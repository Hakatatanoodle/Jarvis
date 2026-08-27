# V1-M1 Implementation Record — Conversation Layer

Status as of 2026-08-11. Written for a cold-start agent picking this up
— assumes no prior context from chat history. If you're that agent,
read this fully before touching code.

Companion docs, in the order they were produced: `V1_PLANNING_INPUT.md`
(why M1 exists), `V1_M1_IMPLEMENTATION_PLAN.md` (the design, agreed
before any code), `ARCHITECTURE_ISSUES.md` (flagged architectural
decisions, some still open). This doc is the "what actually happened"
record, written after the fact.

---

## 1. What M1 is, in one paragraph

Restores the `Conversation` stage named in `HOW_JARVIS_THINKS.md`'s
original flow (`User → Conversation → Orchestrator → ...`) but never
built in V0. New subsystem `conversation/`, own contract, own
`api.py`, sitting between `cli.py` and `orchestrator.api.run_request`.
Two jobs only: (1) a Mission-existence gate, (2) routing each free-text
message to either the full evidence-first Reasoning pipeline
(unchanged) or a lightweight conversational responder (new). Full
design rationale is in `V1_M1_IMPLEMENTATION_PLAN.md` — this doc covers
what was actually built and found afterward.

**Verification standard used throughout:** every fix below was tested
against a real Postgres instance (not just mocks) wherever the claim
was "this now works end-to-end," and the full test suite was run after
every change. Current suite status: **187/187 passing.**

---

## 2. Files that exist because of M1

- `contracts/conversation_turn.py` — `ConversationTurn`: flat,
  append-only archive record. `session_id` field added partway through
  (see §4) — one id per process run, real enforced boundary for "gone
  after /exit."
- `infra/migrations/0015_conversation_turn.sql` — table creation.
- `infra/migrations/0016_conversation_turn_session.sql` — adds
  `session_id`, nullable, no backfill (pre-migration rows have no real
  session to attribute).
- `conversation/api.py` — the subsystem itself. Single entry point:
  `handle(user_text) -> ConversationOutcome`.
- `cli.py` — `_handle_request` now calls `conversation.api.handle()`
  instead of `orchestrator.api.run_request()` directly. Slash commands
  entirely unaffected (dispatched before this is ever reached).
- `tests/test_conversation_api.py` — 41 tests, all against real
  Postgres.
- Also touched: `reasoning/api.py` (two guards added here, not in
  `conversation/`, see §3), `infra/llm_router.py` (provider/model
  changes, see §5), `tests/test_reasoning_api.py`,
  `tests/test_llm_router.py`, `tests/conftest.py` (added
  `conversation_turn` to the truncate list).

---

## 3. Every guard built, and the real bug each one fixes

All follow the same two-layer pattern: **structured grounding**
(reduce the chance an LLM invents something) + **deterministic
post-hoc validation** (guarantee it, since prompting alone doesn't).
This pattern predates M1 — it's from BUG-M6-01, fixed before M1
existed — and every guard below reuses it rather than inventing a new
mechanism each time.

### In `reasoning/api.py` (pre-existing, listed for completeness)
1. **`_asserts_unsupported_relationship`** — LLM once claimed two
   unrelated goals were connected. Fixed pre-M1.
2. **`_looks_garbled`** — LLM output once contained corrupted text
   (`Hajdus(firstcaps(m)arker...)`). Fixed pre-M1.
3. **`_asserts_wrong_mission`** — LLM once confidently called a Goal
   "the mission" when the real Mission title was never in its evidence
   (`"Your active mission is to Build jarvis v1"` when the real mission
   was "I wanna be Ironman"). Found and fixed 2026-08-10, after M1
   existed but in the Reasoning subsystem, not Conversation.

### In `conversation/api.py` (M1-native)
4. **`_claims_unbacked_action`** — the responder once said *"I've
   noted the word 'coco'"* — a false persistence claim, since the
   conversational path (at the time) had zero memory of any kind.
   Generalizes past the literal word "remember" — catches any
   first-person completed/promised persistent-action claim
   (`"I'll remind you"`, `"I've saved that"`, etc.), because the
   underlying invariant it checks is real: this path performs zero
   Action Engine calls.
5. **`_mentions_fabricated_ui`** — the responder once said capabilities
   were *"listed for you under the Mission tab"* — there is no GUI at
   all, terminal-only. Term list deliberately narrow (dropped
   `click`/`window`/`screen`/`page` etc. — real idiom-collision risk in
   casual conversation, e.g. "keeping tabs on" would false-positive
   less narrowly).
6. **`_leaks_prompt_structure`** — the responder once literally started
   a reply with `"Mission: I wanna be ironman"` — echoing the prompt's
   own `"Label: value"` formatting instead of answering. Checks for any
   line starting with one of the prompt's literal field labels.

### Session-context addition (2026-08-09, mid-M1)
Originally the conversational responder had zero memory of anything
said earlier, even within the same sitting — an offer like *"call me
Tony?"* was hollow the instant it was made. Fixed by feeding the last 6
`ConversationTurn` rows (both routing paths) into the prompt, scoped by
`session_id` so it provably doesn't leak across process restarts (see
`test_session_boundary_is_real_not_just_claimed`). This also
surfaced a reflexive-disclaimer LLM behavior (denying it "remembers"
something while simultaneously using it) — addressed with an explicit
system-prompt instruction, not a new guard (see `_CONVERSATIONAL_SYSTEM`
in `conversation/api.py`).

### Routing precision fix (2026-08-10)
Surface phrasing like *"what should I do"* or *"how do I..."* was
over-triggering `needs_reasoning=true` regardless of actual subject
matter — a personal question about a relationship, or a product
question about a nonexistent "mission tab," both got routed into
Reasoning. **This is not just a bad-answer problem** — misrouted
content spuriously touches real `goal_history` via `goals.advance`
("Reviewed via Jarvis planning cycle" on goals that have nothing to do
with the actual message). Fixed with contrastive examples in
`_ROUTING_SYSTEM`. **Confirmed working in live dogfooding** on the
exact phrasing that used to fail (`"what should i do"` about a
relationship, `"make a plan to talk to her"` — both correctly stayed
conversational; `"what goals do i have"` correctly routed to
Reflection). This is the one fix with the strongest real-world
confirmation of everything in this document.

---

## 4. Mission-gate + onboarding

`bug.md` BUG-001: `"hello jarvis"` with no Mission set used to surface
a raw `NoActiveMissionError` exception string. Fixed: `handle()` checks
`get_active_mission()` first, returns a plain onboarding message if
none exists, before any routing/classification happens. Confirmed
working end-to-end.

---

## 5. LLM provider history — read this before touching `llm_router.py` again

This has been the single most volatile part of the whole project.
Full chronological record, because guessing here has cost real time
repeatedly:

1. `gemini-1.5-flash` — fully retired by Google, unconditional 404.
2. `gemini-2.5-flash` + `?key=` query param — Google migrated keys to a
   new `AQ.` format requiring the `x-goog-api-key` header instead.
3. Header fix alone still 404'd — `gemini-2.5-flash` was *also*
   deprecated by then; migrated to the Interactions API
   (`/v1beta/interactions`) with `gemini-3.5-flash`.
4. `complex` task type flip-flopped between `gemini`/`groq` — a user's
   manual edit got silently reverted by a later full-file delivery (a
   real process failure — full-file rewrites can clobber hand-edits
   outside the sandbox's own tracked state).
5. **2026-08-11: OpenRouter promoted to primary for all task types**
   (`fast`/`conversation`/`complex`), Groq demoted to fallback.
   Reasoning: OpenRouter had a 100% success rate every time it was
   *reached* across the entire session; Groq/Gemini repeatedly failed
   on DNS errors, key migrations, endpoint changes, model
   deprecations — real debugging cost for calls that only ever polish
   prose or do cheap classification, never core logic.
6. Picked `meta-llama/llama-3.3-70b-instruct:free` as the model —
   verified free via search at the time, a real jump from the
   previous 8B tier. **Broke within the same day** — OpenRouter
   returned 404 with a genuinely different cause than expected: the
   account's privacy settings block free-tier models by default
   (`https://openrouter.ai/settings/privacy` — "Enable free endpoints
   that may train on inputs" / "...may publish prompts"). User enabled
   it, accepting the free-tier data tradeoff knowingly.
7. **Same day, broke again** — after the privacy fix, OpenRouter
   returned a *different* 404: `"This model is unavailable for free."`
   The specific `:free` slug had been pulled from the free tier within
   hours of being verified. This is the second time in one day a
   hardcoded free-model slug broke.
8. **Current state:** switched to `openrouter/free` — OpenRouter's own
   official auto-router alias (not a specific model), documented as
   existing specifically to survive individual free models rotating
   out. This is the structurally correct fix for the failure mode in
   steps 6-7, not another guessed slug. **NOT YET CONFIRMED WORKING —
   this is the first thing the next session should verify.**

**If `openrouter/free` also proves unstable:** the honest fallback
option is demoting OpenRouter back to secondary and running primarily
on Groq (which, DNS blips aside, was the most consistently *available*
single provider across the whole session — just not the highest
quality). A paid tier (`anthropic/claude-haiku-4.5` at $1/$5 per
million tokens was researched and rejected only because of budget, not
quality) remains the most robust long-term option if that constraint
ever changes.

**Diagnostic improvement made 2026-08-11:** all three `_call_*`
functions now include the actual HTTP response body in error messages
(previously `httpx`'s default dropped it, turning a self-explanatory
"no endpoints match your data policy" into an opaque "404 Not Found"
that took real research to trace). Any future provider failure should
now be diagnosable directly from the log line.

---

## 6. Known open issues — NOT fixed, need a real decision or design pass

### 6.1 Reflection can't answer open-ended "does X align with my mission" questions
Found 2026-08-11. `"which [personal life choice] aligns more with my
mission?"` correctly routes to Reflection (it explicitly names
"mission"), but Reflection's entire machinery only compares Goal
statuses against the Mission — it has no way to render a values
judgment. Result: `"No goals are currently available to compare with
the mission"` — technically true, useless, and *confidently* unhelpful
rather than honestly declining. **This is not a routing bug and not
fixable with another guard** — nothing false was asserted. It's a
genuine scope gap in what Reflection was ever built to do. Likely
V1-M4 territory ("better conversation/context... why did you
recommend this") per the original roadmap, not an M1 fix.

### 6.2 Model-quality / emotional-tone-deafness question — deferred, not resolved
2026-08-10: mid-conversation, after a user disclosed something
genuinely vulnerable, the responder completely ignored it and pivoted
to an unrelated topic, while also leaking prompt structure (§3, guard
#6, now fixed) into the same reply. The formatting bug is fixed; the
underlying *attention/coherence* failure under a long, emotionally
loaded conversation was not addressed — it was explicitly deferred
pending the model-quality decision that's now entangled with §5's
provider chaos. Worth revisiting once §5 stabilizes: is this happening
because of small-model limitations specifically, or does it recur on
long *non-emotional* conversations too? Not yet tested either way.

### 6.3 Guard-list growth as a structural signal
Six deterministic guards now exist patching around cheap/free model
mistakes (relationship hallucination, garbled text, mission
conflation, false capability claims, fabricated UI, prompt leakage).
Each was the right call individually and each is tested. But the
count itself is evidence for §6.2's underlying question — a
meaningfully stronger model would likely eliminate several of these
failure classes at the source rather than needing guard #7, #8, etc.
Not urgent, but don't add a seventh guard without also asking whether
that's still the right lever.

### 6.4 `LearningProposal` schema sign-off — old, unrelated, still open
Predates M1 entirely (`IMPLEMENTATION.md` §9). Not touched this round,
noted here only so it doesn't get lost now that `learning/` (still
scaffolded-empty) is back in view given how much LLM-behavior work has
happened.

### 6.5 Crash: `'NoneType' object has no attribute 'strip'` — found 2026-08-11, root cause identified, NOT fixed
Immediately after switching to `openrouter/free` (§5, step 8), a plain
`"yo bro"` produced an uncaught crash instead of any reply:

```
> yo bro
complete_json: response was not valid JSON: 'The user is just greeting me
casually with "yo bro". This is small talk/greeting,'
Conversation routing LLM call unavailable/invalid — defaulting to
needs_reasoning=True
Error: 'NoneType' object has no attribute 'strip'
```

**Root cause, traced but not fixed:** `infra/llm_router.py`'s
`complete_json()` does `text = await complete(...)` then immediately
`text.strip()` with no null-check. `complete()` itself has no
validation either — it returns whatever the provider function returns,
or raises `LLMUnavailableError`; there's no third state handled. If a
provider function returns `None` (e.g. `_call_openrouter`'s
`resp.json()["choices"][0]["message"]["content"]` is `null` in the
response body — a real possibility, not hypothetical, when the
underlying model returns empty output or hits some other soft failure
without the HTTP call itself failing), that `None` propagates silently
through `complete()` and crashes two layers up in `complete_json()`
instead of being treated as a retryable provider failure.

**Why this specific change likely exposed it:** `openrouter/free`
dynamically selects from *many* different backend models per request,
each with potentially different edge-case response quirks, unlike the
single fixed model used before. More backend variety, more chance of a
response shape (like null content) that was never actually exercised
during development against one model. Also worth noting: the first
log line shows the LLM was clearly emitting raw *reasoning* text
("The user is just greeting me casually...") instead of the requested
JSON — the routing call is expected to return `{"needs_reasoning":
bool}` only. That itself is a second, related symptom of using a more
variable auto-routed model backend: less consistent instruction-
following on structured-output requests. `complete_json`'s existing
extraction logic correctly handled *that* part (fell back to `None`
cleanly) — the crash is specifically the null-content case, downstream
of that.

**Where a fix would go, for whoever picks this up:** `complete_json()`
should check `text is None` before calling `.strip()` and treat it as
the same "unavailable, use fallback" path as any other failure —
returning `None` instead of crashing. More robust: each `_call_*`
function should validate the content it extracts is a non-empty string
and raise `LLMUnavailableError` itself if not, so `complete()`'s
existing retry/fallback chain (retry primary, then try the fallback
provider) actually gets a chance to recover, instead of a bad response
silently succeeding and crashing two layers away from where the real
problem was. Two call sites currently missing this:
`infra/llm_router.py` lines ~85 and ~179 (`_call_groq` and
`_call_openrouter`'s return statements).

---

## 7. If you're the next agent: start here

1. **§6.5 first** — a real crash, root cause already identified, just
   not fixed. Small, contained fix (null-check in `complete_json`, or
   better, validation in the `_call_*` functions per §6.5's last
   paragraph). Good first task to build confidence in the codebase
   before anything larger.
2. Run the test suite (`pytest tests/`) — should be 187/187. If not,
   something regressed since this doc was written; find that first.
3. Confirm whether `openrouter/free` actually resolved §5's saga
   beyond the §6.5 crash. If it's still failing outright, don't guess a
   new model slug a third time — use the fallback plan in §5, or ask
   the user for their actual current free-model listing from
   `openrouter.ai/models?free=true` rather than trusting search
   results, since this catalog has now proven to change faster than
   search indexing keeps up with.
4. §6.1 and §6.2 are real, undecided design questions, not backlog
   items to silently pick an answer for — they need the same
   plan-before-code treatment M1 itself got
   (`V1_M1_IMPLEMENTATION_PLAN.md`), not a quick patch.
