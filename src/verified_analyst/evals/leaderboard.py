"""Multi-model leaderboard: how often does each model invent a number, and does the guard save it?

For every model we run the same oracle-scored eval and report, per model:

  invented_rate      share of runs whose FIRST answer stated a number no query returned. This is the
                     model's raw behaviour, before the guard bounced anything back. Denominator: runs
                     where the model's first answer had status "answered".
  unanswerable_rate  share of impossible/vague tasks where the model answered anyway (a number where
                     the honest reply is "I can't" or "what do you mean?").
  pass_rate          share of runs that end correct AND grounded, with the guard on (+ Wilson 95% CI).
  saved_by_guard     runs where the first draft invented a number, the guard rejected it, and the
                     final answer passed.
  cost / latency     median per task, from token usage and a price table (OpenRouter's, if available).

Nothing here calls a model directly: callers pass a provider factory per model, so the whole
pipeline is testable with the deterministic reference solvers.
"""

from __future__ import annotations

import html
import json
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..db import Database
from .runner import ProviderFactory, RunRecord, run_eval, summarize, write_outputs
from .stats import percentile, wilson
from .tasks import Task

Pricing = dict[str, tuple[float, float]]  # model -> (USD per input token, USD per output token)


@dataclass
class ModelRow:
    model: str
    runs: int
    pass_rate: float
    pass_ci: tuple[float, float]
    invented_rate: float | None
    invented_ci: tuple[float, float] | None
    invented_n: int
    unanswerable_rate: float | None
    unanswerable_n: int
    saved_by_guard: int
    errors: int
    latency_p50_s: float
    cost_median_usd: float | None
    cost_total_usd: float | None


def model_row(model: str, records: list[RunRecord], pricing: Pricing | None = None) -> ModelRow:
    n = len(records)
    passed = sum(r.passed for r in records)
    answered_first = [r for r in records if r.first_status == "answered"]
    invented = sum(1 for r in answered_first if r.first_draft_ungrounded)
    refusal_tasks = [r for r in records if r.expected_status != "answered"]
    answered_anyway = sum(1 for r in refusal_tasks if r.status == "answered" or r.first_status == "answered")
    saved = sum(1 for r in records if r.first_draft_ungrounded and r.rejections and r.passed)
    price = (pricing or {}).get(model)
    costs = ([r.input_tokens * price[0] + r.output_tokens * price[1] for r in records] if price else None)
    return ModelRow(
        model=model, runs=n,
        pass_rate=passed / n if n else 0.0, pass_ci=wilson(passed, n),
        invented_rate=invented / len(answered_first) if answered_first else None,
        invented_ci=wilson(invented, len(answered_first)) if answered_first else None,
        invented_n=len(answered_first),
        unanswerable_rate=answered_anyway / len(refusal_tasks) if refusal_tasks else None,
        unanswerable_n=len(refusal_tasks),
        saved_by_guard=saved,
        errors=sum(1 for r in records if r.status == "error"),
        latency_p50_s=round(percentile([r.latency_s for r in records], 50), 2) if records else 0.0,
        cost_median_usd=round(percentile(costs, 50), 5) if costs else None,
        cost_total_usd=round(sum(costs), 4) if costs else None,
    )


def run_leaderboard(db: Database, tasks: list[Task], models: list[str],
                    factory_for: Callable[[str], ProviderFactory], out_dir: str | Path, *,
                    runs: int = 1, max_steps: int = 10, pricing: Pricing | None = None,
                    meta: dict | None = None, progress: Callable[[str], None] | None = None) -> list[ModelRow]:
    out = Path(out_dir)
    rows: list[ModelRow] = []
    for m in models:
        if progress:
            progress(f"== {m}")
        records = run_eval(db, tasks, factory_for(m), runs=runs, max_steps=max_steps, progress=progress)
        per_model = {k: "?" for k in ("date_utc", "verified_analyst", "duckdb", "python",
                                      "dataset_sha256", "tasks_sha256")}
        per_model.update(meta or {})
        per_model.update(provider=m, runs_per_task=runs)
        write_outputs(out / _slug(m), records, per_model)
        rows.append(model_row(m, records, pricing))
    rows.sort(key=lambda r: (-r.pass_rate, r.invented_rate if r.invented_rate is not None else 1.0))
    out.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {**(meta or {}), "runs_per_task": runs, "tasks": len(tasks)},
               "models": [asdict(r) for r in rows]}
    (out / "leaderboard.json").write_text(json.dumps(payload, indent=2))
    (out / "LEADERBOARD.md").write_text(render_markdown(rows, payload["meta"]))
    (out / "index.html").write_text(render_html(rows, payload["meta"]))
    return rows


