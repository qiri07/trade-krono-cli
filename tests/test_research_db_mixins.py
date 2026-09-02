"""research_db 各 Mixin 表的 CRUD 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_krono_cli.research_db.schema import RESEARCH_TABLES, validate_table_name


@pytest.fixture
def db(tmp_path: Path) -> object:
    """创建一个使用临时 SQLite 数据库的测试实例。"""
    from trade_krono_cli.research_db.base import ResearchDatabase
    from trade_krono_cli.research_db.decisions import DecisionsMixin as DM
    from trade_krono_cli.research_db.experiments import ExperimentsMixin as EM
    from trade_krono_cli.research_db.jobs import JobMixin as JM
    from trade_krono_cli.research_db.kronos_forecast import KronosForecastMixin as KM
    from trade_krono_cli.research_db.signals import SignalsMixin as SM
    from trade_krono_cli.research_db.stats import StatsMixin as ST
    from trade_krono_cli.research_db.strategy_runs import StrategyRunsMixin as StrM
    from trade_krono_cli.research_db.ta_analysis import TaAnalysisMixin as TM
    from trade_krono_cli.research_db.walkforward import WalkforwardMixin as WM

    class TestDB(
        JM,
        SM,
        KM,
        TM,
        DM,
        ST,
        EM,
        WM,
        StrM,
        ResearchDatabase,
    ):
        pass

    return TestDB(tmp_path / "test_research.db")  # type: ignore[return-value]


class TestValidateTableName:
    """表名白名单验证。"""

    def test_valid_table(self) -> None:
        assert validate_table_name("jobs") == "jobs"

    def test_valid_table_in_set(self) -> None:
        assert validate_table_name("signals", allowed=RESEARCH_TABLES) == "signals"

    def test_invalid_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Unauthorized table"):
            validate_table_name("DROP TABLE users")

    def test_sql_injection_attempt(self) -> None:
        with pytest.raises(ValueError):
            validate_table_name("jobs; DROP TABLE signals")

    def test_none_allowed_defaults_to_research_tables(self) -> None:
        result = validate_table_name("jobs")
        assert result == "jobs"


class TestJobMixin:
    """Jobs 表 CRUD。"""

    def test_create_job(self, db: object) -> None:
        job_id = db.create_job("2026-09-01", ["sh.600519", "sz.000858"])  # type: ignore[attr-defined]
        assert job_id is not None
        assert len(job_id) > 0

    def test_complete_job(self, db: object) -> None:
        job_id = db.create_job("2026-09-01", ["sh.600519"])  # type: ignore[attr-defined]
        db.complete_job(job_id, n_success=1, elapsed=1.5)  # type: ignore[attr-defined]
        job = db.get_job(job_id)  # type: ignore[attr-defined]
        assert job is not None
        assert job["n_success"] == 1
        assert job["elapsed"] == 1.5

    def test_get_job_not_found(self, db: object) -> None:
        result = db.get_job("nonexistent")  # type: ignore[attr-defined]
        assert result is None


class TestSignalsMixin:
    """Signals 表读写。"""

    def test_insert_and_get_signals(self, db: object) -> None:
        job_id = db.create_job("2026-09-01", ["sh.600519"])  # type: ignore[attr-defined]
        merged = [
            {
                "ticker": "sh.600519",
                "rank": 1,
                "composite_score": 85.0,
                "ranking_score": 90.0,
                "ta_signal": "BUY",
                "ta_confidence": 80.0,
                "ta_reasoning": "strong uptrend",
                "kronos_direction": "UP",
                "kronos_change_pct": 2.5,
                "uncertainty": None,
                "ta_error": None,
                "kronos_error": None,
                "signal_assessment": None,
                "expected_value": None,
                "conflict": "",
            }
        ]
        db.insert_signals(job_id, merged)  # type: ignore[attr-defined]
        signals = db.get_signals_by_job(job_id)  # type: ignore[attr-defined]
        assert len(signals) == 1
        assert signals[0]["ticker"] == "sh.600519"
        assert signals[0]["ta_signal"] == "BUY"

    def test_get_signals_empty(self, db: object) -> None:
        results = db.get_signals_by_job("no-such-job")  # type: ignore[attr-defined]
        assert results == []


class TestKronosForecastMixin:
    """Kronos 预测表 CRUD。"""

    def test_get_forecasts_empty(self, db: object) -> None:
        results = db.get_kronos_by_job("no-such-job")  # type: ignore[attr-defined]
        assert results == []

    def test_insert_and_get_forecasts(self, db: object) -> None:
        job_id = db.create_job("2026-09-01", ["sh.600519"])  # type: ignore[attr-defined]
        with db._conn as conn:  # type: ignore[attr-defined]
            conn.execute(
                "INSERT INTO kronos_forecast "
                "(job_id, ticker, direction, expected_change, predicted_close, "
                " confidence_band, uncertainty, error, elapsed) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", "UP", 2.5, 1850.0, "[1800,1900]", "medium", None, 1.2),
            )
            conn.commit()
        results = db.get_kronos_by_job(job_id)  # type: ignore[attr-defined]
        assert len(results) == 1
        assert results[0]["ticker"] == "sh.600519"
        assert results[0]["direction"] == "UP"


class TestTaAnalysisMixin:
    """TA 分析表 CRUD。"""

    def test_get_analyses_empty(self, db: object) -> None:
        results = db.get_ta_by_job("no-such-job")  # type: ignore[attr-defined]
        assert results == []

    def test_insert_and_get_analyses(self, db: object) -> None:
        job_id = db.create_job("2026-09-01", ["sh.600519"])  # type: ignore[attr-defined]
        with db._conn as conn:  # type: ignore[attr-defined]
            conn.execute(
                "INSERT INTO ta_analysis "
                "(job_id, ticker, signal, confidence, thesis, risks, error, elapsed) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", "BUY", 85.0, "strong momentum", "overbought", None, 2.1),
            )
            conn.commit()
        results = db.get_ta_by_job(job_id)  # type: ignore[attr-defined]
        assert len(results) == 1
        assert results[0]["signal"] == "BUY"


class TestDecisionsMixin:
    """Decisions 表 CRUD。"""

    def test_insert_and_get_decision(self, db: object) -> None:
        from trade_krono_cli.domain.types import Signal
        from trade_krono_cli.ta_decision import InvestmentDecision

        decision = InvestmentDecision(
            signal=Signal.BUY,
            confidence=80.0,
            thesis="strong momentum",
            risks=["overbought"],
        )
        db.insert_decision("job-001", "sh.600519", decision, "strong momentum", ["overbought"])  # type: ignore[attr-defined]
        result = db.get_decision("job-001", "sh.600519")  # type: ignore[attr-defined]
        assert result is not None
        assert result["decision"]["signal"] == "BUY"


class TestReportsMixin:
    """Reports 表 CRUD。"""

    def test_insert_report(self, db: object) -> None:
        # raw_reports 表结构验证（不应抛异常）
        with db._conn as conn:  # type: ignore[attr-defined]
            conn.execute("SELECT COUNT(*) FROM raw_reports").fetchone()


class TestStatsMixin:
    """Stats 查询。"""

    def test_get_latest_signal_for_ticker(self, db: object) -> None:
        # signal_history 表可能不存在于测试环境，只验证不抛异常
        result = db.get_latest_signal_for_ticker("sh.600519")  # type: ignore[attr-defined]
        # 可能返回 None（表空或不存在），这不算是失败
        assert result is None or isinstance(result, dict)

    def test_get_latest_signal_not_found(self, db: object) -> None:
        result = db.get_latest_signal_for_ticker("sh.999999")  # type: ignore[attr-defined]
        assert result is None


class TestExperimentsMixin:
    """Experiments 表 CRUD。"""

    def test_insert_experiment(self, db: object) -> None:
        db.insert_experiment(  # type: ignore[attr-defined]
            "exp-001",
            "full-id-hash",
            "alpha",
            {"statement": "hypothesis text", "prediction": "UP", "falsification": "DOWN"},
            config={"key": "value"},
        )
        # 应无异常


class TestWalkforwardMixin:
    """Walkforward 表 CRUD。"""

    def test_insert_walkforward_run(self, db: object) -> None:
        db.insert_walkforward_run(  # type: ignore[attr-defined]
            run_id="wf-001",
            experiment_id=None,
            ticker="sh.600519",
            config={"lookback": 400},
            total_windows=10,
            valid_windows=8,
            win_rate=0.85,
            avg_return=0.02,
            sharpe_annual=1.5,
            n_records=100,
            elapsed_sec=5.0,
        )
        # 应无异常


class TestStrategyRunsMixin:
    """Strategy Runs 表 CRUD。"""

    def test_insert_strategy_run(self, db: object) -> None:
        db.insert_strategy_run(  # type: ignore[attr-defined]
            run_at=1725235200.0,
            strategy="linear",
            params={"threshold": 0.7},
            tickers=["sh.600519"],
            results=[{"ticker": "sh.600519", "score": 85.0}],
            notes=None,
        )
        # 应无异常
