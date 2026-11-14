import asyncio
import json
import os
import sys

import pytest

from verified_analyst.mcp_server import AnalystSession


def test_session_checks_numbers_against_this_sessions_results(db):
    s = AnalystSession(db)
    assert "users" in s.describe_schema()
    out = s.run_sql("SELECT COUNT(*) AS n FROM users")
    n = int(out.split()[-1].replace(",", "")) if out.split()[-1].replace(",", "").isdigit() else None
    total = db.run_internal("SELECT COUNT(*) FROM users").rows[0][0]
    assert str(total) in out.replace(",", "")
    ok = s.check_answer(f"There are {total} users.")
    assert ok["grounded"] and ok["ungrounded"] == [] and ok["queries_in_session"] == 1
    bad = s.check_answer(f"There are {total + 13} users, up 12.5% on last year.")
    assert not bad["grounded"] and len(bad["ungrounded"]) == 2
    # numbers the user wrote in the question are allowed
    assert s.check_answer("Users above 30 days: none.", question="How many users above 30 days?")["grounded"]


def test_session_refuses_writes_and_file_access(db):
    s = AnalystSession(db)
    assert s.run_sql("DROP TABLE users").startswith("REJECTED")
    assert s.run_sql("SELECT * FROM read_csv('/etc/passwd')").startswith(("REJECTED", "ERROR"))
    assert s.run_sql("SELECT nope FROM users").startswith("ERROR")
    assert s.queries == 0


def test_reset_forgets_results(db):
    s = AnalystSession(db)
    s.run_sql("SELECT 4242 AS x")
    assert s.check_answer("4242")["grounded"]
    s.reset_session()
    assert not s.check_answer("4242")["grounded"]


@pytest.mark.skipif(not __import__("importlib").util.find_spec("mcp"), reason="mcp extra not installed")
def test_stdio_round_trip(data_dir):
    """Start `va mcp` as a real subprocess and talk to it over MCP stdio, like Claude Desktop would."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "verified_analyst.cli", "mcp", "--data", str(data_dir)],
                                       env={**os.environ})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                names = sorted(t.name for t in (await session.list_tools()).tools)
                assert names == ["check_answer", "describe_schema", "reset_session", "run_sql"]
                res = await session.call_tool("run_sql", {"query": "SELECT 1234 AS v"})
                assert "1234" in res.content[0].text
                chk = await session.call_tool("check_answer", {"answer": "It is 1234, not 999."})
                payload = (getattr(chk, "structured_content", None) or getattr(chk, "structuredContent", None)
                           or json.loads(chk.content[0].text))
                if "result" in payload and isinstance(payload["result"], dict):
                    payload = payload["result"]
                assert payload["grounded"] is False and payload["ungrounded"] == ["999"]
    asyncio.run(asyncio.wait_for(go(), 60))
