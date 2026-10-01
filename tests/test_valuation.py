"""tests/test_valuation.py — 估值评估模块单元测试。"""

from __future__ import annotations

from scripts.valuation import (
    calc_ddm_price,
    calc_graham_price,
    calc_pe_median_price,
    calc_valuation_score,
    evaluate_valuation,
)


class TestCalcGrahamPrice:
    """格雷厄姆公式估值测试。"""

    def test_basic_calculation(self) -> None:
        """基本计算：EPS=5, g=5% → V = 5 × 18.5 = 92.5，但 BV=20 上限 30，结果取 min。"""
        result = calc_graham_price(eps=5.0, book_value=20.0, growth_rate=5.0)
        assert result is not None
        assert result == 30.0  # 被净资产上限约束（20 × 1.5 = 30）

    def test_cap_by_book_value(self) -> None:
        """内在价值被净资产上限约束。"""
        # EPS=100, g=10% → V = 100 × 28.5 = 2850，但 BV=50 → 上限 50×1.5=75
        result = calc_graham_price(eps=100.0, book_value=50.0, growth_rate=10.0)
        assert result is not None
        assert result == 75.0

    def test_no_eps_returns_none(self) -> None:
        """EPS 为 None 时返回 None。"""
        assert calc_graham_price(eps=None, book_value=10.0) is None

    def test_negative_eps_returns_none(self) -> None:
        """亏损股 EPS 为负时返回 None。"""
        assert calc_graham_price(eps=-5.0, book_value=10.0) is None

    def test_default_growth_rate(self) -> None:
        """未指定增长率时使用默认 5%；无 BV 上限时按公式计算。"""
        result = calc_graham_price(eps=10.0, book_value=None, growth_rate=None)
        assert result is not None
        assert abs(result - 185.0) < 0.01  # 10 × (8.5 + 2×5) = 185


class TestCalcPeMedianPrice:
    """PE 历史分位估值测试。"""

    def test_low_percentile_price_above_current(self) -> None:
        """PE 分位低（低估）→ 建议价高于当前价。"""
        result = calc_pe_median_price(current_price=100.0, pe_ttm=10.0, pe_percentile=20.0)
        assert result is not None
        assert result > 100.0  # 低估时建议买入价高于当前价

    def test_high_percentile_price_below_current(self) -> None:
        """PE 分位高（高估）→ 建议价低于当前价。"""
        result = calc_pe_median_price(current_price=100.0, pe_ttm=30.0, pe_percentile=80.0)
        assert result is not None
        assert result < 100.0

    def test_no_percentile_returns_current(self) -> None:
        """无分位数据时建议价等于当前价。"""
        result = calc_pe_median_price(current_price=50.0, pe_ttm=15.0, pe_percentile=None)
        assert result is not None
        assert abs(result - 50.0) < 0.01

    def test_null_pe_returns_none(self) -> None:
        """PE 为 None 时返回 None。"""
        assert calc_pe_median_price(current_price=50.0, pe_ttm=None, pe_percentile=None) is None

    def test_ceiling_20_percent(self) -> None:
        """建议价涨幅不超过 20%。"""
        result = calc_pe_median_price(current_price=100.0, pe_ttm=5.0, pe_percentile=5.0)
        assert result is not None
        assert result <= 120.0


class TestCalcDdmPrice:
    """股息折现模型测试。"""

    def test_basic_ddm(self) -> None:
        """基本 DDM 计算：D0=2, g=3%, r=10% → D1=2.06, V=2.06/0.07≈29.43"""
        result = calc_ddm_price(dividend_per_share=2.0, required_return=0.10, growth_rate=0.03)
        assert result is not None
        assert abs(result - 29.43) < 0.01

    def test_null_dividend_returns_none(self) -> None:
        """股息为 None 时返回 None。"""
        assert calc_ddm_price(dividend_per_share=None) is None

    def test_growth_equals_return_returns_none(self) -> None:
        """增长率 ≥ 要求回报时模型不适用。"""
        assert calc_ddm_price(dividend_per_share=1.0, required_return=0.05, growth_rate=0.05) is None


class TestCalcValuationScore:
    """估值综合评分测试。"""

    def test_low_pe_high_score(self) -> None:
        """低 PE 分位 → 高分。"""
        score, _ = calc_valuation_score(pe_ttm=10.0, pb=1.5, pe_percentile=15.0, roe=18.0)
        assert score >= 70

    def test_high_pe_low_score(self) -> None:
        """高 PE 分位 + 高 PB → 得分低于中性。"""
        score, _ = calc_valuation_score(pe_ttm=40.0, pb=5.0, pe_percentile=85.0, roe=8.0)
        # PE分位85% → 15%得分 × 0.4 = 6；PB5.0 → 0分；ROE8% → 40% × 0.2 = 8
        # 总分 = 50 + 6 + 0 + 8 = 64，低于高分股（如低PE低PB高ROE）
        assert score < 70

    def test_all_none_returns_mid_score(self) -> None:
        """全 None 时返回中性分 50。"""
        score, methods = calc_valuation_score(pe_ttm=None, pb=None, pe_percentile=None, roe=None)
        assert score == 50.0
        assert methods == []

    def test_with_dividend_boosts_score(self) -> None:
        """股息率提升评分。"""
        score_no_div, _ = calc_valuation_score(pe_ttm=10.0, pb=1.5, pe_percentile=15.0, roe=18.0)
        score_with_div, _ = calc_valuation_score(
            pe_ttm=10.0, pb=1.5, pe_percentile=15.0, roe=18.0, dividend_yield=3.0
        )
        assert score_with_div > score_no_div

    def test_methods_returned(self) -> None:
        """返回的 methods 列表包含正确维度。"""
        _, methods = calc_valuation_score(
            pe_ttm=10.0, pb=1.5, pe_percentile=20.0, roe=18.0, dividend_yield=2.0
        )
        assert "pe_percentile" in methods
        assert "pb" in methods
        assert "roe" in methods
        assert "dividend" in methods


class TestEvaluateValuation:
    """完整估值评估测试。"""

    def test_full_evaluation(self) -> None:
        """完整评估流程。"""
        result = evaluate_valuation(
            ticker="600519",
            name="贵州茅台",
            current_price=1600.0,
            pe_ttm=30.0,
            pb=9.0,
            roe=30.0,
            pe_percentile=50.0,
            eps_ttm=53.33,
            book_value_per_share=177.78,
        )
        assert result.ticker == "600519"
        assert result.current_price == 1600.0
        assert result.pe_ttm == 30.0
        assert result.valuation_score is not None
        assert result.conclusion != ""
        assert isinstance(result.methods_used, list)

    def test_missing_data_fallback(self) -> None:
        """缺少数据时不崩溃，返回中性结果。"""
        result = evaluate_valuation(
            ticker="000001",
            name="平安银行",
            current_price=12.0,
            pe_ttm=None,
            pb=None,
            roe=None,
            pe_percentile=None,
        )
        assert result.ticker == "000001"
        assert result.valuation_score == 50.0
        assert result.combined_price is None

    def test_conclusion_text(self) -> None:
        """结论文本包含关键信息。"""
        result = evaluate_valuation(
            ticker="600519",
            name="贵州茅台",
            current_price=1600.0,
            pe_ttm=12.0,
            pb=3.0,
            roe=25.0,
            pe_percentile=15.0,
        )
        assert "低估" in result.conclusion or "🟢" in result.conclusion
