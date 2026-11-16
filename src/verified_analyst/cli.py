from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import run_agent
from .datagen import generate
from .db import Database
from .evals import load_tasks, run_eval, write_outputs
from .evals.reference import MODES, reference_factory
from .evals.runner import metadata


def _provider(spec: str, model: str | None):
    from .providers import make_provider
    return make_provider(spec, model)


def cmd_data(a: argparse.Namespace) -> int:
    out = generate(a.out, seed=a.seed, n_users=a.users)
    print(f"wrote synthetic dataset to {out}/ (users.csv, subscriptions.csv, payments.csv)")
    return 0


def cmd_schema(a: argparse.Namespace) -> int:
    print(Database(a.data).describe())
    return 0


def cmd_ask(a: argparse.Namespace) -> int:
    db = Database(a.data)
    res = run_agent(a.question, db, _provider(a.provider, a.model), max_steps=a.max_steps)
    if a.show_sql:
        for i, q in enumerate(res.sql, 1):
            print(f"-- query {i}\n{q}\n", file=sys.stderr)
    print(res.answer or f"(no answer: {res.status} {res.error or ''})")
    flag = "verified" if res.verified else f"UNVERIFIED numbers: {res.ungrounded}"
    print(f"\n[{res.status} | {flag} | {res.steps} steps | {res.latency_s:.1f}s | "
          f"{res.usage.input_tokens}+{res.usage.output_tokens} tokens]", file=sys.stderr)
    if a.chart_out and res.chart:
        Path(a.chart_out).write_text(json.dumps(res.chart, indent=2))
        print(f"chart spec written to {a.chart_out}", file=sys.stderr)
    return 0 if res.verified and res.status != "error" else 1


def cmd_eval(a: argparse.Namespace) -> int:
    db = Database(a.data)
    tasks = load_tasks(a.tasks)
    if a.provider.startswith("reference:"):
        mode = a.provider.split(":", 1)[1]
        if mode not in MODES:
            print(f"unknown reference mode {mode!r}; choose from {MODES}", file=sys.stderr)
            return 2
        factory, name = reference_factory(mode), a.provider
    else:
        p = _provider(a.provider, a.model)
        factory, name = (lambda t, g: p), p.name
    records = run_eval(db, tasks, factory, runs=a.runs, max_steps=a.max_steps,
                       progress=None if a.quiet else print)
    meta = metadata(name, Path(a.data), Path(a.tasks), a.runs)
    summary = write_outputs(a.out, records, meta)
    o = summary["overall"]
    print(f"\n{name}: {o['passed']}/{o['n']} passed ({o['pass_rate']:.1%}, "
          f"Wilson95 {o['wilson95'][0]:.1%}-{o['wilson95'][1]:.1%}); results in {a.out}/")
    if a.min_pass is not None and o["pass_rate"] < a.min_pass:
        return 1
    if a.max_pass is not None and o["pass_rate"] > a.max_pass:
        print(f"pass rate {o['pass_rate']:.1%} exceeds --max-pass {a.max_pass:.1%}: the harness is too lenient",
              file=sys.stderr)
        return 1
    return 0


