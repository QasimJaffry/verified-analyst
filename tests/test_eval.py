"""Harness validation: the scorer and verifier must accept a faithful solver and reject every mutant."""
from __future__ import annotations

from pathlib import Path

import pytest

from verified_analyst.agent import AgentResult
from verified_analyst.db import QueryResult
from verified_analyst.evals import load_tasks, run_eval, summarize, write_outputs
from verified_analyst.evals.reference import reference_factory
from verified_analyst.evals.runner import metadata
from verified_analyst.evals.scoring import score
from verified_analyst.evals.stats import percentile, wilson
from verified_analyst.evals.tasks import Task

TASKS_PATH = Path(__file__).parent.parent / "evals" / "tasks.jsonl"


@pytest.fixture(scope="module")
def tasks():
    return load_tasks(TASKS_PATH)


def rate(db, tasks, mode, only=None):
    ts = [t for t in tasks if only is None or only(t)]
    recs = run_eval(db, ts, reference_factory(mode))
    return recs, sum(r.passed for r in recs) / len(recs)


def test_oracle_passes_every_task(db, tasks):
    recs, r = rate(db, tasks, "oracle")
    assert r == 1.0, [(x.task_id, x.reason) for x in recs if not x.passed]


@pytest.mark.parametrize("mode", ["wrong_value", "tampered_prose", "hallucinate", "refuse_all"])
def test_mutants_fail_every_answerable_task(db, tasks, mode):
    recs, _ = rate(db, tasks, mode, only=lambda t: t.expected_status == "answered")
    assert not any(r.passed for r in recs), [r.task_id for r in recs if r.passed]


def test_overconfident_fails_every_unanswerable_and_ambiguous_task(db, tasks):
    recs, _ = rate(db, tasks, "overconfident", only=lambda t: t.expected_status != "answered")
    assert recs and not any(r.passed for r in recs)


def test_refuse_all_gets_only_the_refusal_tasks_right(db, tasks):
    recs, _ = rate(db, tasks, "refuse_all")
    passed = {r.task_id for r in recs if r.passed}
    assert passed == {"U01", "U02", "U03"}   # not C01/C02: those need needs_clarification


def test_tampered_prose_is_caught_by_the_verifier_not_the_scorer(db, tasks):
    recs, _ = rate(db, tasks, "tampered_prose", only=lambda t: t.id in ("L01", "A04"))
    assert all(r.correct and not r.grounded and r.ungrounded for r in recs)


def test_wrong_value_is_caught_by_the_scorer_not_the_verifier(db, tasks):
    recs, _ = rate(db, tasks, "wrong_value", only=lambda t: t.id in ("L01", "A02", "A04"))
    assert all(r.grounded and not r.correct for r in recs)


def test_summary_and_outputs(db, tasks, tmp_path):
    recs = run_eval(db, tasks, reference_factory("oracle"), runs=2)
    meta = metadata("reference:oracle", db.data_dir, TASKS_PATH, 2)
    s = write_outputs(tmp_path, recs, meta)
    assert s["overall"]["n"] == 52 and s["overall"]["pass_rate"] == 1.0
    assert set(s["by_category"]) == {"lookup", "aggregate", "join", "time", "unanswerable", "ambiguous"}
    assert (tmp_path / "runs.csv").exists() and "Wilson" in (tmp_path / "RESULTS.md").read_text()
    assert len(meta["dataset_sha256"]) == 12


# -- scorer unit tests (no agent) ---------------------------------------------------------
def R(status="answered", value=None, verified=True, ungrounded=None):
    return AgentResult(status=status, value=value, verified=verified, ungrounded=ungrounded or [])


def T(**kw):
    base = dict(id="x", category="lookup", question="q", gold_sql="select 1")
    base.update(kw)
    return Task(**base)


@pytest.mark.parametrize("value,ok", [
    (12.35, True), (12.3456, True), ("12.35", True), ("$12.35", True), ([12.35], True),
    ([[12.35]], True), ([{"r": 12.35}], True), (12.4, False), (None, False), ("n/a", False),
])
def test_scalar_scoring_forms(value, ok):
    g = QueryResult(["x"], [(12.35,)])
    assert score(T(), R(value=value), g).correct is ok


def test_label_scoring_is_case_and_space_insensitive():
    g = QueryResult(["x"], [("US",)])
    assert score(T(answer_type="label"), R(value=" us "), g).correct
    assert not score(T(answer_type="label"), R(value="GB"), g).correct


def test_table_scoring_ignores_row_order_column_names_and_float_noise():
    g = QueryResult(["p", "n"], [("ios", 40.8), ("android", 34.6)])
    t = T(answer_type="table")
    assert score(t, R(value=[{"platform": "android", "rate": 34.6}, {"platform": "ios", "rate": 40.80001}]), g).correct
    assert not score(t, R(value=[["ios", 40.8]]), g).correct                      # missing row
    assert not score(t, R(value=[["ios", 40.8], ["android", 35.9]]), g).correct   # wrong number
    assert not score(t, R(value="ios 40.8"), g).correct                            # not rows


def test_ungrounded_answer_cannot_pass_even_if_value_is_right():
    g = QueryResult(["x"], [(5,)])
    s = score(T(), R(value=5, verified=False, ungrounded=["99"]), g)
    assert s.correct and not s.grounded and not s.passed


def test_expected_refusal_scoring():
    t = T(expected_status="cannot_answer", gold_sql=None, answer_type="none")
    assert score(t, R("cannot_answer"), None).passed
    assert not score(t, R("answered", value=1), None).passed
    assert not score(t, R("needs_clarification"), None).passed


# -- stats ----------------------------------------------------------------------------------
def test_wilson_known_values():
    lo, hi = wilson(0, 10); assert lo == 0 and 0.27 < hi < 0.28      # 0/10 -> [0, 0.278]
    lo, hi = wilson(10, 10); assert hi == 1 and 0.72 < lo < 0.73
    lo, hi = wilson(50, 100); assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 0) == (0.0, 0.0)


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 0) == 1 and percentile([1, 2, 3, 4], 100) == 4
    assert percentile([7], 95) == 7
