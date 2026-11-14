"""MCP server: give any AI assistant (Claude Desktop/Code, Cursor, ...) a data analyst whose
numbers are checked.

Tools
  describe_schema()            tables, columns, types, sample rows
  run_sql(query)               one read-only SELECT (same guard as the agent); results are remembered
  check_answer(answer, question="")
                               before replying, the assistant submits its draft: every number in it
                               must appear in a result returned during this session (or in the
                               question). Returns which numbers are not grounded.
  reset_session()              forget remembered results (start a new analysis)

The data directory is fixed when the server starts (`va mcp --data DIR`); the assistant cannot
point it at other files. The checker is deterministic: no model is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .db import Database, SQLError, SQLRejected
from .verify import GroundingPool, extract_claims, verify_answer

INSTRUCTIONS = (
    "You are connected to a read-only analytics database. Compute every figure you report with "
    "run_sql; do not do arithmetic in your head. Before you send an answer that contains numbers, "
    "call check_answer with your draft. If it reports ungrounded numbers, query for them or remove "
    "them, then check again. Say plainly when the data cannot answer the question."
)


@dataclass
class AnalystSession:
    """Transport-independent core of the MCP server (unit-testable without MCP)."""

    db: Database
    _results: list[tuple[list[str], list[tuple]]] = field(default_factory=list)
    queries: int = 0

    def describe_schema(self) -> str:
        return self.db.describe()

    def run_sql(self, query: str) -> str:
        try:
            qr = self.db.run_select(query)
        except SQLRejected as e:
            return f"REJECTED: {e}"
        except SQLError as e:
            return f"ERROR: {e}"
        self.queries += 1
        self._results.append((qr.columns, qr.rows))
        return qr.to_text()

    def check_answer(self, answer: str, question: str = "") -> dict:
        pool = GroundingPool(question)
        for cols, rows in self._results:
            pool.add_result(cols, rows)
        verdict = verify_answer(answer, pool)
        numbers = [c.raw for c in extract_claims(answer)]
        if verdict.ok:
            note = ("All numbers are grounded in query results from this session." if numbers
                    else "The answer contains no numeric claims.")
        else:
            note = ("These numbers were not returned by any query in this session. Compute them with "
                    "run_sql (do the arithmetic in SQL), or remove them, then check again.")
        return {"grounded": verdict.ok, "ungrounded": verdict.ungrounded, "numbers_checked": numbers,
                "queries_in_session": self.queries, "note": note}

    def reset_session(self) -> str:
        self._results.clear()
        self.queries = 0
        return "Session cleared."


def build_server(data_dir: str | Path):
    """Create the MCP server. Supports the MCP Python SDK 2.x (MCPServer) and 1.x (FastMCP)."""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:  # pragma: no cover - depends on installed SDK
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise SystemExit("The MCP extra is not installed: pip install 'verified-analyst[mcp]'") from e

    session = AnalystSession(Database(str(data_dir)))
    server = Server("verified-analyst", instructions=INSTRUCTIONS)

    @server.tool()
    def describe_schema() -> str:
        """List all tables with columns, types, row counts, allowed values of small categorical columns, and sample rows."""
        return session.describe_schema()

    @server.tool()
    def run_sql(query: str) -> str:
        """Run ONE read-only DuckDB SELECT and return up to 100 rows. Results are remembered so check_answer can verify numbers against them."""
        return session.run_sql(query)

    @server.tool()
    def check_answer(answer: str, question: str = "") -> dict:
        """Check a draft answer before sending it: every number must appear in a query result from this session (or in the user's question). Returns grounded true/false and the ungrounded numbers."""
        return session.check_answer(answer, question)

    @server.tool()
    def reset_session() -> str:
        """Forget all remembered query results (start a fresh analysis)."""
        return session.reset_session()

    return server
