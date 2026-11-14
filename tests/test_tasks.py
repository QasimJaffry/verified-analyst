from pathlib import Path

from verified_analyst.evals import load_tasks

TASKS = Path(__file__).parent.parent / "evals" / "tasks.jsonl"


def test_every_gold_query_runs_and_is_non_empty(db):
    for t in load_tasks(TASKS):
        if t.gold_sql:
            r = db.run_internal(t.gold_sql)
            assert r.rows, t.id
            if t.answer_type in ("scalar", "label"):
                assert len(r.rows) == 1 and len(r.rows[0]) == 1 and r.rows[0][0] is not None, t.id


def test_gold_queries_are_themselves_read_only_selects(db):
    for t in load_tasks(TASKS):
        if t.gold_sql:
            db.validate_select(t.gold_sql)


def test_task_mix_covers_every_category():
    cats = {t.category for t in load_tasks(TASKS)}
    assert cats == {"lookup", "aggregate", "join", "time", "unanswerable", "ambiguous"}
