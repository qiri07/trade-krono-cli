"""tests for scripts.combined_stock_analysis (Kronos 预测 + 历史回测 + 卡片)。

拆分后结构：
- 模型/视图：scripts.combined_stock_models
- 收集/交集：scripts.combined_stock_collect / combined_stock_intersect
- 分析增强：scripts.combined_stock_analyze（attach_kronos / attach_backtest）
- 卡片构建：scripts.combined_stock_cards
- 主入口：scripts.combined_stock_analysis
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from trade_krono_cli.eval_data import EvalRecord

from scripts import combined_stock_analysis as csa
from scripts.combined_stock_analyze import (
    _build_eval_records,
    _lookup_signal,
    analyze_candidates,
    attach_backtest,
    attach_kronos,
)
from scripts.combined_stock_cards import (
    _build_combined_card,
    _build_stock_card,
    _llm_view,
)
from scripts.combined_stock_collect import _codes_from_czsc_csv, collect_source_sets
from scripts.combined_stock_intersect import build_intersection
from scripts.combined_stock_models import BacktestView, KronosView, StockCandidate
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
        with patch("trade_krono_cli.models.kronos_session.KronosSession") as mock_cls:
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
        with patch("trade_krono_cli.models.kronos_session.KronosSession") as mock_cls:
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
        with (
            patch(
                "scripts.combined_stock_analyze._build_eval_records",
                return_value=fake_records,
            ),
            patch(
                "trade_krono_cli.prediction_eval_backtest.run_backtest",
                return_value=self._fake_bt(),
            ),
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
        with patch("scripts.combined_stock_analyze._build_eval_records", return_value=[]):
            n = attach_backtest([cand], "2026-10-15")
        assert n == 0
        assert cand.backtest is not None
        assert cand.backtest.available is False

    def test_exception_marks_unavailable(self) -> None:
        """回测抛异常时降级为 available=False，不阻断。"""
        cand = self._cand()
        with (
            patch("scripts.combined_stock_analyze._build_eval_records", return_value=[object()]),
            patch(
                "trade_krono_cli.prediction_eval_backtest.run_backtest",
                side_effect=RuntimeError("boom"),
            ),
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
        """前 10 名候选用 analyzed_map 明细时追加 预测 / 回测 行。"""
        cand = self._cand()
        source_sets = {"whitelist": {"600519"}, "resonance": {"600519"}}
        card = _build_combined_card(
            [cand], "2026-10-15", source_sets, analyzed_map={"600519": cand}
        )
        assert "预测:" in card
        assert "回测:" in card

    def test_combined_card_rest_shows_only_name_and_hits(self) -> None:
        """第 11 名起只列名称与交集次数，不展开技术面 / 基本面明细。"""
        cands = [
            StockCandidate(code=f"{600000 + i}", hit_count=3, sources=["whitelist"])
            for i in range(12)
        ]
        card = _build_combined_card(cands, "2026-10-15", {"whitelist": set()})
        assert "其余候选（仅列出名称与交集次数）" in card
        assert "技术面:" not in card

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


class TestAnalyzeCandidates:
    """analyze_candidates 语义回归（拆分曾丢失 ticker 归一化与 status 过滤）。"""

    def _cand(self, code: str = "600519") -> StockCandidate:
        """构造 1 条来源命中的候选。"""
        return StockCandidate(code=code, hit_count=1, sources=["whitelist"])

    def test_normalizes_ticker_before_analyze(self) -> None:
        """入场前必须归一化为 sh./sz. 前缀，否则缓存键查不到、PE 前缀判错。"""
        cand = self._cand()
        with patch(
            "scripts.combined_stock_analyze.analyze_stock",
            return_value={"status": "ok", "score": 70},
        ) as mock_analyze:
            analyzed = analyze_candidates([cand], "2026-11-05", max_analyze=10)
        mock_analyze.assert_called_once_with("sh.600519", end_date="2026-11-05")
        assert analyzed == [cand]
        assert cand.analysis == {"status": "ok", "score": 70}

    def test_skips_result_without_ok_status(self) -> None:
        """status != "ok"（如 no_data）的候选不计入 analyzed，也不写 analysis。"""
        cand = self._cand("000858")
        with patch(
            "scripts.combined_stock_analyze.analyze_stock",
            return_value={"status": "no_data"},
        ):
            analyzed = analyze_candidates([cand], "2026-11-05", max_analyze=10)
        assert analyzed == []
        assert cand.analysis is None

    def test_sorts_by_rank_score_descending(self) -> None:
        """--sort-by score 依赖函数尾部按 rank_score 降序（hit_count*10 + 技术分）。"""
        low, high = self._cand("000858"), self._cand("600519")
        fake_results: dict[str, dict[str, object]] = {
            "000858": {"status": "ok", "score": 30},
            "600519": {"status": "ok", "score": 80},
        }

        def fake_analyze(ticker: str, **kwargs: object) -> dict[str, object]:
            assert isinstance(ticker, str)
            return fake_results[ticker.split(".", 1)[1]]

        with patch("scripts.combined_stock_analyze.analyze_stock", side_effect=fake_analyze):
            analyzed = analyze_candidates([low, high], "2026-11-05", max_analyze=10)
        assert [c.code for c in analyzed] == ["600519", "000858"]


class TestMainFlagWiring:
    """main() 默认不启用 kronos/backtest，避免重模型加载。"""

    def test_defaults_do_not_invoke_attach(self) -> None:
        """--kronos / --backtest 缺省时 attach 不被调用。"""
        import argparse

        cands = [StockCandidate(code="600519", hit_count=2)]
        captured: dict[str, object] = {}

        def fake_collect(_date: str) -> dict[str, set[str]]:
            return {"whitelist": {"600519"}, "resonance": {"600519"}}

        with (
            patch.object(csa, "collect_source_sets", side_effect=fake_collect),
            patch.object(csa, "build_intersection", return_value=cands),
            patch.object(csa, "analyze_candidates", return_value=cands),
            patch.object(
                csa,
                "ai_verify",
                return_value={
                    "analysis_summary": "ok",
                    "top_picks": "",
                    "risk_alerts": "",
                    "conclusion": "",
                },
            ),
            patch.object(csa, "push_feishu", return_value=True),
            patch.object(csa, "attach_kronos") as mk,
            patch.object(csa, "attach_backtest") as mb,
        ):
            argv = [
                "--date",
                "2026-10-15",
                "--push",
                "1",
                "--per-stock-push",
                "0",
            ]
            # main() 内部调用 parser.parse_args()，注入 argv
            with (
                patch(
                    "argparse.ArgumentParser.parse_args",
                    return_value=argparse.Namespace(
                        date="2026-10-15",
                        min_sources=2,
                        sort_by="hits",
                        max_analyze=60,
                        push=1,
                        per_stock_push=0,
                        kronos=0,
                        backtest=0,
                        backtest_days=90,
                        backtest_horizon=5,
                    ),
                    autospec=False,
                ),
                patch("sys.argv", argv),
            ):
                csa.main()
                captured["kronos_called"] = mk.called
                captured["backtest_called"] = mb.called
        assert captured["kronos_called"] is False
        assert captured["backtest_called"] is False

    def test_flags_trigger_attach(self) -> None:
        """--kronos / --backtest 开启时调用对应 attach。"""
        import argparse

        cands = [StockCandidate(code="600519", hit_count=2)]
        with (
            patch.object(csa, "collect_source_sets", return_value={"whitelist": {"600519"}}),
            patch.object(csa, "build_intersection", return_value=cands),
            patch.object(csa, "analyze_candidates", return_value=cands),
            patch.object(
                csa,
                "ai_verify",
                return_value={
                    "analysis_summary": "ok",
                    "top_picks": "",
                    "risk_alerts": "",
                    "conclusion": "",
                },
            ),
            patch.object(csa, "push_feishu", return_value=True),
            patch.object(csa, "attach_kronos", return_value=1),
            patch.object(csa, "attach_backtest", return_value=1),
        ):
            ns = argparse.Namespace(
                date="2026-10-15",
                min_sources=2,
                sort_by="hits",
                max_analyze=60,
                push=1,
                per_stock_push=0,
                kronos=1,
                backtest=1,
                backtest_days=90,
                backtest_horizon=5,
            )
            with (
                patch("argparse.ArgumentParser.parse_args", return_value=ns),
                patch("sys.argv", ["--date", "2026-10-15"]),
                patch.object(csa, "attach_kronos") as mk,
                patch.object(csa, "attach_backtest") as mb,
            ):
                csa.main()
                assert mk.called is True
                assert mb.called is True


class TestBuildIntersection:
    """build_intersection 语义回归（来源序为遍历序，同条数保持首现序稳定排序）。"""

    def test_sources_follow_iteration_order_and_stable_sort(self) -> None:
        """hit_count 降序；sources 保留来源集合的遍历序（原版展示顺序）。"""
        source_sets = {
            "whitelist": {"000858", "600519"},
            "resonance": {"600519"},
            "multi_head": {"600519", "000858"},
        }
        cands = build_intersection(source_sets, min_sources=2)
        assert [c.code for c in cands] == ["600519", "000858"]
        assert cands[0].sources == ["whitelist", "resonance", "multi_head"]
        assert cands[1].sources == ["whitelist", "multi_head"]

    def test_min_sources_filters_candidates(self) -> None:
        """未达 min_sources 条数的股票不进入候选池。"""
        source_sets = {"whitelist": {"600519"}, "resonance": {"600519", "000858"}}
        cands = build_intersection(source_sets, min_sources=2)
        assert [(c.code, c.hit_count) for c in cands] == [("600519", 2)]


class TestIntradayCodeSpace:
    """盘中共振代码空间回归（曾返回 sh.600519，与其他线的 600519 重复计数）。"""

    def test_intraday_codes_normalized_to_bare_six_digits(self, tmp_path: Path) -> None:
        """intraday 结果应统一为 6 位裸代码，并剔除北交所。"""
        payload = {
            "results": [
                {"ticker": "sh.600519"},
                {"ticker": "sz.000858"},
                {"ticker": "bj.920001"},
            ]
        }
        (tmp_path / "intraday_scan_20261111_1000.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        source_sets = collect_source_sets("2026-11-11", results_dir=tmp_path, czsc_dir=tmp_path)
        assert source_sets["intraday"] == {"600519", "000858"}


class TestCzscCodeParsing:
    """缠论 CSV 代码解析回归（曾误取 sh/sz 前缀，导致命中数只剩 3）。"""

    def test_extracts_six_digit_code_from_prefixed_symbol(self, tmp_path: Path) -> None:
        """sh.600519 / sz.000858 应解析为 6 位代码，bj.920002 应被排除。"""
        csv_path = tmp_path / "matched.csv"
        csv_path.write_text(
            "symbol,dt,close,signals,matched_signals\n"
            'sh.600519,2026-11-02,10.0,{},"[]"\n'
            'sz.000858,2026-11-02,20.0,{},"[]"\n'
            'bj.920002,2026-11-02,30.0,{},"[]"\n',
            encoding="utf-8",
        )
        assert _codes_from_czsc_csv(csv_path) == {"600519", "000858"}

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件缺失时返回空集，不抛异常。"""
        assert _codes_from_czsc_csv(tmp_path / "none.csv") == set()


