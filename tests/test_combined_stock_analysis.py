"""tests for scripts.combined_stock_analysis (Kronos 预测 + 历史回测 + 卡片)。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts import combined_stock_analysis as csa
from scripts.combined_stock_analysis import (
    BacktestView,
    KronosView,
    StockCandidate,
    _build_combined_card,
    _build_stock_card,
    _llm_view,
    attach_backtest,
    attach_kronos,
)
from trade_krono_cli.domain.kronos_result import KronosForecastResult


def _mk_result(
    ticker: str = "sh.600519",
    *,
    direction: str = "UP",
    exp_chg: float = 3.2,
    last_close: float = 10.0,
    pred_close: float = 10.32,
    band: dict | None = None,
    vol: float = 0.15,
    horizon: int = 5,
    error: str | None = None,
) -> KronosForecastResult:
    """构造 KronosForecastResult 测试样本。"""
    if band is None:
        band = {"low": -1.0, "high": 7.0}
    return KronosForecastResult(
        ticker=ticker,
        eval_date="2026-10-15",
        horizon=horizon,
        last_close=last_close,
        predicted_close_mean=pred_close,
        predicted_close_final=pred_close,
        expected_change_pct=exp_chg,
        direction=direction,
        volatility_proxy=vol,
        confidence_band=band,
        model_name="kronos-base",
        error=error,
    )


class TestKronosView:
    """KronosView.from_result / available / summary_line / to_payload。"""

    def test_from_none(self) -> None:
        """None 输入返回默认视图，available=False。"""
        v = KronosView.from_result(None)
        assert v.direction == ""
        assert v.expected_change_pct is None
        assert v.available is False
        assert v.summary_line() == "无 Kronos 预测数据"

    def test_from_full_result(self) -> None:
        """完整结果正确映射各字段。"""
        res = _mk_result()
        v = KronosView.from_result(res)
        assert v.direction == "UP"
        assert v.expected_change_pct == 3.2
        assert v.last_close == 10.0
        assert v.predicted_close == 10.32
        assert v.confidence_low == -1.0
        assert v.confidence_high == 7.0
        assert v.volatility_proxy == 0.15
        assert v.horizon == 5
        assert v.available is True

    def test_error_result_not_available(self) -> None:
        """error 结果（direction 空、收益 None）视为不可用。"""
        res = _mk_result(direction="", exp_chg=0.0, error="boom")
        # 模拟真实失败：direction 空且 expected_change_pct None
        res.direction = ""
        res.expected_change_pct = None
        v = KronosView.from_result(res)
        assert v.available is False

    def test_summary_line_full(self) -> None:
        """摘要包含方向 / 预期收益 / 置信区间 / 周期。"""
        v = KronosView.from_result(_mk_result())
        line = v.summary_line()
        assert "方向=UP" in line
        assert "+3.2%" in line
        assert "置信" in line
        assert "预测5日" in line

    def test_to_payload_keys(self) -> None:
        """payload 含全部展示字段。"""
        v = KronosView.from_result(_mk_result())
        p = v.to_payload()
        for k in (
            "direction",
            "expected_change_pct",
            "last_close",
            "predicted_close",
            "confidence_low",
            "confidence_high",
            "volatility_proxy",
            "horizon",
        ):
            assert k in p


class TestBacktestView:
    """BacktestView.summary_line / to_payload / available。"""

    def test_empty_unavailable(self) -> None:
        """无信号历史时摘要为占位文案。"""
        v = BacktestView(available=False)
        assert v.summary_line() == "无历史回测数据"
        assert v.to_payload()["available"] is False

    def test_summary_full(self) -> None:
        """含全部指标时摘要包含收益 / 夏普 / 回撤 / 胜率。"""
        v = BacktestView(
            total_return_pct=12.5,
            sharpe_ratio=1.8,
            max_drawdown_pct=4.2,
            win_rate_pct=60.0,
            n_trades=8,
            n_signals=10,
            horizon_days=5,
            window_days=90,
            available=True,
        )
        line = v.summary_line()
        assert "近90天" in line
        assert "10 笔信号" in line
        assert "+12.5%" in line
        assert "夏普 1.80" in line
        assert "最大回撤 4.2%" in line
        assert "胜率 60%" in line

    def test_payload_keys(self) -> None:
        """payload 含全部字段。"""
        v = BacktestView(n_signals=3, available=True)
        p = v.to_payload()
        for k in (
            "total_return_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "win_rate_pct",
            "n_trades",
            "n_signals",
            "horizon_days",
            "window_days",
            "available",
        ):
            assert k in p


class TestAttachKronos:
    """attach_kronos 批量预测填充 cand.kronos。"""

    def test_empty_cands(self) -> None:
        """无候选返回 0，不触发任何调用。"""
        assert attach_kronos([], "2026-10-15") == 0

    def test_fills_successes_only(self) -> None:
        """仅成功（无 error）结果写入，失败的跳过。"""
        cands = [
            StockCandidate(code="600519", hit_count=2, sources=["whitelist"]),
            StockCandidate(code="000858", hit_count=2, sources=["whitelist"]),
        ]
        ok = _mk_result("sh.600519")
        fail = _mk_result("sz.000858", error="no data")
        with patch(
            "trade_krono_cli.models.kronos_session.KronosSession"
        ) as mock_cls:
            session = MagicMock()
            session.runner.predict_batch.return_value = [ok, fail]
            mock_cls.return_value = session
            n = attach_kronos(cands, "2026-10-15")
        assert n == 1
        assert cands[0].kronos is ok
        assert cands[1].kronos is None
        session.runner.predict_batch.assert_called_once()
        args = session.runner.predict_batch.call_args[0]
        assert args[0] == ["sh.600519", "sz.000858"]
        assert args[1] == "2026-10-15"

    def test_batch_failure_returns_zero(self) -> None:
        """predict_batch 抛异常不阻断，返回 0 且候选未填充。"""
        cands = [StockCandidate(code="600519", hit_count=2)]
        with patch(
            "trade_krono_cli.models.kronos_session.KronosSession"
        ) as mock_cls:
            session = MagicMock()
            session.runner.predict_batch.side_effect = RuntimeError("model oom")
            mock_cls.return_value = session
            n = attach_kronos(cands, "2026-10-15")
        assert n == 0
        assert cands[0].kronos is None


class TestAttachBacktest:
    """attach_backtest 逐只回测填充 cand.backtest。"""

    def _cand(self, code: str = "600519") -> StockCandidate:
        return StockCandidate(code=code, hit_count=2, sources=["whitelist"])

    def _fake_bt(self) -> MagicMock:
        bt = MagicMock()
        bt.total_return_pct = 9.9
        bt.n_trades = 4
        bt.metrics = {
            "sharpe_ratio": 1.2,
            "max_drawdown_pct": 3.3,
            "win_rate_pct": 50.0,
        }
        return bt

    def test_empty_cands(self) -> None:
        """无候选返回 0。"""
        assert attach_backtest([], "2026-10-15") == 0

    def test_with_records(self) -> None:
        """有历史信号时写回 available=True 的 BacktestView。"""
        cand = self._cand()
        fake_records = [object(), object()]
        with patch.object(csa, "_build_eval_records", return_value=fake_records), patch(
            "trade_krono_cli.prediction_eval_backtest.run_backtest",
            return_value=self._fake_bt(),
        ):
            n = attach_backtest([cand], "2026-10-15", days=90, horizon=5)
        assert n == 1
        assert cand.backtest is not None
        assert cand.backtest.available is True
        assert cand.backtest.total_return_pct == 9.9
        assert cand.backtest.n_signals == 2
        assert cand.backtest.sharpe_ratio == 1.2

    def test_no_records_marks_unavailable(self) -> None:
        """无历史信号时写回 available=False 的 BacktestView，不计入成功。"""
        cand = self._cand()
        with patch.object(csa, "_build_eval_records", return_value=[]):
            n = attach_backtest([cand], "2026-10-15")
        assert n == 0
        assert cand.backtest is not None
        assert cand.backtest.available is False

    def test_exception_marks_unavailable(self) -> None:
        """回测抛异常时降级为 available=False，不阻断。"""
        cand = self._cand()
        with patch.object(
            csa, "_build_eval_records", return_value=[object()]
        ), patch(
            "trade_krono_cli.prediction_eval_backtest.run_backtest",
            side_effect=RuntimeError("boom"),
        ):
            n = attach_backtest([cand], "2026-10-15")
        assert n == 0
        assert cand.backtest is not None
        assert cand.backtest.available is False


class TestCardBuilders:
    """卡片构建：Kronos / 回测 区块的开关。"""

    def _cand(self) -> StockCandidate:
        c = StockCandidate(code="600519", hit_count=3, sources=["whitelist", "resonance"])
        c.analysis = {
            "name": "贵州茅台",
            "trend": "多头",
            "ma_signal": "金叉",
            "vol_signal": "放量",
            "score": 78,
            "pe_ttm": 18.0,
            "pb": 3.5,
            "valuation_score": 60,
            "valuation_conclusion": "估值适中",
        }
        c.kronos = _mk_result()
        c.backtest = BacktestView(
            total_return_pct=9.9,
            n_trades=4,
            n_signals=10,
            horizon_days=5,
            window_days=90,
            available=True,
        )
        return c

    def test_stock_card_shows_kronos_and_backtest(self) -> None:
        """开启数据时卡片含 Kronos 与历史回测区块。"""
        card = _build_stock_card(self._cand(), "2026-10-15", ai_note="看好")
        assert "Kronos 预测" in card
        assert "历史回测" in card
        assert "AI 核实" in card
        assert "贵州茅台" in card

    def test_stock_card_hides_when_absent(self) -> None:
        """无 Kronos / 回测数据时不渲染对应区块。"""
        c = StockCandidate(code="000858", hit_count=2, sources=["whitelist"])
        c.analysis = {"name": "五粮液", "trend": "", "ma_signal": "", "vol_signal": "", "score": 50}
        card = _build_stock_card(c, "2026-10-15")
        assert "Kronos 预测" not in card
        assert "历史回测" not in card

    def test_combined_card_includes_prediction_lines(self) -> None:
        """汇总卡片在候选下追加 预测 / 回测 行。"""
        cands = [self._cand()]
        source_sets = {"whitelist": {"600519"}, "resonance": {"600519"}}
        card = _build_combined_card(cands, "2026-10-15", source_sets)
        assert "预测:" in card
        assert "回测:" in card

    def test_llm_view_includes_kronos(self) -> None:
        """kronos 可用时 LLM 视图含 kronos payload。"""
        c = self._cand()
        view = _llm_view(c)
        assert "kronos" in view
        kronos_payload = view["kronos"]
        assert isinstance(kronos_payload, dict)
        assert kronos_payload.get("direction") == "UP"

    def test_llm_view_omits_kronos_when_absent(self) -> None:
        """kronos 不可用时 LLM 视图不含 kronos 键。"""
        c = StockCandidate(code="600519", hit_count=2)
        c.analysis = {"name": "贵州茅台"}
        view = _llm_view(c)
        assert "kronos" not in view


class TestMainFlagWiring:
    """main() 默认不启用 kronos/backtest，避免重模型加载。"""

    def test_defaults_do_not_invoke_attach(self) -> None:
        """--kronos / --backtest 缺省时 attach 不被调用。"""
        import argparse

        cands = [StockCandidate(code="600519", hit_count=2)]
        captured: dict[str, object] = {}

        def fake_collect(_date: str) -> dict[str, set[str]]:
            return {"whitelist": {"600519"}, "resonance": {"600519"}}

        with patch.object(csa, "collect_source_sets", side_effect=fake_collect), patch.object(
            csa, "build_intersection", return_value=cands
        ), patch.object(
            csa, "analyze_candidates", return_value=cands
        ), patch.object(
            csa, "ai_verify", return_value={
                "analysis_summary": "ok",
                "top_picks": "",
                "risk_alerts": "",
                "conclusion": "",
            }
        ), patch.object(
            csa, "push_feishu", return_value=True
        ), patch.object(
            csa, "attach_kronos"
        ) as mk, patch.object(
            csa, "attach_backtest"
        ) as mb:
            argv = [
                "--date",
                "2026-10-15",
                "--push",
                "1",
                "--per-stock-push",
                "0",
            ]
            # main() 内部调用 parser.parse_args()，注入 argv
            with patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(
                date="2026-10-15",
                min_sources=2,
                max_analyze=60,
                push=1,
                per_stock_push=0,
                kronos=0,
                backtest=0,
                backtest_days=90,
                backtest_horizon=5,
            ), autospec=False), patch("sys.argv", argv):
                csa.main()
                captured["kronos_called"] = mk.called
                captured["backtest_called"] = mb.called
        assert captured["kronos_called"] is False
        assert captured["backtest_called"] is False

    def test_flags_trigger_attach(self) -> None:
        """--kronos / --backtest 开启时调用对应 attach。"""
        import argparse

        cands = [StockCandidate(code="600519", hit_count=2)]
        with patch.object(csa, "collect_source_sets", return_value={"whitelist": {"600519"}}), patch.object(
            csa, "build_intersection", return_value=cands
        ), patch.object(
            csa, "analyze_candidates", return_value=cands
        ), patch.object(
            csa, "ai_verify", return_value={
                "analysis_summary": "ok",
                "top_picks": "",
                "risk_alerts": "",
                "conclusion": "",
            }
        ), patch.object(
            csa, "push_feishu", return_value=True
        ), patch.object(
            csa, "attach_kronos", return_value=1
        ), patch.object(
            csa, "attach_backtest", return_value=1
        ):
            ns = argparse.Namespace(
                date="2026-10-15",
                min_sources=2,
                max_analyze=60,
                push=1,
                per_stock_push=0,
                kronos=1,
                backtest=1,
                backtest_days=90,
                backtest_horizon=5,
            )
            with patch("argparse.ArgumentParser.parse_args", return_value=ns), patch(
                "sys.argv", ["--date", "2026-10-15"]
            ), patch.object(csa, "attach_kronos") as mk, patch.object(
                csa, "attach_backtest"
            ) as mb:
                csa.main()
                assert mk.called is True
                assert mb.called is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
