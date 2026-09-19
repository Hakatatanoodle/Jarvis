# V1-M1 Implementation Plan: Conversation Layer

Status: drafted from discussion between user + Claude, 2026-08-08.
Restores the `Conversation` stage named in `HOW_JARVIS_THINKS.md`'s
original flow (`User → Conversation → Orchestrator → ...`) but never
built in V0. See `V1_PLANNING_INPUT.md` §0 for the full background.

Every item below is marked **DECIDED** (ready to build against) or
**PROPOSED** (my recommendation, grounded in the original docs, needs
just a confirm-or-object rather than a full debate).

---

## 1. Exact responsibilities of the Conversation layer — DECIDED

A new subsystem, `conversation/`, own `contracts/`, own `api.py`, same
boundary discipline as every other subsystem. Exactly two jobs, no more:

1. **Mission gate.** Does an active Mission exist? If not: onboarding
   response, stop.
2. **Routing.** For everything else: does this input need the grounded,
   evidence-first machinery, or can it be answered as ordinary
   conversation? Hands off to Orchestrator or answers directly.

It does **not** do intent classification (Planning vs. Reflection stays
exactly where it is, inside `orchestrator`/`reasoning`), does not do NL
CRUD (out of scope, §9), and does not touch Goals/Missions/Memory
directly except read-only (checking Mission existence).

---

## 2. Input flow: CLI → Conversation → Orchestrator — DECIDED

Per the original diagram, Conversation sits **before** Orchestrator, not
inside it. Concretely: `cli.py`'s `_handle_request` (currently calls
`orchestrator.api.run_request(text)` directly at line 240) changes to
call `conversation.api.handle(text)` instead. `conversation/api.py`
internally decides whether to answer directly or call
`orchestrator.api.run_request(text)` itself.

This keeps Orchestrator's own responsibility unchanged (it still only
ever does Reasoning→Planning→Permission→Action), and gives Conversation
a clean, independent public API — matches "every subsystem communicates
only through public api.py."

**Slash commands are entirely unaffected** — `cli.py`'s existing
`elif line.startswith("/goal")` etc. dispatch happens before any of
this; Conversation only ever sees the free-text branch, exactly as
Orchestrator does today.

---

## 3. Routing mechanism and fallback behavior — PROPOSED

Same hybrid shape already proven in `orchestrator/intent.py`
(deterministic fast-path, LLM fallback), scoped concretely:

- **Deterministic fast-path:** a small, tight set of unambiguous
  greeting/farewell/thanks patterns — the exact examples from `bug.md`
  (`hi`, `hello`, `hey`, `yo`, `good morning`, `good night`, `thanks`,
  `thank you`, `bye`, `goodbye`) — instant conversational routing, zero
  latency, zero API cost, works even with all providers down.
- **Everything else:** one cheap LLM call (`task_type="fast"`, matching
  `detect_intent`'s cost profile) asking a single yes/no question:
  *"does this need Jarvis's goal/mission/memory-grounded reasoning to
  answer, or is this ordinary conversation?"* → `{"needs_reasoning": true|false}`.
- **Fallback if the LLM call fails or returns garbage:** default to
  `needs_reasoning = true`. Deliberately the *safer* default — an
  under-grounded conversational answer risks accidentally asserting
  something about the user's actual goals/mission (exactly the failure
  class BUG-M6-01 fixed); routing an ambiguous case into the rigorous,
  evidence-first path costs nothing but a slightly more formal answer.

---

## 4. Mission gate behavior — DECIDED

Confirmed: slash commands work exactly as they do in V0, unaffected by
whether a Mission exists — `/mission set` obviously still needs to work
with no Mission, since that's how the first one gets created. The gate
applies only to the free-text path Conversation owns.

**Onboarding message** (delegated — my draft, happy to adjust):

```
Hey — I don't have a Mission set for you yet, so I can't help you plan
or reflect on anything until I know what you're working toward.

Run /mission set <title> | <statement> to get started, or /help to see
everything I can do.
```

---

## 5 & 6. Conversational response behavior + what gets persisted — DECIDED direction, PROPOSED shape

Per your recall, confirmed against `Jarvis_Architecture_Review_v1.md`
line 110: *"Memory (Archive = flat append-only log; Memory = rule-based
creation only...)."* Archive was named at the MVP-scoping stage but
never actually built — no table/contract exists for it today, only
`ARCHIVED` as a lifecycle *status* on Memory/Goal, which is unrelated.
So this needs one small new piece, not a reuse of something that
already exists.

**Proposed contract**, deliberately minimal — flat and append-only, no
lifecycle, no update path, matching the original description exactly:

```python
class ConversationTurn(BaseModel):
    id: str
    user_text: str
    response_text: str
    routed_to: Literal["conversation", "reasoning"]
    created_at: datetime
```

Owned entirely by `conversation/` (one writer per entity, unchanged
rule). Never read by Reasoning or anything else in M1 — this is
deliberate: it shouldn't become a second, competing source of truth
about the user. It exists to (a) give you a real conversation history,
and (b) be raw material for the Learning Engine later (still scaffolded,
still deferred — see `V1_PLANNING_INPUT.md` §0), not to feed anything in
M1 itself.

Conversational responses themselves are **not** persisted as a Decision
and don't spawn a Plan — only this flat log entry, every turn, both
paths (conversational and reasoning-routed) get one row, so the archive
is a complete record either way.

---

## 7. How M1 interacts with existing V0 invariants — DECIDED

- Deterministic fallback: the routing check has one (§3, defaults to
  the safer path).
- Evidence-first: conversational responses must never assert anything
  specific about the user's actual goals/mission/memory contents — if
  it needs to say something grounded, that's exactly the signal it
  should have routed to Reasoning instead. The system prompt for the
  conversational responder should say this explicitly.
- One writer per entity: `conversation_turn` is owned solely by
  `conversation/`.
- Everything downstream (Reasoning, Planning, Permission, Action Engine)
  is completely untouched — Conversation only ever decides whether to
  call in, never changes what happens after.

---

## 8. Tests / acceptance criteria — concrete examples, drawn from real dogfooding transcripts

| Input | Mission state | Expected routing | Expected behavior |
|---|---|---|---|
| `hello jarvis` | none | mission gate | onboarding message, no exception (fixes `bug.md` BUG-001) |
| `yo` | active | fast-path | conversational reply, no `Cannot reason without...` error |
| `12345` | active | fast-path miss → LLM → likely `needs_reasoning=false` | conversational reply, not forced into Planning |
| `what should i work on today?` | active | `needs_reasoning=true` | routes to Orchestrator exactly as today — must not regress |
| `why do you always talk about goals?` | active | `needs_reasoning=false` | conversational reply, ideally addresses the meta-question directly instead of dumping goal statuses (current bug) |
| `remember that i like rock music` | active | `needs_reasoning=false` (NL CRUD stays out, §9) | conversational reply acknowledging it can't persist that yet — not silence, not a Reasoning misfire |
| all provider calls failing (simulated) | active | fast-path only | greetings still work with zero network; non-fast-path falls back to `needs_reasoning=true` per §3 |

---

## 9. What explicitly stays out of M1 — DECIDED

Everything except the above. Specifically excluded: natural-language
CRUD for goals/memory/mission (Decision B, still separately open),
`Knowledge` and `Learning` subsystem work (still scaffolded, still
deferred per `V1_PLANNING_INPUT.md` §0), any change to Planning/
Reflection's binary intent classification itself, any change to
Reasoning/Planner/Permission/Action Engine.
