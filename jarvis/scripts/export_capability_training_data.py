"""Export (user_text -> capability_id) pairs for fine-tuning a local
decision model (e.g. Laya) on Nika's own capability-selection task.

Source: every capability invocation that got far enough to create a
Decision+Action pair (capability_invocation/dispatch.py's
_persist_decision_plan_action) — decision.objective is the raw user
text, decision.selected_option is the capability that was picked.
This ONLY covers turns that successfully resolved to a capability; it
does not capture "asked to clarify" or "no capability applies" turns,
so it's a positive-only dataset as a first cut.

Usage (from the jarvis/ directory, same env as the backend):
    python scripts/export_capability_training_data.py [output_path]
    (output_path defaults to training_data.jsonl)

Writes JSONL straight to output_path — don't use shell `>` redirection,
Nika's own logger (infra/logging.py) prints INFO lines to stdout and
they'd end up mixed into the same file.

Each output line is one Laya-shaped example:
    {"state": "<raw user message>",
     "questions": [{"key": "capability", "type": "choice",
                    "options": [<every registered capability id>, "none"]}],
     "answer": {"capability": "<the capability_id that was actually picked>"}}
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")  # run from jarvis/ root, same as the backend

import capabilities.bootstrap  # noqa: F401 — populates the registry below
from capabilities.registry import list_capabilities
from config.loader import load_config
from infra.storage import close_pool, connection, init_pool


async def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "training_data.jsonl"

    await init_pool(load_config())  # same bootstrap cli.py/server/app.py do at startup
    option_ids = sorted(c.id for c in list_capabilities()) + ["none"]

    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT d.objective AS user_text, d.selected_option AS capability_id,
                   d.confidence, d.created_at
            FROM decision d
            JOIN plan p ON p.decision_id = d.id
            JOIN action a ON a.plan_id = p.id
            WHERE d.intent = 'CapabilityInvocation'
            ORDER BY d.created_at;
            """
        )

    n = 0
    by_capability: dict[str, int] = {}
    with open(output_path, "w") as f:  # write directly — never rely on shell stdout redirection
        for r in rows:
            if r["capability_id"] not in option_ids:
                continue  # capability since removed from the registry — skip
            example = {
                "state": r["user_text"],
                "questions": [{"key": "capability", "type": "choice", "options": option_ids}],
                "answer": {"capability": r["capability_id"]},
            }
            f.write(json.dumps(example) + "\n")
            n += 1
            by_capability[r["capability_id"]] = by_capability.get(r["capability_id"], 0) + 1

    print(f"# Wrote {n} examples to {output_path}. Per-capability counts: {by_capability}")
    print(f"# Registered capabilities with ZERO examples: "
          f"{[c for c in option_ids if c not in by_capability]}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