def cmd_leaderboard(a: argparse.Namespace) -> int:
    from .evals.leaderboard import fetch_openrouter_pricing, run_leaderboard
    from .providers.openai_compat import OpenAICompatProvider

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    if not models:
        print("pass --models a,b,c", file=sys.stderr)
        return 2
    bad = [m for m in models if m.startswith("reference:") and m.split(":", 1)[1] not in MODES]
    if bad:
        print(f"unknown reference mode(s) {bad}; choose from {MODES}", file=sys.stderr)
        return 2

    def factory_for(model: str):
        if model.startswith("reference:"):
            return reference_factory(model.split(":", 1)[1])
        provider = OpenAICompatProvider(model=model, base_url=a.base_url)
        return lambda t, g: provider

    pricing = {}
    real = [m for m in models if not m.startswith("reference:")]
    if real and not a.no_pricing:
        try:
            pricing = fetch_openrouter_pricing(real)
        except Exception as e:  # pricing is optional; the eval is not
            print(f"(could not fetch OpenRouter pricing: {type(e).__name__}; costs will show n/a)", file=sys.stderr)
    db, tasks = Database(a.data), load_tasks(a.tasks)
    meta = metadata("leaderboard", Path(a.data), Path(a.tasks), a.runs)
    rows = run_leaderboard(db, tasks, models, factory_for, a.out, runs=a.runs, max_steps=a.max_steps,
                           pricing=pricing, meta=meta, progress=None if a.quiet else print)
    print()
    for r in rows:
        inv = "n/a" if r.invented_rate is None else f"{r.invented_rate:.0%}"
        print(f"{r.model:40s} invented {inv:>5}   pass {r.pass_rate:.0%}   saved by guard {r.saved_by_guard}")
    print(f"\nwrote {a.out}/LEADERBOARD.md, leaderboard.json, index.html")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    """Stateless check for scripts and agent skills: run the given queries, then verify the answer against them.

    Exit code 0 = every number is grounded, 1 = some are not, 2 = a query was rejected or failed.
    """
    from .mcp_server import AnalystSession
    answer = sys.stdin.read() if a.answer == "-" else a.answer
    session = AnalystSession(Database(a.data))
    queries = []
    for sql in a.sql:
        out = session.run_sql(sql)
        failed = out.startswith(("REJECTED:", "ERROR:"))
        queries.append({"sql": sql, "ok": not failed, "result": out})
        if failed:
            print(json.dumps({"grounded": False, "error": out, "queries": queries}, indent=2))
            return 2
    verdict = session.check_answer(answer, a.question)
    print(json.dumps({**verdict, "queries": queries}, indent=2))
    return 0 if verdict["grounded"] else 1


def cmd_mcp(a: argparse.Namespace) -> int:
    from .mcp_server import build_server
    build_server(a.data).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="va", description="Verified SQL analyst agent + oracle-scored eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("data", help="generate the synthetic subscription-app dataset")
    d.add_argument("--out", default="data"); d.add_argument("--seed", type=int, default=42)
    d.add_argument("--users", type=int, default=3000); d.set_defaults(fn=cmd_data)

    s = sub.add_parser("schema", help="print the schema the agent sees")
    s.add_argument("--data", default="data"); s.set_defaults(fn=cmd_schema)

    q = sub.add_parser("ask", help="ask one question")
    q.add_argument("question"); q.add_argument("--data", default="data")
    q.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "openrouter"])
    q.add_argument("--model"); q.add_argument("--max-steps", type=int, default=10)
    q.add_argument("--show-sql", action="store_true"); q.add_argument("--chart-out")
    q.set_defaults(fn=cmd_ask)

    e = sub.add_parser("eval", help="run the oracle-scored eval")
    e.add_argument("--provider", default="reference:oracle",
                   help="anthropic | openai | openrouter | reference:<" + "|".join(MODES) + ">")
    e.add_argument("--model"); e.add_argument("--data", default="data")
    e.add_argument("--tasks", default="evals/tasks.jsonl"); e.add_argument("--runs", type=int, default=1)
    e.add_argument("--max-steps", type=int, default=10); e.add_argument("--out", default="results/latest")
    e.add_argument("--min-pass", type=float); e.add_argument("--max-pass", type=float)
    e.add_argument("--quiet", action="store_true"); e.set_defaults(fn=cmd_eval)

    lb = sub.add_parser("leaderboard", help="run the eval for several models and rank them")
    lb.add_argument("--models", required=True,
                    help="comma-separated OpenRouter model ids (or reference:<mode> for offline checks)")
    lb.add_argument("--base-url", help="OpenAI-compatible endpoint (default: VA_BASE_URL or OpenRouter)")
    lb.add_argument("--data", default="data"); lb.add_argument("--tasks", default="evals/tasks.jsonl")
    lb.add_argument("--runs", type=int, default=3); lb.add_argument("--max-steps", type=int, default=10)
    lb.add_argument("--out", default="results/leaderboard"); lb.add_argument("--no-pricing", action="store_true")
    lb.add_argument("--quiet", action="store_true"); lb.set_defaults(fn=cmd_leaderboard)

    c = sub.add_parser("check", help="run SQL queries, then check that every number in an answer came from them")
    c.add_argument("--data", default="data"); c.add_argument("--sql", action="append", required=True,
                                                             help="a SELECT query; repeat for several")
    c.add_argument("--answer", required=True, help="the draft answer text, or - to read it from stdin")
    c.add_argument("--question", default=""); c.set_defaults(fn=cmd_check)

    m = sub.add_parser("mcp", help="serve the analyst tools over MCP (stdio) for Claude, Cursor, ...")
    m.add_argument("--data", default="data"); m.set_defaults(fn=cmd_mcp)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
