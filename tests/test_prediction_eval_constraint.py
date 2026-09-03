"""测试 prediction_eval.py 交易约束：价格涨跌停、成本模型、约束感知评估。"""

from unittest.mock import patch

import pytest

from trade_krono_cli.prediction_eval import (
    EvalRecord,
    EvaluationSummary,
    _apply_roundtrip_cost,
    _is_price_at_limit,
)


class TestIsPriceAtLimit:
    """测试涨跌停价格检测。"""

    def test_limit_up_detection(self) -> None:
        """涨停价 = prev_close * 1.10，price >= 涨停价 × 0.999 则拦截。"""
        assert _is_price_at_limit("sh.600519", 110.0, 100.0, "up") is True
        assert _is_price_at_limit("sh.600519", 109.99, 100.0, "up") is True  # 容差
        assert _is_price_at_limit("sh.600519", 109.88, 100.0, "up") is False  # 低于容差

    def test_limit_down_detection(self) -> None:
        """跌停价 = prev_close * 0.90，price / limit_down <= 1.001 则拦截。"""
        assert _is_price_at_limit("sh.600519", 90.0, 100.0, "down") is True
        assert _is_price_at_limit("sh.600519", 90.05, 100.0, "down") is True  # 容差内
        assert _is_price_at_limit("sh.600519", 90.20, 100.0, "down") is False  # 超出容差

    def test_gem_limit(self) -> None:
        """创业板 ±20%。"""
        assert _is_price_at_limit("sz.300001", 120.0, 100.0, "up") is True
        assert _is_price_at_limit("sz.300001", 80.0, 100.0, "down") is True

    def test_no_prev_close(self) -> None:
        """prev_close 为 None 时不过滤。"""
        assert _is_price_at_limit("sh.600519", 110.0, None, "up") is False
        assert _is_price_at_limit("sh.600519", 110.0, 0, "up") is False

    def test_disabled_limit_check(self) -> None:
        """Disable limit check 时不拦截。"""
        from trade_krono_cli.constraints_config import ConstraintConfig

        cfg = ConstraintConfig(enable_limit_check=False)
        from trade_krono_cli.trading_constraints import compute_limit_prices

        up, _down = compute_limit_prices(100.0, "sh.600519", config=cfg)
        assert up is None  # 禁用时返回 None


class TestApplyRoundtripCost:
    """测试交易成本扣减。"""

    def test_cost_deducted(self) -> None:
        """17bps 成本应从收益中扣减。"""
        result = _apply_roundtrip_cost(5.0)
        assert result == pytest.approx(4.83, abs=0.01)

    def test_zero_return(self) -> None:
        assert _apply_roundtrip_cost(0.0) == pytest.approx(-0.17, abs=0.01)

    def test_custom_bps(self) -> None:
        assert _apply_roundtrip_cost(3.0, cost_bps=10.0) == pytest.approx(2.9, abs=0.01)


class TestEvalRecordWithConstraints:
    """测试新增的约束字段。"""

    def test_record_with_constraints(self) -> None:
        r = EvalRecord(
            ticker="sh.600519",
            eval_date="2026-01-01",
            horizon_days=5,
            pred_direction="UP",
            pred_return_pct=3.0,
            actual_return_pct=2.5,
            actual_direction="UP",
            is_direction_correct=True,
            error_pct=0.5,
            ta_signal="BUY",
            composite_score=80.0,
            entry_blocked_limit_up=False,
            exit_blocked_limit_down=False,
            cost_bps_applied=17.0,
        )
        assert r.cost_bps_applied == 17.0
        assert r.entry_blocked_limit_up is False
        assert r.exit_blocked_limit_down is False

    def test_summary_tracks_constraints(self) -> None:
        s = EvaluationSummary()
        s.entry_limit_up_blocked = 3
        s.exit_limit_down_blocked = 1
        s.cost_applied_n = 10
        assert s.entry_limit_up_blocked == 3
        assert s.cost_applied_n == 10


