import pytest

from verified_analyst.db import SQLError, SQLRejected


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM users",
    "WITH x AS (SELECT 1 a) SELECT * FROM x",
    "SELECT platform, COUNT(*) FROM users GROUP BY 1 ORDER BY 2",
])
def test_select_allowed(db, sql):
    assert db.run_select(sql).rows


@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "DELETE FROM users",
    "UPDATE users SET platform='x'",
    "CREATE TABLE t AS SELECT 1",
    "INSERT INTO users VALUES (1,'2024-01-01','ios','US','organic')",
    "ATTACH 'x.db'",
    "COPY users TO 'out.csv'",
    "PRAGMA database_list",
    "SET enable_external_access=true",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE users",
])
def test_non_select_or_multi_rejected(db, sql):
    with pytest.raises((SQLRejected, SQLError)):
        db.run_select(sql)


def test_table_survives_rejected_drop(db):
    with pytest.raises(SQLRejected):
        db.run_select("DROP TABLE users")
    assert db.run_select("SELECT COUNT(*) FROM users").rows == [(3000,)]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM glob('/*')",
])
def test_filesystem_reads_blocked(db, sql):
    with pytest.raises((SQLRejected, SQLError)):
        db.run_select(sql)


def test_configuration_is_locked_even_for_trusted_code_paths(db):
    import duckdb

    with pytest.raises(duckdb.Error, match="locked"):
        db.con.execute("SET enable_external_access=true")
    # ...and the agent-facing path still cannot read files afterwards
    with pytest.raises((SQLRejected, SQLError)):
        db.run_select("SELECT * FROM read_csv('/etc/passwd')")


def test_row_cap_and_truncation_flag(db):
    r = db.run_select("SELECT * FROM users", max_rows=10)
    assert len(r.rows) == 10 and r.truncated
    r2 = db.run_select("SELECT * FROM users LIMIT 5", max_rows=10)
    assert len(r2.rows) == 5 and not r2.truncated


def test_timeout_cancels_runaway_query(db):
    heavy = "SELECT COUNT(*) FROM range(100000000) a, range(100000000) b"
    with pytest.raises(SQLRejected, match="cancelled"):
        db.run_select(heavy, timeout=0.3)
    assert db.run_select("SELECT 1").rows == [(1,)]  # connection still usable


def test_sql_error_is_distinct_from_rejection(db):
    with pytest.raises(SQLError):
        db.run_select("SELECT nope FROM users")


@pytest.mark.parametrize("sql", [
    "SELECT 1", "select 1", "  \n SELECT 1", "-- note\nSELECT 1", "/* a */ /* b */ select 1",
    "WITH a AS (SELECT 1) SELECT * FROM a", "FROM users", "(SELECT 1)", "VALUES (1)",
    "DESCRIBE users", "SUMMARIZE users",
])
def test_ordinary_queries_still_allowed(sql):
    from verified_analyst.db import Database
    Database.validate_select(sql)


@pytest.mark.parametrize("sql", [
    "PRAGMA database_list", "  pragma database_list", "-- sneaky\nPRAGMA database_list", "/* x */PRAGMA table_info('users')",
])
def test_pragma_rejected_even_if_the_parser_calls_it_a_select(sql):
    from verified_analyst.db import Database
    with pytest.raises((SQLRejected, SQLError)):
        Database.validate_select(sql)