class TestCombinedCardHeaderAndPushLimit:
    """汇总卡片标题统计（交集总数 / 展示数）与逐只推送上限。"""

    def test_card_header_shows_intersection_total_and_shown_count(self) -> None:
        """标题应写交集池总数，并注明前 10 名详细 / 其余仅名称。"""
        cand = StockCandidate(
            code="600519",
            hit_count=4,
            sources=["whitelist", "macd_ma", "double_ma", "czsc_bi_ma"],
        )
        cand.analysis = {"name": "贵州茅台"}
        card = _build_combined_card(
            [cand], "2026-11-10", {"whitelist": {"600519"}}, total=101, min_sources=3
        )
        assert "命中 ≥3 条线共 101 只" in card
        assert "前 10 名详细分析" in card
        assert "其余仅列出名称与交集次数" in card
        assert "1. 贵州茅台（600519）-4次交集" in card

    def test_per_stock_push_defaults_to_first_50(self) -> None:
        """默认只推送交集排序前 50 只，标题分母同步为 50。"""
        from scripts import combined_stock_cards as cards_mod

        cands = [StockCandidate(code=f"{600000 + i}", hit_count=3) for i in range(60)]
        with (
            patch.object(cards_mod, "push_feishu", return_value=True) as mock_push,
            patch.object(cards_mod, "time"),
        ):
            cards_mod._push_per_stock(cands, "2026-11-10")
        assert mock_push.call_count == 50
        assert "1/50" in mock_push.call_args_list[0].kwargs["title"]
        assert "50/50" in mock_push.call_args_list[-1].kwargs["title"]

    def test_per_stock_push_limit_zero_means_unlimited(self) -> None:
        """limit<=0 时不截断，全部推送。"""
        from scripts import combined_stock_cards as cards_mod

        cands = [StockCandidate(code=f"{600000 + i}", hit_count=3) for i in range(7)]
        with (
            patch.object(cards_mod, "push_feishu", return_value=True) as mock_push,
            patch.object(cards_mod, "time"),
        ):
            cards_mod._push_per_stock(cands, "2026-11-10", limit=0)
        assert mock_push.call_count == 7


