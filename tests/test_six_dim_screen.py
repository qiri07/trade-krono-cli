"""tests for scripts.six_dim_screen（六维打分与门槛逻辑）。"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts.six_dim_screen import (
    DIM_WEIGHTS,
    TIER_WIDTH,
    LogicMetrics,
    StockScore,
    _clamp,
    _compute_technicals,
    _parse_thscode,
    _pct_rank,
    _recent_quarters,
    build_feishu_card,
    score_holder,
    score_logic,
    score_position,
    score_valuation,
    score_vol_price,
    sort_scores,
    trend_gate_and_score,
    write_report,
)


def _mk_logic(**overrides: float | None) -> LogicMetrics:
    """构造一个默认通过全部逻辑门槛的指标对象。"""
    base: dict[str, float | None] = {
        "code": "600519",
        "name": "贵州茅台",
        "roe_avg": 30.0,
        "roe_min": 28.0,
        "roe_excl_avg": 29.5,
        "debt_ratio": 20.0,
        "gross_margin": 90.0,
        "profit_cagr": 18.0,
        "revenue_cagr": 15.0,
        "cfo_ratio": 1.2,
        "latest_profit_yi": 800.0,
        "latest_profit_yoy": 10.0,
        "positive_years": 4,
        "total_years": 4,
        "is_financial": False,
    }
    base.update(overrides)
    return LogicMetrics(**base)  # type: ignore[arg-type]


def _mk_tech(**overrides: float) -> dict[str, float]:
    """构造一组默认多头排列、量价健康的技术指标。"""
    base: dict[str, float] = {
        "close": 20.0,
        "ma5": 19.8,
        "ma10": 19.5,
        "ma20": 19.0,
        "ma60": 18.0,
        "ma120": 17.0,
        "ma20_slope": 1.5,
        "ma60_slope": 2.0,
        "dif": 0.3,
        "dea": 0.2,
        "pct_pos_3y": 35.0,
        "pct_pos_1y": 40.0,
        "high_3y": 30.0,
        "low_3y": 10.0,
        "amt_ratio": 1.3,
        "updown_amt_ratio": 1.5,
        "ret5": 2.0,
        "ret20": 8.0,
        "bars": 500.0,
        "last_bar_ts": 1762704000.0,
    }
    base.update(overrides)
    return base


class TestHelpers:
    """基础工具函数。"""

    def test_clamp(self) -> None:
        assert _clamp(-5) == 0.0
        assert _clamp(105) == 100.0
        assert _clamp(42.5) == 42.5

    def test_pct_rank(self) -> None:
        series = [1.0, 2.0, 3.0, 4.0]
        assert _pct_rank(series, 2.0) == pytest.approx(50.0)
        assert _pct_rank(series, 4.0) == pytest.approx(100.0)
        assert _pct_rank([], 1.0) == 100.0

    def test_recent_quarters_starts_with_latest_and_descends(self) -> None:
        quarters = _recent_quarters(date(2026, 11, 10), n=4)
        assert quarters[0] == "20260930"
        assert quarters[1] == "20260630"
        assert quarters[2] == "20260331"
        assert quarters[3] == "20251231"

    def test_parse_thscode(self) -> None:
        assert _parse_thscode("fin_600004.SH_2025-4", "fin_") == "600004.SH"
        assert _parse_thscode("fin_broken", "fin_") is None
        assert _parse_thscode("income_600004.SH", "fin_") is None


class TestLogicDimension:
    """逻辑维度：硬门槛 + 打分。"""

    def test_pass_returns_positive_score(self) -> None:
        ok, score, reason = score_logic(_mk_logic())
        assert ok is True
        assert 0 < score <= 100
        assert reason == "逻辑成立"

    @pytest.mark.parametrize(
        ("overrides", "keyword"),
        [
            ({"roe_avg": 11.0}, "ROE均值"),
            ({"roe_excl_avg": 9.0}, "扣非ROE均值"),
            ({"profit_cagr": -3.0}, "3年净利CAGR"),
            ({"cfo_ratio": 0.2}, "现金流/净利"),
            ({"latest_profit_yi": 0.5}, "净利润"),
            ({"positive_years": 3}, "年亏损"),
            ({"debt_ratio": 70.0}, "资产负债率"),
        ],
    )
    def test_each_gate_rejects(self, overrides: dict[str, float], keyword: str) -> None:
        ok, score, reason = score_logic(_mk_logic(**overrides))
        assert ok is False
        assert score == 0.0
        assert keyword in reason

    def test_financial_exempt_from_debt_gate(self) -> None:
        ok, _, _ = score_logic(_mk_logic(debt_ratio=92.0, gross_margin=None, is_financial=True))
        assert ok is True

    def test_higher_roe_scores_higher(self) -> None:
        _, low, _ = score_logic(_mk_logic(roe_avg=15.0, roe_excl_avg=14.0, roe_min=14.0))
        _, high, _ = score_logic(_mk_logic())
        assert high > low


class TestValuationDimension:
    """估值维度：绝对估值 + 五年分位 + PEG。"""

    def test_pass(self) -> None:
        ok, score, detail = score_valuation(15.0, 2.0, {"pe_pct": 20.0, "pb_pct": 30.0}, 20.0)
        assert ok is True
        assert 0 < score <= 100
        assert "PE=15.0" in detail

    def test_pe_out_of_range(self) -> None:
        ok, _, reason = score_valuation(35.0, 2.0, None, 20.0)
        assert ok is False
        assert "PE" in reason

    def test_pb_out_of_range(self) -> None:
        ok, _, reason = score_valuation(10.0, 9.0, None, 20.0)
        assert ok is False
        assert "PB" in reason

    def test_high_pe_percentile_rejected(self) -> None:
        ok, _, reason = score_valuation(10.0, 1.0, {"pe_pct": 80.0, "pb_pct": 20.0}, 20.0)
        assert ok is False
        assert "五年分位" in reason

    def test_high_peg_rejected(self) -> None:
        ok, _, reason = score_valuation(28.0, 1.0, {"pe_pct": 10.0, "pb_pct": 10.0}, 5.0)
        assert ok is False
        assert "PEG" in reason

    def test_low_percentile_scores_higher(self) -> None:
        _, cheap, _ = score_valuation(12.0, 1.5, {"pe_pct": 5.0, "pb_pct": 5.0}, 20.0)
        _, pricey, _ = score_valuation(12.0, 1.5, {"pe_pct": 55.0, "pb_pct": 55.0}, 20.0)
        assert cheap > pricey


class TestPositionAndTrend:
    """位置与趋势维度。"""

    def test_position_peaks_near_target_band(self) -> None:
        best, _ = score_position(_mk_tech(pct_pos_3y=35.0))
        low, _ = score_position(_mk_tech(pct_pos_3y=5.0))
        high, _ = score_position(_mk_tech(pct_pos_3y=95.0))
        assert best > low > high

    def test_uptrend_passes(self) -> None:
        ok, score, detail = trend_gate_and_score(_mk_tech())
        assert ok is True
        assert score > 50
        assert "多头排列" in detail

    def test_downtrend_rejected(self) -> None:
        tech = _mk_tech(close=10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma60=12.0, ma120=13.0)
        tech["ma60_slope"] = -5.0
        ok, _, reason = trend_gate_and_score(tech)
        assert ok is False
        assert "下跌趋势" in reason


class TestHolderAndVolumePrice:
    """股东人数与量价关系维度。"""

    def test_holder_concentration_scores_above_neutral(self) -> None:
        score, detail = score_holder({"chg_pct": -12.0, "holders": 1e5, "avg_value": 3e5})
        assert score > 50
        assert "明显集中" in detail

    def test_holder_dispersion_scores_below_neutral(self) -> None:
        score, _ = score_holder({"chg_pct": 10.0, "holders": 1e5, "avg_value": 3e5})
        assert score < 50

    def test_holder_missing_is_neutral(self) -> None:
        score, detail = score_holder(None)
        assert score == 50.0
        assert "无股东户数" in detail

    def test_healthy_volume_scores_high(self) -> None:
        score, _ = score_vol_price(_mk_tech())
        assert score > 70

    def test_heavy_volume_price_drop_penalised(self) -> None:
        score, _, = score_vol_price(_mk_tech(amt_ratio=2.5, ret5=-6.0))
        healthy, _ = score_vol_price(_mk_tech())
        assert score < healthy


class TestTechnicals:
    """K 线指标计算。"""

    def _frame(self, n: int = 300, trend: float = 0.1) -> pd.DataFrame:
        closes = [10.0 + i * trend for i in range(n)]
        return pd.DataFrame(
            {
                "timestamps": pd.date_range("2025-01-01", periods=n, freq="D"),
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1e5] * n,
                "amount": [1e8] * n,
            }
        )

    def test_insufficient_bars_returns_none(self) -> None:
        assert _compute_technicals(self._frame(n=50)) is None

    def test_uptrend_metrics(self) -> None:
        metrics = _compute_technicals(self._frame())
        assert metrics is not None
        assert metrics["close"] > metrics["ma20"] > metrics["ma60"]
        assert metrics["pct_pos_3y"] > 90  # 单调上涨，收在区间高点
        assert metrics["last_bar_ts"] > 0


class TestFeishuCard:
    """飞书汇总卡片。"""

    def test_card_contains_funnel_and_ranking(self) -> None:
        item = StockScore(code="600519", name="贵州茅台", ticker="sh.600519", logic=80.0)
        item.composite = 78.5
        item.metrics = {
            "pe": 20.0,
            "pb": 6.6,
            "pe_pct": 15.0,
            "roe_avg": 30.0,
            "profit_cagr": 18.0,
            "market_cap_yi": 18000.0,
            "pct_pos_3y": 42.0,
            "ma60_slope": 1.2,
            "amount_ratio": 1.1,
            "holder_chg_pct": -5.0,
        }
        funnel = {"universe": 5038, "logic_pass": 345, "abs_val_pass": 240, "val_pass": 142,
                  "trend_pass": 64}
        card = build_feishu_card([item], "2026-11-10", funnel)
        assert "2026-11-10 六维选股" in card
        assert "A股 5038 → 逻辑 345" in card
        assert "1. 贵州茅台（600519）-78.5分" in card
        assert "PE20.0(15%分位)" in card
        assert "不构成投资建议" in card

    def test_card_without_funnel(self) -> None:
        item = StockScore(code="000001", name="平安银行", ticker="sz.000001")
        item.metrics = {"pe": 5.0, "pb": 0.5}
        card = build_feishu_card([item], "2026-11-10", {})
        assert "1. 平安银行（000001）" in card


class TestReport:
    """报告写出。"""

    def test_write_report_outputs_json_and_markdown(self, tmp_path: Path) -> None:
        item = StockScore(code="600519", name="贵州茅台", ticker="sh.600519", logic=80.0)
        item.composite = 70.0
        item.metrics = {"pe": 20.0, "pe_pct": 15.0, "roe_avg": 30.0, "profit_cagr": 18.0}
        item.notes = {"logic": "ROE 优秀"}
        json_path, md_path = write_report([item], "2026-11-10", tmp_path)
        assert json_path.exists() and md_path.exists()
        assert "贵州茅台" in md_path.read_text(encoding="utf-8")
        assert "2026-11-10" in json_path.read_text(encoding="utf-8")


class TestTieredSort:
    """排序口径：分档字典序（逻辑 > 估值 > 位置 > 趋势 > 股东 > 量价）。"""

    @staticmethod
    def _mk(
        code: str,
        *,
        logic: float,
        valuation: float = 0.0,
        position: float = 0.0,
        trend: float = 0.0,
        holder: float = 0.0,
        vol_price: float = 0.0,
    ) -> StockScore:
        """构造带显式六维分与综合分的候选。"""
        item = StockScore(
            code=code,
            name=code,
            ticker=f"sh.{code}",
            logic=logic,
            valuation=valuation,
            position=position,
            trend=trend,
            holder=holder,
            vol_price=vol_price,
        )
        item.composite = sum(getattr(item, k) * w for k, w in DIM_WEIGHTS)
        return item

    def test_tier_width_is_five_points(self) -> None:
        """档位宽度 5 分：80→16 档、85→17 档。"""
        assert pytest.approx(5.0) == TIER_WIDTH
        assert self._mk("600000", logic=80.0).tiered_key[0] == pytest.approx(16.0)
        assert self._mk("600001", logic=85.0).tiered_key[0] == pytest.approx(17.0)

    def test_key_follows_dimension_priority_order(self) -> None:
        """键的前六位顺序与 DIM_WEIGHTS 一致，最后一位是综合分。"""
        item = self._mk(
            "600000",
            logic=90.0,
            valuation=80.0,
            position=70.0,
            trend=60.0,
            holder=50.0,
            vol_price=40.0,
        )
        assert item.tiered_key[:6] == (18.0, 16.0, 14.0, 12.0, 10.0, 8.0)
        assert item.tiered_key[-1] == pytest.approx(item.composite)

    def test_logic_tier_beats_all_other_dims(self) -> None:
        """逻辑高一个档即排前，即使其余五维全面落后（旧加权口径会反转）。"""
        weak_logic = self._mk("600000", logic=85.0)
        strong_others = self._mk(
            "600001",
            logic=84.9,
            valuation=100.0,
            position=100.0,
            trend=100.0,
            holder=100.0,
            vol_price=100.0,
        )
        assert strong_others.composite > weak_logic.composite
        assert [s.code for s in sort_scores([strong_others, weak_logic])] == [
            "600000",
            "600001",
        ]

    def test_same_logic_tier_falls_back_to_valuation(self) -> None:
        """逻辑同档时由估值档决定，位置/趋势/量价再高也不越级。"""
        better_val = self._mk("600000", logic=81.0, valuation=90.0)
        better_rest = self._mk(
            "600001",
            logic=81.0,
            valuation=86.0,
            position=100.0,
            trend=100.0,
            holder=100.0,
            vol_price=100.0,
        )
        assert [s.code for s in sort_scores([better_rest, better_val])] == [
            "600000",
            "600001",
        ]

    def test_all_tiers_equal_uses_composite(self) -> None:
        """六维全部同档时才用综合分兜底。"""
        higher = self._mk(
            "600000",
            logic=82.0,
            valuation=72.0,
            position=62.0,
            trend=52.0,
            holder=42.0,
            vol_price=32.0,
        )
        lower = self._mk(
            "600001",
            logic=81.0,
            valuation=71.0,
            position=61.0,
            trend=51.0,
            holder=41.0,
            vol_price=31.0,
        )
        assert higher.tiered_key[:6] == lower.tiered_key[:6]
        assert [s.code for s in sort_scores([lower, higher])] == ["600000", "600001"]

    def test_returns_same_list_object(self) -> None:
        """就地排序并返回同一列表对象。"""
        items = [self._mk("600000", logic=70.0), self._mk("600001", logic=90.0)]
        result = sort_scores(items)
        assert result is items
        assert items[0].code == "600001"

    def test_full_order_matches_priority(self) -> None:
        """名次完全由优先级决定，与综合分高低无关。"""
        items = [
            self._mk(
                "600000",
                logic=70.0,
                valuation=100.0,
                position=100.0,
                vol_price=100.0,
            ),
            self._mk(
                "600001",
                logic=95.0,
                valuation=10.0,
                position=10.0,
                vol_price=10.0,
            ),
            self._mk("600002", logic=85.0, valuation=10.0),
            self._mk("600003", logic=85.0, valuation=50.0),
        ]
        assert [s.code for s in sort_scores(items)] == [
            "600001",
            "600003",
            "600002",
            "600000",
        ]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
