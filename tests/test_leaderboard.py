import json

from verified_analyst.agent import run_agent
from verified_analyst.evals.leaderboard import fetch_openrouter_pricing, model_row, run_leaderboard
from verified_analyst.evals.reference import ReferenceSolver, reference_factory
from verified_analyst.evals.runner import run_eval


def _factory(m):
    return reference_factory(m.split(":", 1)[1])


def test_first_draft_is_recorded_even_when_the_guard_fixes_it(db, tasks):
    t = next(t for t in tasks if t.expected_status == "answered" and t.answer_type == "scalar")
    gold = db.run_internal(t.gold_sql)
    res = run_agent(t.question, db, ReferenceSolver(t, gold, "self_correct"))
    assert res.verified and res.status == "answered"
    assert res.first_draft_ungrounded, "the invented number in the first draft must be kept"
    assert res.rejections == 1
    assert res.ungrounded == []


def test_model_row_metrics(db, tasks):
    recs = run_eval(db, tasks, reference_factory("self_correct"))
    row = model_row("m", recs)
    answerable = [t for t in tasks if t.expected_status == "answered"]
    assert row.invented_rate == 1.0                       # every first draft was tampered
    assert row.saved_by_guard == len(answerable)          # and the guard saved every one
    assert row.pass_rate == 1.0
    assert row.unanswerable_rate == 0.0

    hall = model_row("h", run_eval(db, tasks, reference_factory("hallucinate")))
    assert hall.invented_rate == 1.0 and hall.unanswerable_rate == 1.0 and hall.pass_rate == 0.0
    assert hall.saved_by_guard == 0

    over = model_row("o", run_eval(db, tasks, reference_factory("overconfident")))
    assert over.unanswerable_rate == 1.0
    assert 0 < over.invented_rate < 1                      # only its answers to impossible tasks are invented


def test_cost_uses_the_price_table(db, tasks):
    recs = run_eval(db, tasks[:3], reference_factory("oracle"))
    for r in recs:
        r.input_tokens, r.output_tokens = 1000, 100
    row = model_row("x", recs, {"x": (1e-6, 1e-5)})
    assert row.cost_median_usd == round(1000 * 1e-6 + 100 * 1e-5, 5)
    assert row.cost_total_usd == round(3 * (1000 * 1e-6 + 100 * 1e-5), 4)
    assert model_row("y", recs, {"x": (1, 1)}).cost_median_usd is None


def test_run_leaderboard_writes_ranked_outputs(db, tasks, tmp_path):
    models = ["reference:hallucinate", "reference:oracle", "reference:self_correct"]
    rows = run_leaderboard(db, tasks, models, _factory, tmp_path, runs=1)
    assert [r.model for r in rows][-1] == "reference:hallucinate"
    data = json.loads((tmp_path / "leaderboard.json").read_text())
    assert len(data["models"]) == 3
    md = (tmp_path / "LEADERBOARD.md").read_text()
    assert "reference:self_correct" in md and "invented a number" in md
    page = (tmp_path / "index.html").read_text()
    assert page.startswith("<!DOCTYPE html>") and "reference:oracle" in page
    assert (tmp_path / "reference_oracle" / "summary.json").exists()


def test_html_escapes_model_names(db, tasks, tmp_path):
    rows = run_leaderboard(db, tasks[:2], ["reference:oracle"], _factory, tmp_path)
    from verified_analyst.evals.leaderboard import render_html
    rows[0].model = "<script>alert(1)</script>"
    assert "<script>alert" not in render_html(rows, {})


def test_fetch_openrouter_pricing_parses_and_skips_bad_rows():
    import io

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    body = {"data": [{"id": "a/x", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                     {"id": "b/y", "pricing": {"prompt": "oops"}}, {"id": "c/z", "pricing": {"prompt": "1"}}]}
    out = fetch_openrouter_pricing(["a/x", "b/y"], urlopen=lambda *a, **k: R(json.dumps(body).encode()))
    assert out == {"a/x": (1e-6, 2e-6)}


def test_scripted_solver_rows_are_labelled_but_real_models_are_not(db, tasks):
    from verified_analyst.evals.leaderboard import render_html, render_markdown
    ref = model_row("reference:oracle", run_eval(db, tasks, reference_factory("oracle")))
    real = model_row("openai/gpt-x", run_eval(db, tasks, reference_factory("oracle")))
    meta = {"tasks": len(tasks), "runs_per_task": 1}
    assert "not real models" in render_markdown([ref], meta)
    assert "not real models" in render_html([ref], meta)
    # one real model in the mix means the banner must not appear
    assert "not real models" not in render_markdown([ref, real], meta)
    assert "not real models" not in render_html([real], meta)