def _slug(model: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in model)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _ci(ci: tuple[float, float] | None) -> str:
    return "" if ci is None else f" ({ci[0]:.0%}–{ci[1]:.0%})"


def render_markdown(rows: list[ModelRow], meta: dict) -> str:
    L = ["# How often does an AI data analyst invent numbers?", "",
         f"{meta.get('tasks', '?')} tasks × {meta.get('runs_per_task', '?')} runs per model"
         f"{(' · ' + meta['date_utc']) if meta.get('date_utc') else ''}. "
         "Brackets are Wilson 95% confidence intervals.", "",
         "| model | invented a number (first draft) | answered the unanswerable | correct + grounded (with guard) | saved by guard | median cost / task | p50 latency |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        cost = "n/a" if r.cost_median_usd is None else f"${r.cost_median_usd:.4f}"
        L.append(f"| `{r.model}` | {_pct(r.invented_rate)}{_ci(r.invented_ci)} | {_pct(r.unanswerable_rate)} "
                 f"| {_pct(r.pass_rate)}{_ci(r.pass_ci)} | {r.saved_by_guard} | {cost} | {r.latency_p50_s}s |")
    L += ["", "- **invented a number**: the model's first answer contained a figure that none of its own queries returned.",
          "- **answered the unanswerable**: on tasks with no valid numeric answer, it gave one anyway.",
          "- **correct + grounded**: final answer matches the oracle and every number is traceable, with the guard re-prompting up to twice.",
          "- **saved by guard**: runs that invented a number, were bounced back, and then passed."]
    return "\n".join(L) + "\n"


def render_html(rows: list[ModelRow], meta: dict) -> str:
    """Self-contained page (no external assets) so it can be published anywhere."""
    def bar(x: float | None, color: str) -> str:
        if x is None:
            return '<span class="na">n/a</span>'
        return (f'<div class="bar"><i style="width:{x * 100:.1f}%;background:{color}"></i>'
                f'<span>{x:.0%}</span></div>')
    body = "".join(
        f"<tr><td><code>{html.escape(r.model)}</code></td>"
        f"<td>{bar(r.invented_rate, '#ff6b6b')}</td><td>{bar(r.unanswerable_rate, '#ffb547')}</td>"
        f"<td>{bar(r.pass_rate, '#4fd18b')}</td><td class='num'>{r.saved_by_guard}</td>"
        f"<td class='num'>{'n/a' if r.cost_median_usd is None else f'${r.cost_median_usd:.4f}'}</td></tr>"
        for r in rows)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>How often does an AI data analyst invent numbers?</title>
<style>body{{margin:0;background:#0b0f17;color:#e6ebf5;font:15px/1.5 ui-sans-serif,system-ui,sans-serif}}
.w{{max-width:1080px;margin:0 auto;padding:36px 22px}}h1{{font-size:34px;letter-spacing:-.02em;margin:0 0 6px}}
p{{color:#9aa6bf}}table{{width:100%;border-collapse:collapse;margin-top:18px}}th{{text-align:left;font-size:12px;color:#9aa6bf;font-weight:600;padding:8px;border-bottom:1px solid #1d2638}}
td{{padding:10px 8px;border-bottom:1px solid #141b29;vertical-align:middle}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{position:relative;height:22px;background:#141b29;border-radius:6px;overflow:hidden;min-width:120px}}.bar i{{position:absolute;inset:0 auto 0 0}}
.bar span{{position:relative;padding-left:8px;font-size:12.5px;line-height:22px;font-weight:600}}.na{{color:#6b7690}}code{{font-size:13px}}</style></head>
<body><div class="w"><h1>How often does an AI data analyst invent numbers?</h1>
<p>{meta.get('tasks', '?')} questions about a synthetic app database × {meta.get('runs_per_task', '?')} runs per model. Scored against
gold SQL, no LLM judge. Source: <code>verified-analyst</code>.</p>
<table><tr><th>model</th><th>invented a number (first draft)</th><th>answered the unanswerable</th><th>correct + grounded, with guard</th><th>saved by guard</th><th>median cost/task</th></tr>
{body}</table></div></body></html>"""


def fetch_openrouter_pricing(models: list[str], urlopen=urllib.request.urlopen, timeout: float = 20) -> Pricing:
    """Per-token USD prices from OpenRouter's public model list. Missing models are simply absent."""
    with urlopen("https://openrouter.ai/api/v1/models", timeout=timeout) as resp:
        data = json.load(resp)
    out: Pricing = {}
    wanted = set(models)
    for m in data.get("data", []):
        if m.get("id") in wanted:
            p = m.get("pricing") or {}
            try:
                out[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
            except (TypeError, ValueError):
                continue
    return out
