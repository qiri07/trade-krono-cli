"""tests for trade_krono_cli.research_db.schema."""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db.schema import (
    CREATE_SCRIPT,
    REASONING_TRUNCATE_LEN,
    RESEARCH_TABLES,
    validate_table_name,
)


class TestConstants:
    """Module-level constants."""

    def test_reasoning_truncate_len(self) -> None:
        assert REASONING_TRUNCATE_LEN == 500

    def test_research_tables_contains_jobs(self) -> None:
        assert "jobs" in RESEARCH_TABLES

    def test_research_tables_contains_signals(self) -> None:
        assert "signals" in RESEARCH_TABLES

    def test_research_tables_contains_decisions(self) -> None:
        assert "decisions" in RESEARCH_TABLES

    def test_research_tables_contains_ta_analysis(self) -> None:
        assert "ta_analysis" in RESEARCH_TABLES

    def test_research_tables_contains_kronos_forecast(self) -> None:
        assert "kronos_forecast" in RESEARCH_TABLES

    def test_research_tables_contains_committee(self) -> None:
        assert "committee_deliberations" in RESEARCH_TABLES

    def test_research_tables_contains_experiments(self) -> None:
        assert "experiments" in RESEARCH_TABLES

    def test_research_tables_contains_strategy_runs(self) -> None:
        assert "strategy_runs" in RESEARCH_TABLES

    def test_research_tables_contains_backtest_results(self) -> None:
        assert "backtest_results" in RESEARCH_TABLES

    def test_research_tables_is_frozenset(self) -> None:
        assert isinstance(RESEARCH_TABLES, frozenset)

    def test_create_script_not_empty(self) -> None:
        assert len(CREATE_SCRIPT) > 1000

    def test_create_script_contains_jobs(self) -> None:
        assert "CREATE TABLE" in CREATE_SCRIPT
        assert "jobs" in CREATE_SCRIPT


class TestValidateTableName:
    """SQL injection prevention via table name validation."""

    def test_valid_table(self) -> None:
        assert validate_table_name("jobs") == "jobs"

    def test_valid_table_with_custom_allowed(self) -> None:
        allowed = frozenset({"custom_table"})
        assert validate_table_name("custom_table", allowed) == "custom_table"

    def test_invalid_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Unauthorized table"):
            validate_table_name("DROP TABLE users")

    def test_invalid_table_with_custom_allowed(self) -> None:
        allowed = frozenset({"allowed"})
        with pytest.raises(ValueError, match="Unauthorized table"):
            validate_table_name("injected; DROP TABLE", allowed)

    def test_sql_injection_attempts(self) -> None:
        """Common SQL injection patterns should all raise."""
        injections = [
            "users; DROP TABLE jobs",
            "' OR '1'='1",
            "1; DELETE FROM jobs",
            "jobs' UNION SELECT * FROM secrets",
        ]
        for injection in injections:
            with pytest.raises(ValueError):
                validate_table_name(injection)
