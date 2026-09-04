"""测试 eval_report.py — 评估报告持久化与输出。

覆盖：store_summary / get_latest_evaluation / print_report。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from trade_krono_cli.eval_data import EvalRecord, EvaluationSummary, HorizonMetrics
from trade_krono_cli.eval_report import get_latest_evaluation, store_summary

# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_summary() -> EvaluationSummary:
    """构造最小可用 EvaluationSummary。"""
    return EvaluationSummary(
        records=[
            EvalRecord(
                ticker="sh.600519",
                eval_date="2026-08-11",
                horizon_days=5,
                pred_direction="UP",
                pred_return_pct=1.0,
                actual_return_pct=1.5,
                actual_direction="UP",
                is_direction_correct=True,
                error_pct=-0.5,
                ta_signal="BUY",
                composite_score=80.0,
            ),
            EvalRecord(
                ticker="sz.000858",
                eval_date="2026-08-11",
                horizon_days=5,
                pred_direction="DOWN",
                pred_return_pct=-1.0,
                actual_return_pct=-0.5,
                actual_direction="DOWN",
                is_direction_correct=True,
                error_pct=-0.5,
                ta_signal="HOLD",
                composite_score=60.0,
            ),
        ],
        horizons={
            5: HorizonMetrics(
                kronos_dir_accuracy=60.0,
                ta_buy_win_rate=55.0,
                ta_buy_avg_return=1.2,
                combined_buy_up_win_rate=58.0,
                combined_buy_up_avg_return=1.5,
                high_conf_win_rate=62.0,
                high_conf_avg_return=2.0,
            ),
            10: HorizonMetrics(
                kronos_dir_accuracy=55.0,
                ta_buy_win_rate=50.0,
                ta_buy_avg_return=0.8,
                combined_buy_up_win_rate=52.0,
                combined_buy_up_avg_return=1.0,
                high_conf_win_rate=58.0,
                high_conf_avg_return=1.5,
            ),
            20: HorizonMetrics(
                kronos_dir_accuracy=52.0,
                ta_buy_win_rate=48.0,
                ta_buy_avg_return=0.5,
                combined_buy_up_win_rate=50.0,
                combined_buy_up_avg_return=0.8,
                high_conf_win_rate=55.0,
                high_conf_avg_return=1.2,
            ),
        },
    )


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """创建临时数据库路径。"""
    return tmp_path / "eval_test.db"


# ── store_summary ───────────────────────────────────────────────────────────────


class TestStoreSummary:
    def test_basic(self, tmp_db: Path, sample_summary: EvaluationSummary) -> None:
        store_summary(sample_summary, str(tmp_db))
        # 验证数据写入
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM evaluation_results").fetchone()
        conn.close()
        assert row is not None
        assert row[2] is None  # eval_date_range
        assert row[3] == 2  # n_records

    def test_with_date_range(self, tmp_db: Path, sample_summary: EvaluationSummary) -> None:
        store_summary(sample_summary, str(tmp_db), eval_date_range="2026-08-01~2026-08-31")
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT eval_date_range FROM evaluation_results").fetchone()
        conn.close()
        assert row[0] == "2026-08-01~2026-08-31"

    def test_multiple_stores(self, tmp_db: Path, sample_summary: EvaluationSummary) -> None:
        store_summary(sample_summary, str(tmp_db))
        store_summary(sample_summary, str(tmp_db))
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM evaluation_results").fetchone()[0]
        conn.close()
        assert count == 2

    def test_horizon_metrics_all_zero(self, tmp_db: Path) -> None:
        """所有 horizon metrics 为 0 时不应崩溃。"""
        summary = EvaluationSummary(
            records=[],
            horizons={
                5: HorizonMetrics(),
                10: HorizonMetrics(),
                20: HorizonMetrics(),
            },
        )
        store_summary(summary, str(tmp_db))
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM evaluation_results").fetchone()
        conn.close()
        assert row is not None

    def test_creates_table_if_not_exists(
        self, tmp_db: Path, sample_summary: EvaluationSummary
    ) -> None:
        """表不存在时自动创建。"""
        store_summary(sample_summary, str(tmp_db))
        # 第二次调用不应报错
        store_summary(sample_summary, str(tmp_db))


# ── get_latest_evaluation ──────────────────────────────────────────────────────


class TestGetLatestEvaluation:
    def test_empty_db(self, tmp_db: Path) -> None:
        result = get_latest_evaluation(str(tmp_db))
        assert result is None

    def test_returns_latest(self, tmp_db: Path, sample_summary: EvaluationSummary) -> None:
        store_summary(sample_summary, str(tmp_db), eval_date_range="range1")
        store_summary(sample_summary, str(tmp_db), eval_date_range="range2")
        result = get_latest_evaluation(str(tmp_db))
        assert result is not None
        assert result["eval_date_range"] == "range2"
        assert result["n_records"] == 2
        assert "summary" in result

    def test_returns_none_for_nonexistent_db(self) -> None:
        """数据库文件不存在时返回 None。"""
        result = get_latest_evaluation("/nonexistent/path.db")
        assert result is None

    def test_result_structure(self, tmp_db: Path, sample_summary: EvaluationSummary) -> None:
        store_summary(sample_summary, str(tmp_db))
        result = get_latest_evaluation(str(tmp_db))
        assert result is not None
        assert isinstance(result["id"], int)
        assert isinstance(result["eval_at"], float)
        assert isinstance(result["n_records"], int)
        assert isinstance(result["summary"], dict)
        summary = result["summary"]
        assert "kronos_n" in summary
        assert "kronos_dir_accuracy" in summary


# ── print_report（逻辑路径） ───────────────────────────────────────────────────


class TestPrintReport:
    def test_print_report_no_error(self, sample_summary: EvaluationSummary) -> None:
        """print_report 应正常执行不抛异常。"""
        with patch("trade_krono_cli.eval_report.logger.info"):
            from trade_krono_cli.eval_report import print_report

            print_report(sample_summary)  # 不应抛异常

    def test_print_report_custom_horizons(self, sample_summary: EvaluationSummary) -> None:
        with patch("trade_krono_cli.eval_report.logger.info"):
            from trade_krono_cli.eval_report import print_report

            print_report(sample_summary, horizons=[5])  # 只打印 5D
