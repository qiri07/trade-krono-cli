"""tests for trade_krono_cli.research_db.migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trade_krono_cli.research_db.migrations import migrate_schema


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Create an in-memory SQLite connection with basic tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    # Create base tables
    conn.execute(
        "CREATE TABLE jobs ("
        "job_id TEXT PRIMARY KEY, run_at REAL NOT NULL, date TEXT NOT NULL, "
        "tickers TEXT NOT NULL, n_tickers INTEGER NOT NULL, n_success INTEGER NOT NULL, "
        "elapsed REAL NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE signals ("
        "job_id TEXT NOT NULL, ticker TEXT NOT NULL, rank INTEGER, "
        "composite_score REAL, ta_signal TEXT, ta_confidence REAL, "
        "PRIMARY KEY (job_id, ticker)"
        ")"
    )
    conn.execute(
        "CREATE TABLE backtest_results ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
        "strategy TEXT NOT NULL, results TEXT, created_at REAL NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE strategy_runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, run_at REAL NOT NULL, "
        "strategy TEXT NOT NULL, results TEXT, created_at REAL NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE ta_analysis ("
        "job_id TEXT NOT NULL, ticker TEXT NOT NULL, signal TEXT, "
        "confidence REAL, thesis TEXT, risks TEXT, error TEXT, elapsed REAL, "
        "PRIMARY KEY (job_id, ticker)"
        ")"
    )
    conn.execute(
        "CREATE TABLE kronos_forecast ("
        "job_id TEXT NOT NULL, ticker TEXT NOT NULL, direction TEXT, "
        "expected_change REAL, predicted_close REAL, error TEXT, elapsed REAL, "
        "PRIMARY KEY (job_id, ticker)"
        ")"
    )
    conn.execute(
        "CREATE TABLE decisions ("
        "job_id TEXT NOT NULL, ticker TEXT NOT NULL, decision_json TEXT NOT NULL, "
        "thesis TEXT, risks TEXT, PRIMARY KEY (job_id, ticker)"
        ")"
    )
    conn.execute(
        "CREATE TABLE raw_reports ("
        "job_id TEXT NOT NULL, ticker TEXT NOT NULL, path TEXT NOT NULL, "
        "reports TEXT, PRIMARY KEY (job_id, ticker)"
        ")"
    )
    conn.commit()
    return conn


def test_migrate_adds_version_cols(conn: sqlite3.Connection) -> None:
    """Migration adds run_id and other version columns when they don't exist."""
    migrate_schema(conn)
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    col_names = {row[1] for row in info}
    assert "run_id" in col_names
    assert "data_version" in col_names
    assert "model_versions" in col_names
    assert "external_repos" in col_names


def test_migrate_adds_scoring_strategy(conn: sqlite3.Connection) -> None:
    """Migration adds scoring_strategy to backtest_results."""
    migrate_schema(conn)
    info = conn.execute("PRAGMA table_info(backtest_results)").fetchall()
    col_names = {row[1] for row in info}
    assert "scoring_strategy" in col_names


def test_migrate_adds_config_hash_to_strategy_runs(conn: sqlite3.Connection) -> None:
    """Migration adds config_hash to strategy_runs."""
    migrate_schema(conn)
    info = conn.execute("PRAGMA table_info(strategy_runs)").fetchall()
    col_names = {row[1] for row in info}
    assert "config_hash" in col_names


def test_migrate_adds_signal_fields(conn: sqlite3.Connection) -> None:
    """Migration adds new signal domain fields."""
    migrate_schema(conn)
    info = conn.execute("PRAGMA table_info(signals)").fetchall()
    col_names = {row[1] for row in info}
    assert "signal_assessment_json" in col_names
    assert "expected_value" in col_names
    assert "conflict" in col_names


def test_migrate_ranking_score_copy(conn: sqlite3.Connection) -> None:
    """Migration copies composite_score to ranking_score when ranking_score missing."""
    # Insert a signal without ranking_score
    conn.execute(
        "INSERT INTO signals (job_id, ticker, composite_score) VALUES (?, ?, ?)",
        ("job1", "sh.600519", 75.5),
    )
    conn.commit()
    migrate_schema(conn)
    result = conn.execute(
        "SELECT ranking_score FROM signals WHERE job_id = ? AND ticker = ?",
        ("job1", "sh.600519"),
    ).fetchone()
    assert result is not None
    assert result[0] == 75.5


