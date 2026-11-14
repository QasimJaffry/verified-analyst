from verified_analyst.agent import run_agent

from conftest import Scripted, call, final


def test_happy_path_query_then_verified_answer(db):
    p = Scripted([
        call("describe_schema", "a"),
        call("run_sql", "b", query="SELECT COUNT(*) FROM users"),
        final(answer="There are 3000 users.", value=3000),
    ])
    r = run_agent("How many users?", db, p)
    assert (r.status, r.verified, r.value, r.ungrounded) == ("answered", True, 3000, [])
    assert r.steps == 3 and r.sql == ["SELECT COUNT(*) FROM users"]
    assert r.usage.input_tokens == 30 and r.usage.output_tokens == 15


def test_invented_number_is_bounced_then_fixed(db):
    p = Scripted([
        call("run_sql", "q", query="SELECT COUNT(*) FROM users"),
        final(answer="There are 3100 users.", value=3100, id="f1"),      # wrong, not in any result
        final(answer="There are 3000 users.", value=3000, id="f2"),
    ])
    r = run_agent("How many users?", db, p)
    assert r.verified and r.value == 3000 and r.ungrounded == []
    bounced = [m for m in p.seen[-1] if m["role"] == "tool" and m["tool_call_id"] == "f1"][0]
    assert "REJECTED" in bounced["content"] and "3100" in bounced["content"]


def test_persistent_hallucination_returns_unverified_with_offenders(db):
    p = Scripted([final(answer="It is 777.", value=777, id=f"f{i}") for i in range(3)])
    r = run_agent("How many?", db, p, max_verify_retries=2)
    assert r.status == "answered" and not r.verified and r.ungrounded == ["777"]
    assert r.steps == 3


def test_numbers_in_a_refusal_are_not_policed(db):
    r = run_agent("avg age?", db, Scripted([final("cannot_answer", "There is no age column among the 3 tables.")]))
    assert r.status == "cannot_answer" and r.verified


def test_sql_error_goes_back_to_the_model_and_it_recovers(db):
    p = Scripted([
        call("run_sql", "bad", query="SELECT nope FROM users"),
        call("run_sql", "good", query="SELECT COUNT(*) FROM users"),
        final(answer="3000 users", value=3000),
    ])
    r = run_agent("How many users?", db, p)
    err = [m for m in p.seen[1] if m["role"] == "tool"][0]["content"]
    assert err.startswith("ERROR") and r.verified


def test_destructive_sql_is_refused_and_data_survives(db):
    p = Scripted([call("run_sql", "x", query="DROP TABLE users"),
                  call("run_sql", "y", query="SELECT COUNT(*) FROM users"),
                  final(answer="3000", value=3000)])
    r = run_agent("How many users?", db, p)
    assert "only SELECT" in [m for m in p.seen[1] if m["role"] == "tool"][0]["content"]
    assert r.verified and r.value == 3000


def test_no_tool_call_is_nudged_once_then_gives_up(db):
    from verified_analyst.providers.base import Turn
    p = Scripted([Turn("I think it is 5"), Turn("still prose")])
    r = run_agent("q", db, p)
    assert r.status == "no_answer" and r.steps == 2


def test_max_steps_is_enforced(db):
    p = Scripted([call("describe_schema", f"d{i}") for i in range(3)])
    r = run_agent("q", db, p, max_steps=3)
    assert r.status == "no_answer" and r.steps == 3


def test_provider_exception_becomes_error_result_not_crash(db):
    class Boom:
        name = "boom"
        def complete(self, *a):
            raise RuntimeError("network down")
    r = run_agent("q", db, Boom())
    assert r.status == "error" and "network down" in r.error


def test_invalid_final_answer_status_is_rejected_and_retried(db):
    p = Scripted([final(status="maybe", answer="x", id="bad"), final("cannot_answer", "no such data", id="ok")])
    r = run_agent("q", db, p)
    assert r.status == "cannot_answer"


def test_chart_is_built_from_last_query_rows(db):
    p = Scripted([
        call("run_sql", "q", query="SELECT platform, COUNT(*) AS n FROM users GROUP BY 1 ORDER BY 1"),
        final(answer="android 1616, ios 1384", value=[["android", 1616], ["ios", 1384]],
              chart={"type": "bar", "x": "platform", "y": "n"}),
    ])
    r = run_agent("users per platform?", db, p)
    assert r.verified and r.chart["data"]["values"] == [{"platform": "android", "n": 1616}, {"platform": "ios", "n": 1384}]


def test_bad_chart_request_bounces_without_burning_a_verify_retry(db):
    p = Scripted([
        call("run_sql", "q", query="SELECT COUNT(*) AS n FROM users"),
        final(answer="3000", value=3000, chart={"type": "bar", "x": "zzz", "y": "n"}, id="f1"),
        final(answer="3000", value=3000, id="f2"),
    ])
    r = run_agent("how many?", db, p)
    assert r.verified and r.chart is None


def test_parallel_tool_calls_final_answer_is_processed_last(db):
    from verified_analyst.providers.base import ToolCall, Turn
    p = Scripted([Turn(None, [
        ToolCall("fa", "final_answer", {"status": "answered", "answer": "3000 users", "value": 3000}),
        ToolCall("rs", "run_sql", {"query": "SELECT COUNT(*) FROM users"}),
    ])])
    r = run_agent("how many?", db, p)
    assert r.verified  # the query result was available when the answer was checked
