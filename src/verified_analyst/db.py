"""DuckDB session with a read-only guard.

Safety model (defence in depth, none of it relies on the LLM behaving):
  1. exactly one statement, and it must be a SELECT (checked by DuckDB's parser);
  2. external access is switched off after the CSVs are loaded, so SELECT read_csv('/etc/passwd')
     and friends fail, and the configuration is locked so a query cannot switch it back on;
  3. results are capped and queries are interrupted after a timeout.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

MAX_ROWS = 100
QUERY_TIMEOUT_S = 10.0


class SQLRejected(Exception):
    """The query was refused before or during execution (policy, not a SQL mistake)."""


class SQLError(Exception):
    """The query ran and DuckDB reported an error (the model can fix this)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    sql: str = ""

    def to_text(self, max_rows: int = 50) -> str:
        if not self.rows:
            return f"columns: {', '.join(self.columns)}\n(0 rows)"
        shown = self.rows[:max_rows]
        lines = [" | ".join(self.columns)]
        lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in shown]
        note = ""
        if self.truncated:
            note = f"\n(result truncated to {len(self.rows)} rows; add LIMIT/aggregation)"
        elif len(self.rows) > max_rows:
            note = f"\n(showing {max_rows} of {len(self.rows)} rows)"
        return f"{len(self.rows)} row(s)\n" + "\n".join(lines) + note


@dataclass
class Database:
    data_dir: Path
    con: duckdb.DuckDBPyConnection = field(init=False, repr=False)
    tables: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        csvs = sorted(self.data_dir.glob("*.csv"))
        if not csvs:
            raise FileNotFoundError(f"no .csv files in {self.data_dir} (run `va data` first)")
        self.con = duckdb.connect(":memory:")
        for p in csvs:
            self.con.execute(f'CREATE TABLE "{p.stem}" AS SELECT * FROM read_csv_auto(?)', [str(p)])
            self.tables.append(p.stem)
        self.con.execute("SET enable_external_access=false")
        self.con.execute("SET lock_configuration=true")

    # -- policy -----------------------------------------------------------------
    @staticmethod
    def validate_select(sql: str) -> None:
        try:
            stmts = duckdb.extract_statements(sql)
        except duckdb.Error as e:
            raise SQLError(str(e)) from e
        if len(stmts) != 1:
            raise SQLRejected("send exactly one statement per run_sql call")
        if stmts[0].type != duckdb.StatementType.SELECT:
            raise SQLRejected(f"only SELECT is allowed (got {stmts[0].type.name})")

    # -- execution --------------------------------------------------------------
    def run_select(self, sql: str, max_rows: int = MAX_ROWS,
                   timeout: float = QUERY_TIMEOUT_S) -> QueryResult:
        self.validate_select(sql)
        timer = threading.Timer(timeout, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise SQLRejected(f"query exceeded {timeout:g}s and was cancelled") from e
        except duckdb.Error as e:
            raise SQLError(str(e)) from e
        finally:
            timer.cancel()
        truncated = len(rows) > max_rows
        return QueryResult(cols, [tuple(r) for r in rows[:max_rows]], truncated, sql)

    def run_internal(self, sql: str) -> QueryResult:
        """Unguarded, uncapped. Used only for trusted gold queries in the eval."""
        cur = self.con.execute(sql)
        return QueryResult([d[0] for d in cur.description], [tuple(r) for r in cur.fetchall()], False, sql)

    # -- schema -----------------------------------------------------------------
    def describe(self, sample_rows: int = 3, enum_limit: int = 8) -> str:
        out: list[str] = []
        for t in self.tables:
            n = self.con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            out.append(f"TABLE {t} ({n} rows)")
            cols = self.con.execute(f'DESCRIBE "{t}"').fetchall()
            for name, ctype, *_ in cols:
                note = ""
                if ctype in ("VARCHAR", "BOOLEAN"):
                    vals = [r[0] for r in self.con.execute(
                        f'SELECT DISTINCT "{name}" FROM "{t}" WHERE "{name}" IS NOT NULL '
                        f"ORDER BY 1 LIMIT {enum_limit + 1}").fetchall()]
                    if len(vals) <= enum_limit:
                        note = f"  values: {vals}"
                out.append(f"  - {name} {ctype}{note}")
            sample = self.con.execute(f'SELECT * FROM "{t}" LIMIT {sample_rows}').fetchall()
            out.append(f"  sample: {sample}")
        return "\n".join(out)
