# Jarvis V0

An AI operating system for one person. See `docs/IMPLEMENTATION_PROMPT.md`
for the full spec and `ARCHITECTURE_ISSUES.md` for every logged deviation
between spec and implementation.

## Status: V0 runnable end-to-end on your own machine (post-build phase)

Per architect decision (2026-08-04): infra + LLM Router + CLI are done;
implementation stops here until 1-2 weeks of daily use produce real bugs
to drive V1 planning. **Docker and the three LLM providers require local
verification** — this sandbox can't reach Docker Hub, Groq, Gemini, or
OpenRouter. See `ARCHITECTURE_ISSUES.md`'s last entry for exactly what
was and wasn't verified here.

### Setup (on your machine)

```bash
cp .env.example .env        # then fill in GROQ_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY
./setup.sh                  # installs deps, starts Postgres+pgvector via Docker, runs migrations
python3 cli.py
```

Any provider key can stay blank — the Router falls back to the next
provider, and ultimately to the original deterministic templates if all
three are unreachable (this fallback path is what's actually been tested
in this sandbox, end-to-end, with real Postgres and no LLM keys).

### CLI

Explicit slash-commands for system management (`/mission`, `/goal`,
`/memory`, `/status`, `/help`); everything else goes through Jarvis's
normal reasoning pipeline (`orchestrator.run_request`). No natural-
language CRUD yet — that's an explicit later-version decision, not an
oversight.

### What changed in this phase (not a new milestone — M0-M7 is still V0's full scope)

- `docker-compose.yml`, `.env.example`, `setup.sh` — one-command local setup.
- `infra/llm_router.py` — real Groq/Gemini/OpenRouter routing (fixed
  rules per §5.1, not the adaptive routing §5.3 defers), retry, fallback.
- The three deterministic placeholders V0 always intended to replace now
  call the Router first, falling back to their original templates on any
  failure: `reasoning/api.py`'s Decision prose, `orchestrator/intent.py`'s
  classifier, `insight/api.py`'s narrative. IDs and traceable fields
  (goal_ids, context_item_ids, confidence, mission_alignment) stay
  computed deterministically in Python either way — the LLM only ever
  generates prose grounded in evidence already gathered, never invents IDs.
- `cli.py` — the terminal interface.

131 tests passing (122 from M0-M7 + 9 new `test_llm_router.py` tests
against mocked providers).

<details><summary>M0-M7 detail</summary>

**M0:** `contracts/` (13 §6 models), logging, config, Postgres+pgvector.
**M1:** `mission/api.py`, `goals/api.py` — CRUD + cycle detection.
**M2:** `memory/api.py` — single writer, merge-not-duplicate.
**M3:** `reasoning/api.py` — Planning/Reflection intents.
**M4:** `planner/api.py`, `permission/api.py` + risk calculator.
**M5:** `action_engine/api.py` real execution + 5 primitive capabilities.
**M6:** `orchestrator/api.py` — first real end-to-end request loop.
**M7:** `insight/api.py` — evidence-first weekly reflection/review.

</details>

## Setup

```bash
pip install -r requirements.txt

# Postgres + pgvector (Ubuntu/Debian):
apt-get install -y postgresql postgresql-contrib postgresql-16-pgvector
service postgresql start
su postgres -c "psql -c \"CREATE USER jarvis WITH PASSWORD 'changeme' SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE jarvis OWNER jarvis;\""
# password goes in a local, untracked config override — never in
# config/default.yaml (INF-13: no hardcoded secrets)
```

Migrations apply automatically the first time anything calls
`infra.migrate.apply_migrations(pool)` — the test suite does this for you
via `tests/conftest.py`'s `db_pool` fixture.

## Run tests

```bash
# Postgres must be running first — M1's tests hit a real database.
python3 -m pytest tests/ -v
```

## Repo map

See §7 of `docs/IMPLEMENTATION_PROMPT.md` for the intended structure and
the RFC-014 boundary rule (packages only talk to each other's `api.py`,
never reach into internal storage/models directly). `docs/rfcs/` holds
every source document the architecture was built from, across all three
review rounds.