class TestEvaluateConstraintAware:
    """测试 evaluate() 对约束的处理逻辑。"""

    def test_limit_up_entry_skipped(self, tmp_path) -> None:
        """买入日涨停 → 该记录被跳过，不计入 eval_records。"""
        from trade_krono_cli.prediction_eval import PredictionEvaluator
        from trade_krono_cli.research_db import ResearchDatabase

        db = tmp_path / "constraint_eval.db"
        research = ResearchDatabase(db_path=db)

        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test", "UP", 3.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        def fake_fetch_kline(ticker, start, end, frequency="d", use_cache=True):
            import pandas as pd

            # 模拟：前一日(2025-12-31) close=100，eval_date(2026-01-01) close=110（涨停）
            # 不包含 horizon 退出日，避免 iloc[-2] 指向错误日期
            return pd.DataFrame(
                {
                    "timestamps": ["2025-12-31", "2026-01-01"],
                    "open": [100.0, 100.0],
                    "high": [100.0, 110.0],
                    "low": [99.0, 108.0],
                    "close": [100.0, 110.0],
                    "volume": [1000.0, 5000.0],
                    "amount": [100000.0, 550000.0],
                },
            )

        _call_idx = [0]

        def fake_get_close(ticker, date_str) -> float | None:
            if "2026-01-01" in date_str:
                return 110.0  # 涨停价
            if "2026-01-06" in date_str:
                return 108.0  # 退出日正常
            return None

        with patch("trade_krono_cli.eval_data.fetch_kline", side_effect=fake_fetch_kline):
            with patch(
                "trade_krono_cli.prediction_eval._get_close_price",
                side_effect=fake_get_close,
            ):
                summary = evaluator.evaluate(store=False)

        # 涨停买入被跳过，eval_records 为空
        assert len(summary.records) == 0
        assert summary.entry_limit_up_blocked == 1
        assert summary.exit_limit_down_blocked == 0

    def test_cost_deducted_in_return(self, tmp_path) -> None:
        """正常信号应扣减 17bps 交易成本。"""
        from trade_krono_cli.prediction_eval import PredictionEvaluator
        from trade_krono_cli.research_db import ResearchDatabase

        db = tmp_path / "cost_eval.db"
        research = ResearchDatabase(db_path=db)

        job_id = research.create_job("2026-01-01", ["sh.600519"])
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO signals (job_id, ticker, rank, composite_score, "
                " ta_signal, ta_confidence, ta_reasoning, kronos_direction, "
                " kronos_change, ta_error, kronos_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "sh.600519", 1, 80.0, "BUY", 85.0, "test", "UP", 5.0, None, None),
            )
            conn.commit()

        evaluator = PredictionEvaluator.__new__(PredictionEvaluator)
        evaluator._research = research
        evaluator.HORIZONS = [5]

        entry_called = [False]
        exit_called = [False]

        def fake_get_close(ticker, date_str) -> float | None:
            if "2026-01-01" in date_str:
                entry_called[0] = True
                return 100.0
            if "2026-01-06" in date_str:
                exit_called[0] = True
                return 105.0
            return None

        # 返回正常 K 线（非涨停）
        def fake_fetch_kline(ticker, start, end, frequency="d", use_cache=True):
            import pandas as pd

            return pd.DataFrame(
                {
                    "timestamps": ["2025-12-31", "2026-01-01", "2026-01-06"],
                    "open": [100.0, 100.0, 100.0],
                    "high": [101.0, 101.0, 106.0],
                    "low": [99.0, 99.0, 99.0],
                    "close": [100.0, 100.0, 105.0],
                    "volume": [1000.0, 1000.0, 1000.0],
                    "amount": [100000.0, 100000.0, 105000.0],
                },
            )

        with patch("trade_krono_cli.eval_data.fetch_kline", side_effect=fake_fetch_kline):
            with patch(
                "trade_krono_cli.prediction_eval._get_close_price",
                side_effect=fake_get_close,
            ):
                summary = evaluator.evaluate(store=False)

        assert len(summary.records) == 1
        r = summary.records[0]
        # 毛收益 = (105-100)/100*100 = 5.0%，扣 17bps = 0.17%
        assert r.actual_return_pct == pytest.approx(4.83, abs=0.01)
        assert r.cost_bps_applied == 17.0
        assert summary.cost_applied_n == 1
        assert summary.entry_limit_up_blocked == 0
