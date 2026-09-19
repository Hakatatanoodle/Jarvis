# V1 Planning Input

Consolidated from: `Jarvis_V0_Testing_Review___Next_Priorities.pdf`,
`bug.md`, `ARCHITECTURE_ISSUES.md`, and live dogfooding sessions
(2026-08-05 through 2026-08-08). Everything here was reproduced against
real usage or a real database — nothing speculative.

Organized by what V1 actually needs to *design*, not a flat bug list —
per the architect's framing, this is meant to shape planning, not
become a patch queue for V0.

---

## 0. The conversational-layer gap already had a name — we're restoring it, not inventing it

`HOW_JARVIS_THINKS.md`'s own High-Level Flow, written before any of V0
existed, names a stage between User and Orchestrator that V0 never
built:

```
User
↓
Conversation
↓
Orchestrator
↓
Context Engine
...
```

Section 3 below (the "no user-interaction layer" finding) is that
missing `Conversation` stage. Worth stating plainly for planning
purposes: this isn't scope creep or a new subsystem being invented — the
original architecture already reserved a place for it. It just never
got built, and V0's dogfooding is what surfaced why it's needed.

**Proposed shape, refined through discussion (not yet architect-approved):**
a real subsystem — `conversation/`, own `contracts/`, own `api.py`, same
boundary discipline as every other subsystem — sitting between User and
Orchestrator, with exactly two jobs:

1. **Mission gate.** Does an active Mission exist? If not: onboarding
   response, stop. This is the actual fix for `bug.md` BUG-001 —
   `NoActiveMissionError` currently surfaces as a raw exception string
   because nothing checks for this before Reasoning is reached. A
   structural precondition, not a classification case.
2. **One routing question, not an enumerated bucket list.** Does this
   input need the grounded, evidence-first machinery (goals/mission/
   memory-backed, persisted as a Decision), or not?
   - **Yes** → hands off to Orchestrator exactly as today. Reasoning,
     Planning, Permission, Action Engine — all completely unchanged.
   - **No** → a flexible, LLM-backed conversational responder handles
     it directly. Deliberately *not* subdivided into "greeting" vs.
     "small talk" vs. "confusing input" buckets — a good conversational
     model doesn't need those pre-labeled to respond well to any of
     them, and an enumerated bucket list is exactly the shape that
     produces a canned "I don't understand" catch-all for anything
     unanticipated. This response is never persisted as a Decision and
     never spawns a Plan — it isn't claiming anything about the user's
     goals, so it doesn't need evidence-first rigor.

The routing question itself should reuse the hybrid pattern already
proven in `orchestrator/intent.py` (deterministic fast-path for
obvious/cheap cases, LLM fallback for the ambiguous middle) — as an
internal implementation detail of this one subsystem, not a spreading
enum.

### Other subsystems named in the original architecture but not (fully) built

Checked all of `HOW_JARVIS_THINKS.md`'s named stages/responsibilities
against what's actually in the codebase, since `Conversation` turning
out to be a real gap raised the question of what else might be:

