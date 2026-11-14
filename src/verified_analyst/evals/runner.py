from __future__ import annotations

import csv
import hashlib
import json
import platform
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb

from .. import __version__
from ..agent import run_agent
from ..db import Database, QueryResult
from .scoring import score
from .stats import percentile, wilson
from .tasks import Task, file_sha256

ProviderFactory = Callable[[Task, "QueryResult | None"], object]


@dataclass
class RunRecord:
    task_id: str
    category: str
    run: int
    expected_status: str
    status: str
    passed: bool
    correct: bool
    grounded: bool
    reason: str
    steps: int
    sql_calls: int
    input_tokens: int
    output_tokens: int
    latency_s: float
    ungrounded: str
    error: str
    first_status: str = ""
    first_draft_ungrounded: str = ""
    rejections: int = 0


def run_eval(db: Database, tasks: list[Task], factory: ProviderFactory, *, runs: int = 1,
             max_steps: int = 10, progress: Callable[[str], None] | None = None) -> list[RunRecord]:
    records: list[RunRecord] = []
    gold_cache: dict[str, QueryResult | None] = {
        t.id: (db.run_internal(t.gold_sql) if t.gold_sql else None) for t in tasks}
    for t in tasks:
        for r in range(1, runs + 1):
            provider = factory(t, gold_cache[t.id])
            res = run_agent(t.question, db, provider, max_steps=max_steps)  # type: ignore[arg-type]
            sc = score(t, res, gold_cache[t.id])
            rec = RunRecord(t.id, t.category, r, t.expected_status, res.status, sc.passed, sc.correct,
                            sc.grounded, sc.reason, res.steps, len(res.sql), res.usage.input_tokens,
                            res.usage.output_tokens, round(res.latency_s, 3),
                            "|".join(res.ungrounded), res.error or "",
                            res.first_status or "", "|".join(res.first_draft_ungrounded), res.rejections)
            records.append(rec)
            if progress:
                progress(f"{'PASS' if rec.passed else 'FAIL'} {t.id} run{r}  {sc.reason}")
    return records


def summarize(records: list[RunRecord]) -> dict:
    def block(rs: list[RunRecord]) -> dict:
        n, k = len(rs), sum(r.passed for r in rs)
        lo, hi = wilson(k, n)
        lat = [r.latency_s for r in rs]
        return {"n": n, "passed": k, "pass_rate": k / n if n else 0.0,
                "wilson95": [round(lo, 4), round(hi, 4)],
                "latency_p50_s": round(percentile(lat, 50), 3), "latency_p95_s": round(percentile(lat, 95), 3),
                "steps_median": percentile([r.steps for r in rs], 50),
                "tokens_median": percentile([r.input_tokens + r.output_tokens for r in rs], 50),
                "ungrounded_rate": sum(1 for r in rs if r.ungrounded) / n if n else 0.0}
    by_cat: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_cat[r.category].append(r)
    return {"overall": block(records), "by_category": {c: block(rs) for c, rs in sorted(by_cat.items())}}


def metadata(provider_name: str, data_dir: Path, tasks_path: Path, runs: int) -> dict:
    h = hashlib.sha256()
    for p in sorted(Path(data_dir).glob("*.csv")):
        h.update(p.read_bytes())
    return {"provider": provider_name, "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "verified_analyst": __version__, "duckdb": duckdb.__version__,
            "python": platform.python_version(), "runs_per_task": runs,
            "dataset_sha256": h.hexdigest()[:12], "tasks_sha256": file_sha256(tasks_path)}


def write_outputs(out_dir: str | Path, records: list[RunRecord], meta: dict) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(records[0]).keys()))
        w.writeheader()
        w.writerows(asdict(r) for r in records)
    summary = summarize(records)
    (out / "summary.json").write_text(json.dumps({"meta": meta, **summary}, indent=2))
    (out / "RESULTS.md").write_text(render_markdown(meta, summary))
    return summary


def render_markdown(meta: dict, summary: dict) -> str:
    o = summary["overall"]
    lines = [f"# Eval results: {meta['provider']}", "",
             f"- date (UTC): {meta['date_utc']}  |  runs/task: {meta['runs_per_task']}  |  "
             f"verified-analyst {meta['verified_analyst']}, duckdb {meta['duckdb']}, python {meta['python']}",
             f"- dataset `{meta['dataset_sha256']}`, tasks `{meta['tasks_sha256']}`", "",
             f"**Pass rate {o['passed']}/{o['n']} = {o['pass_rate']:.1%}** "
             f"(Wilson 95% CI {o['wilson95'][0]:.1%} to {o['wilson95'][1]:.1%}); "
             f"latency p50 {o['latency_p50_s']}s / p95 {o['latency_p95_s']}s; "
             f"unverified-number rate {o['ungrounded_rate']:.1%}", "",
             "| category | n | pass | rate | Wilson 95% | p50 s | p95 s | median steps |",
             "|---|---|---|---|---|---|---|---|"]
    for c, b in summary["by_category"].items():
        lines.append(f"| {c} | {b['n']} | {b['passed']} | {b['pass_rate']:.0%} | "
                     f"{b['wilson95'][0]:.0%}-{b['wilson95'][1]:.0%} | {b['latency_p50_s']} | "
                     f"{b['latency_p95_s']} | {b['steps_median']:g} |")
    return "\n".join(lines) + "\n"
