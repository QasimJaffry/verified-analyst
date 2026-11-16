"""`va check`: the stateless entry point used by scripts and the agent skill."""
import io
import json

from verified_analyst.cli import main

SQL = "SELECT platform, COUNT(*) AS users FROM users GROUP BY platform ORDER BY users DESC"


def _run(capsys, *args):
    code = main(["check", *args])
    return code, json.loads(capsys.readouterr().out)


def test_grounded_answer_exits_zero(data_dir, db, capsys):
    rows = db.run_select(SQL).rows
    answer = f"{rows[0][0]} leads with {rows[0][1]:,} users."
    code, out = _run(capsys, "--data", str(data_dir), "--sql", SQL, "--answer", answer)
    assert code == 0 and out["grounded"] is True and out["ungrounded"] == []
    assert out["queries"][0]["ok"] is True


def test_invented_number_exits_one_and_is_named(data_dir, capsys):
    code, out = _run(capsys, "--data", str(data_dir), "--sql", SQL, "--answer", "iOS leads with 4,812 users.")
    assert code == 1 and out["grounded"] is False
    assert any("4812" in u.replace(",", "") for u in out["ungrounded"])


def test_rejected_query_exits_two_before_checking(data_dir, capsys):
    code, out = _run(capsys, "--data", str(data_dir), "--sql", "DROP TABLE users", "--answer", "1 user")
    assert code == 2 and out["grounded"] is False and out["error"].startswith("REJECTED")


def test_numbers_must_come_from_the_given_queries_only(data_dir, db, capsys):
    total = db.run_select("SELECT COUNT(*) FROM users").rows[0][0]
    # the true total, but no query here returns it
    code, out = _run(capsys, "--data", str(data_dir), "--sql", SQL, "--answer", f"There are {total} users in total.")
    assert code == 1
    code, out = _run(capsys, "--data", str(data_dir), "--sql", SQL, "--sql", "SELECT COUNT(*) FROM users",
                     "--answer", f"There are {total} users in total.")
    assert code == 0


def test_answer_can_come_from_stdin(data_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("No numbers here."))
    code, out = _run(capsys, "--data", str(data_dir), "--sql", SQL, "--answer", "-")
    assert code == 0 and out["numbers_checked"] == []