def _ta_row(**overrides: object) -> dict:
    """构造 ta_analysis 表行样本（get_ta_by_job 返回结构）。"""
    row: dict = {
        "ticker": "sz.002027",
        "signal": None,
        "confidence": None,
        "thesis": None,
        "risks": None,
        "error": None,
        "elapsed": 0.1,
    }
    row.update(overrides)
    return row


def _signals_row(**overrides: object) -> dict:
    """构造 signals 表行样本（get_signals_by_job 返回结构）。"""
    row: dict = {
        "ticker": "sz.002027",
        "rank": 1,
        "composite_score": None,
        "ranking_score": None,
        "ta_signal": None,
        "ta_confidence": None,
        "kronos_direction": None,
        "kronos_change": None,
        "ta_error": None,
        "kronos_error": None,
        "expected_value": None,
        "conflict": None,
    }
    row.update(overrides)
    return row


class TestLookupSignal:
    """_lookup_signal 合并 ta_analysis / signals 两表：任一源可用即产出记录。"""

    TICKER = "sz.002027"

    def _research(self, ta_rows: list[dict], sig_rows: list[dict]) -> MagicMock:
        research = MagicMock()
        research.get_ta_by_job.return_value = ta_rows
        research.get_signals_by_job.return_value = sig_rows
        return research

    def test_no_rows_returns_none(self) -> None:
        """两表均无该 ticker 记录时返回 None。"""
        assert _lookup_signal(self._research([], []), "job-1", self.TICKER) is None

    def test_ignores_rows_of_other_tickers(self) -> None:
        """同 job 内其他 ticker 的记录不影响匹配，仍返回 None。"""
        research = self._research(
            [_ta_row(ticker="sh.600519", signal="多头排列✅")],
            [_signals_row(ticker="sh.600519", ta_signal="偏多")],
        )
        assert _lookup_signal(research, "job-1", self.TICKER) is None

    def test_ta_only_record_shape(self) -> None:
        """仅 ta_analysis 可用时：TA 信号透传，Kronos/composite 为空。"""
        research = self._research([_ta_row(signal="多头排列✅")], [])
        result = _lookup_signal(research, "job-1", self.TICKER)
        assert result == {
            "ta_signal": "多头排列✅",
            "kronos_direction": None,
            "kronos_change": None,
            "composite_score": None,
            "has_error": False,
        }

    @pytest.mark.parametrize(
        ("ta_row", "sig_row", "exp_has_error", "exp_ta_signal"),
        [
            pytest.param(
                _ta_row(signal="多头排列✅"),
                None,
                False,
                "多头排列✅",
                id="ta_ok_signals_missing",
            ),
            pytest.param(_ta_row(error="boom"), None, True, None, id="ta_error_signals_missing"),
            pytest.param(
                None,
                _signals_row(ta_signal="偏多", kronos_direction="UP", kronos_change=2.5),
                False,
                "偏多",
                id="signals_ok",
            ),
            pytest.param(
                None,
                _signals_row(kronos_error="kronos fail"),
                False,
                None,
                id="signals_only_kronos_error",
            ),
            pytest.param(
                None,
                _signals_row(ta_error="ta fail", kronos_error="kronos fail"),
                True,
                None,
                id="signals_both_errors",
            ),
            pytest.param(
                _ta_row(error="boom"),
                _signals_row(ta_signal="偏多", kronos_direction="UP", kronos_change=2.5),
                False,
                "偏多",
                id="ta_error_signals_ok_fallback",
            ),
            pytest.param(
                _ta_row(signal="多头排列✅"),
                _signals_row(ta_error="ta fail", kronos_error="kronos fail"),
                False,
                "多头排列✅",
                id="ta_ok_signals_both_errors",
            ),
            pytest.param(
                _ta_row(error="boom"),
                _signals_row(ta_error="ta fail", kronos_error="kronos fail"),
                True,
                None,
                id="both_sources_errored",
            ),
        ],
    )
    def test_has_error_truth_table(
        self,
        ta_row: dict | None,
        sig_row: dict | None,
        exp_has_error: bool,
        exp_ta_signal: str | None,
    ) -> None:
        """has_error 仅在两源均不可用（缺失或报错）时为 True；ta_signal 优先 ta_analysis。"""
        research = self._research([ta_row] if ta_row else [], [sig_row] if sig_row else [])
        result = _lookup_signal(research, "job-1", self.TICKER)
        assert result is not None
        assert result["has_error"] == exp_has_error
        assert result["ta_signal"] == exp_ta_signal

    def test_kronos_fields_taken_from_signals(self) -> None:
        """Kronos 字段与 composite_score 仅来自 signals 表。"""
        research = self._research(
            [],
            [_signals_row(kronos_direction="UP", kronos_change=2.5, composite_score=80.0)],
        )
        result = _lookup_signal(research, "job-1", self.TICKER)
        assert result is not None
        assert result["kronos_direction"] == "UP"
        assert result["kronos_change"] == 2.5
        assert result["composite_score"] == 80.0