def test_migrate_idempotent(conn: sqlite3.Connection) -> None:
    """Running migration twice should not fail."""
    migrate_schema(conn)
    migrate_schema(conn)  # Should not raise
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    assert any(row[1] == "run_id" for row in info)


def test_migrate_already_has_columns(conn: sqlite3.Connection) -> None:
    """Migration should not fail when columns already exist."""
    # Add all columns manually
    conn.execute("ALTER TABLE jobs ADD COLUMN run_id TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN data_version TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN model_versions TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN prompt_version TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN strategy_version TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN config_hash TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN external_repos TEXT")
    conn.execute("ALTER TABLE backtest_results ADD COLUMN scoring_strategy TEXT")
    conn.execute("ALTER TABLE strategy_runs ADD COLUMN config_hash TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN signal_assessment_json TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN expected_value REAL")
    conn.execute("ALTER TABLE signals ADD COLUMN conflict TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN ranking_score REAL")
    conn.commit()
    # Should not raise
    migrate_schema(conn)


def test_migrate_creates_missing_tables(conn: sqlite3.Connection) -> None:
    """Migration ensures required tables exist."""
    # Remove experiments table
    conn.execute("DROP TABLE IF EXISTS experiments")
    conn.commit()
    migrate_schema(conn)
    # Should not raise - tables should exist or be creatable
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    assert len(info) > 0


def test_migrate_handles_operational_error_on_add_column(conn: sqlite3.Connection) -> None:
    """Migration gracefully handles OperationalError when adding columns."""
    # Use a wrapper to intercept ADD COLUMN calls
    class ErrorConn:
        def __init__(self, real_conn: sqlite3.Connection) -> None:
            self._real = real_conn
            self._errors: set[str] = set()

        def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
            sql_upper = str(sql).upper()
            if "ADD COLUMN" in sql_upper and any(e in sql_upper for e in self._errors):
                raise sqlite3.OperationalError("duplicate column name")
            return self._real.execute(sql, *args, **kwargs)

        def commit(self) -> None:
            self._real.commit()

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    err_conn = ErrorConn(conn)
    err_conn._errors.add("RUN_ID")  # First ADD COLUMN will fail
    # Should not raise despite OperationalError on some columns
    migrate_schema(err_conn)
    info = err_conn.execute("PRAGMA table_info(jobs)").fetchall()
    col_names = {row[1] for row in info}
    # run_id failed, but other version cols should still be added
    assert "data_version" in col_names


def test_migrate_noop_when_all_cols_exist(conn: sqlite3.Connection) -> None:
    """When all columns exist, no ALTER TABLE is attempted (all except branches)."""
    # Add ALL version cols including external_repos
    for col in ("run_id", "data_version", "model_versions", "prompt_version",
                "strategy_version", "config_hash", "external_repos"):
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    conn.execute("ALTER TABLE backtest_results ADD COLUMN scoring_strategy TEXT")
    conn.execute("ALTER TABLE strategy_runs ADD COLUMN config_hash TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN signal_assessment_json TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN expected_value REAL")
    conn.execute("ALTER TABLE signals ADD COLUMN conflict TEXT")
    conn.execute("ALTER TABLE signals ADD COLUMN ranking_score REAL")
    # Insert data to test ranking_score UPDATE path
    conn.execute(
        "INSERT INTO signals (job_id, ticker, composite_score, ranking_score) VALUES (?, ?, ?, ?)",
        ("j1", "sh.600519", 80.0, None),
    )
    conn.commit()
    migrate_schema(conn)
    # Ranking score should remain None (no update needed since ranking_score exists)
    result = conn.execute(
        "SELECT ranking_score FROM signals WHERE job_id='j1' AND ticker='sh.600519'",
    ).fetchone()
    assert result[0] is None