| Named in original docs | Status in code |
|---|---|
| Mission, Goals, Memory, Context+Decision (merged into `reasoning/`), Planner, Permission, Action Engine, Capabilities, Orchestrator | Built, all present |
| **Insight Engine** | Built (`insight/api.py`, 222 lines) — this is real, not a stub |
| **Conversation** | Not built at all — no folder exists. This section's proposal. |
| **Knowledge** (graph of nodes+edges connecting facts) | Scaffolded only — `knowledge/` exists as an empty package, contracts exist, no subsystem logic. Per `IMPLEMENTATION.md`'s own scope note: explicitly deferred to V1. |
| **Learning Engine** (`LearningProposal` pipeline — proposes updates to Memory, doesn't write directly, per the "Learning proposes, doesn't write" decision already logged) | Scaffolded only — `learning/` exists as an empty package. Per `IMPLEMENTATION.md`: explicitly deferred, targeted at M8 scope, and `LearningProposal`'s exact shape was reconstructed from context clues during V0 build-out and **still needs explicit architect sign-off before any Learning Engine work starts** — this predates the Conversation-layer discussion and is worth carrying into V1 planning as its own open item, not folded into the conversational-layer work. |

So: `Conversation` is the one genuinely-missing piece worth designing
now. `Knowledge` and `Learning` aren't missed, exactly — they were
consciously scaffolded-but-deferred, and that original deferral still
looks like the right call (both are legitimately "the system has data,
now do something more sophisticated with it" territory, which depends
on the conversational layer and real memory-write path existing first).
Worth being explicit about that ordering rather than starting either in
parallel with M1.

---

## 1. The architecture held. This is the headline finding.

Every defect found this session that touched Mission, Goal, Reasoning,
Planning, Permission, or Action Engine was a **fixable implementation
bug**, not a design flaw:

- **BUG-M6-01** (hallucinated goal relationship) — LLM enhancement step
  had no structural signal that two goals were unrelated. Fixed with
  structured evidence (explicit `relationships` field) + a deterministic
  post-hoc guard that rejects and falls back to template on violation.
  Confirmed firing correctly in organic use afterward (2026-08-08, the
  "remember I like rock music" session), not just in a contrived test.
- **Mission-identity orphaning** — Mission's new-id-per-version design
  (correct for Mission itself) leaked into `Goal.mission_id`, silently
  hiding every existing goal from Planning/Reflection after any mission
  edit. Fixed with a stable `identity_id` that survives versions,
  migration included, verified end-to-end against a real DB.
- **CLI short-ID cache bug** — `_ID_CACHE` was read but never populated;
  `_cache_id()` returned the wrong value in a fallback path. Both fixed,
  verified against real Postgres, not just unit-tested against mocks.
- **Garbled LLM output** — an OpenRouter fallback response once produced
  corrupted text (`Hajdus(firstcaps(m)arker...`) that would have gone
  straight into a persisted Decision. Added a cheap coherence guard
  (unbalanced parens / abnormally long tokens) — same
  reject-and-fall-back pattern as BUG-M6-01.

**Why this matters for V1 planning:** the deterministic-fallback
principle (every LLM call has a safe path) and the evidence-first
principle both proved themselves under real, repeated failure —
including actual provider outages, not just simulated ones. V1 should
keep both principles as load-bearing, not revisit them.

---

## 2. Provider reliability — real data, not decided yet

Three separate root causes hit the Gemini integration this session,
each requiring an actual fix (not config): a fully retired model
(`gemini-1.5-flash`), a key-format migration requiring header auth
instead of query-param, and a second model deprecation
(`gemini-2.5-flash`) plus an endpoint migration (`generateContent` →
`/v1beta/interactions`). Even after all three fixes, Gemini's specific
request shape for `/v1beta/interactions` is still not fully nailed down
(a `thinking_config` guess caused a 400 and was reverted).

**Observed reliability, in order of what's actually been proven so far:**
- **OpenRouter** (fallback provider) — succeeded on every single attempt
  observed this session, including every time it was the fallback for a
  failed primary.
- **Groq** — reliable for `fast`/`conversation` task types throughout;
  one clean success as `complex` primary after the 2026-08-08 reprioritization,
  but that's one data point, not a trend yet. Also hit transient DNS
  resolution failures (`[Errno -2] Name or service not known`) —
  environment-level, not a code issue, but real enough to plan around.
- **Gemini** — currently deprioritized out of the default rotation
  (`_TASK_TYPE_PROVIDER_PRIORITY["complex"]` changed from `gemini` to
  `groq`) given the disproportionate debugging cost for a call that only
  ever polishes prose, never core logic. Code path (`_call_gemini`) is
  left intact, not deleted, in case Google's schema stabilizes and it's
  worth revisiting.

**Open question for V1, not V0:** whether to keep chasing exact provider
API shapes reactively (current V0 approach) or build a thinner,
schema-agnostic provider adapter layer that's cheaper to update when a
provider changes their API — this session suggests the latter might pay
for itself, but that's a real architectural call, not a quick patch.

---

## 3. The one coherent theme across nearly every remaining issue: no user-interaction layer

This is the actual shape of what's left, and it's the same root cause
appearing in at least seven distinct symptoms across two independent
testing passes:

| Symptom | Source |
|---|---|
| `"goodmorning"` / `"12345"` → forced into Planning or Reflection, no honest "this isn't a request" option | Testing Review P2 |
| `goal status ... Active` (no leading slash) → silently goes to reasoning pipeline | Testing Review P2 (misdiagnosed there as a classifier bug — it's actually correct behavior per `cli.py`'s own docstring, but still a UX gap worth designing around) |
| `"hello jarvis"` with no Mission set → raw `Cannot reason without an active Mission.` exception string, zero onboarding guidance | `bug.md` BUG-001 — confirmed via code: `NoActiveMissionError` is never caught with a friendly message anywhere; falls through to `cli.py`'s generic `except Exception as e: print(f"Error: {e}")` |
| `/mission set Become the best version of myself` (missing `\|` separator) → raw usage string, not an explanation of what's missing | `bug.md` BUG-002 |
| `"Create mission"` (natural language) → dev-log-only feedback, no user-facing confirmation | `bug.md` BUG-003 |
| `"make a new memory that i like ice cream"` → honestly reports no matching goal (good — evidence-first held), but reveals there's no NL memory-write path at all | Dogfooding 2026-08-08 |
| `"why does every response relate to goals?"` → user correctly self-diagnosed the same root cause from the outside | Dogfooding 2026-08-08 |

**This is the actual V1 feature**, not six separate bugs: a
conversational pre-layer that handles greetings, small talk, help,
onboarding (no-Mission state), and malformed/ambiguous input *before*
anything reaches Planning/Reflection's binary intent classifier —
exactly as `bug.md`'s own proposed routing diagram describes
(`Greeting → Small Talk → Help → Reasoning → Planning`). The
Reasoning/Planning/Reflection binary split itself doesn't need to
change; it needs a filter in front of it.

**A related, smaller design question that falls out of this:** several
symptoms above are really "no NL command support" (BUG-003, the mission
statement, the ice-cream memory attempt) — this is a second, related but
distinct decision: does V1 add limited natural-language CRUD for
goals/memories/missions, or does it stay command-driven with a better
help/onboarding layer only? `cli.py`'s own docstring already frames NL
CRUD as "a later-version decision, not V0's" — V1 is that later version,
so this needs an explicit answer either way.

---

## 4. Smaller, contained things worth a look during V1 design (not blocking)

- **Goal reparenting's cross-mission guard** (`goals/api.py`) is now
  unreachable via any public API path in V0's single-active-mission
  model (see `ARCHITECTURE_ISSUES.md`, 2026-08-07 follow-up) — still
  correct, just untestable unless V1 ever supports concurrent Mission
  identities. Worth a conscious decision either way rather than dead
  code nobody remembers the purpose of.
- **`/goal` CLI silently drops unanswered parts of multi-part questions**
  — e.g. "tell me active goals, prioritize, and any draft goals" answered
  the first two and silently omitted the third instead of saying "no
  drafts." Symptom of the same binary-intent limitation in §3, not a
  separate bug.
- **`goals.advance`, the only capability Plan actions currently use, is
  an explicit placeholder** — logs a review pass, doesn't do real work
  (`ARCHITECTURE_ISSUES.md`, 2026-08-04). The CLI's `"✓ completed"`
  language slightly overclaims what happened for a V0 user reading it
  literally. V1's real capability roster should also revisit this
  labeling.
- **Two already-flagged architect-decision items are still open and
  independent of everything above:** `LearningProposal`'s reconstructed
  shape (§6.11, needs a look before M8-equivalent work in V1), and
  whether Goal's stable-id+history pattern vs. Mission's new-id-per-
  version pattern is the right general precedent to extend to other
  entities V1 might add.

---

## 5. What's explicitly *not* on this list

Per the architect's own prior guidance and `Jarvis_V0_Testing_Review`'s
"Things That Should NOT Be Worked On Yet": no cosmetic/CLI-effects work
is included here, and nothing above proposes changing the core
Reasoning/Planning/Permission/Action pipeline itself — every finding
either confirms that pipeline held up, or points at the layer in front
of it.