class TestBuildEvalRecords:
    """_build_eval_records：同一天多 job 仅保留首个产出记录的 job。"""

    DATE_STR = "2026-11-17"

    def _run(
        self,
        jobs: list[dict],
        ta_by_job: dict[str, list[dict]],
        prices: dict[str, float],
    ) -> list[EvalRecord]:
        """以 mock 的 research DB 与价格表执行 _build_eval_records。"""
        research = MagicMock()
        research.list_jobs.return_value = jobs
        research.get_ta_by_job.side_effect = lambda job_id: ta_by_job.get(job_id, [])
        research.get_signals_by_job.side_effect = lambda job_id: []
        with (
            patch("trade_krono_cli.research_db.get_research", return_value=research),
            patch(
                "trade_krono_cli.eval_data.get_close_price",
                side_effect=lambda _ticker, date: prices.get(date),
            ),
        ):
            return _build_eval_records("002027", self.DATE_STR, 90, 5)

    def test_same_day_jobs_yield_single_record(self) -> None:
        """同一天多个 job 均有效时仅产出 1 条记录，取最新 run 的信号。"""
        jobs = [
            {"job_id": "job-latest", "date": "2026-11-10"},
            {"job_id": "job-earlier", "date": "2026-11-10"},
        ]
        ta_by_job = {
            "job-latest": [_ta_row(signal="多头排列✅")],
            "job-earlier": [_ta_row(signal="偏多")],
        }
        prices = {"2026-11-10": 10.0, "2026-11-15": 11.0}
        records = self._run(jobs, ta_by_job, prices)
        assert len(records) == 1
        assert records[0].eval_date == "2026-11-10"
        assert records[0].ta_signal == "多头排列✅"

    def test_signal_less_job_does_not_block_same_day_jobs(self) -> None:
        """同天首个 job 无信号时不应消耗该日期，后续 job 仍产出记录。"""
        jobs = [
            {"job_id": "job-latest", "date": "2026-11-10"},
            {"job_id": "job-earlier", "date": "2026-11-10"},
        ]
        ta_by_job = {"job-earlier": [_ta_row(signal="偏多")]}
        prices = {"2026-11-10": 10.0, "2026-11-15": 11.0}
        records = self._run(jobs, ta_by_job, prices)
        assert len(records) == 1
        assert records[0].ta_signal == "偏多"

    def test_errored_job_does_not_block_same_day_jobs(self) -> None:
        """同天首个 job 信号报错时不应消耗该日期，后续 job 仍产出记录。"""
        jobs = [
            {"job_id": "job-latest", "date": "2026-11-10"},
            {"job_id": "job-earlier", "date": "2026-11-10"},
        ]
        ta_by_job = {
            "job-latest": [_ta_row(error="boom")],
            "job-earlier": [_ta_row(signal="偏多")],
        }
        prices = {"2026-11-10": 10.0, "2026-11-15": 11.0}
        records = self._run(jobs, ta_by_job, prices)
        assert len(records) == 1
        assert records[0].ta_signal == "偏多"

    def test_multi_day_window_one_record_per_date(self) -> None:
        """多天窗口每天至多 1 条记录，窗口外（过旧/未来）job 被排除。"""
        jobs = [
            {"job_id": "job-future", "date": "2026-11-18"},
            {"job_id": "job-d2", "date": "2026-11-12"},
            {"job_id": "job-d1", "date": "2026-11-10"},
            {"job_id": "job-stale", "date": "2026-08-01"},
        ]
        ta_by_job = {
            "job-d1": [_ta_row(signal="多头排列✅")],
            "job-d2": [_ta_row(signal="偏多")],
            "job-future": [_ta_row(signal="多头排列✅")],
            "job-stale": [_ta_row(signal="偏多")],
        }
        prices = {
            "2026-11-10": 10.0,
            "2026-11-15": 11.0,
            "2026-11-12": 20.0,
            "2026-11-17": 22.0,
            "2026-08-01": 5.0,
            "2026-08-06": 5.5,
        }
        records = self._run(jobs, ta_by_job, prices)
        assert [r.eval_date for r in records] == ["2026-11-12", "2026-11-10"]
        assert [r.ta_signal for r in records] == ["偏多", "多头排列✅"]

    def test_missing_exit_price_skips_record(self) -> None:
        """出场价缺失时跳过该 job，不产出记录。"""
        jobs = [{"job_id": "job-1", "date": "2026-11-10"}]
        ta_by_job = {"job-1": [_ta_row(signal="多头排列✅")]}
        prices = {"2026-11-10": 10.0}  # 缺 2026-11-15 出场价
        records = self._run(jobs, ta_by_job, prices)
        assert records == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
