"""Print the harness-validation table (README) from a fresh run. No model involved."""
from verified_analyst.db import Database
from verified_analyst.evals import load_tasks, run_eval
from verified_analyst.evals.reference import MODES, reference_factory

db, tasks = Database("data"), load_tasks("evals/tasks.jsonl")
ans = [t for t in tasks if t.expected_status == "answered"]
ref = [t for t in tasks if t.expected_status != "answered"]
print(f"| reference solver | answerable passed (of {len(ans)}) | refusal/clarify passed (of {len(ref)}) |")
print("|---|---|---|")
for m in MODES:
    recs = run_eval(db, tasks, reference_factory(m))
    p = {r.task_id: r.passed for r in recs}
    print(f"| `{m}` | {sum(p[t.id] for t in ans)} | {sum(p[t.id] for t in ref)} |")
