"""测试风险引擎各模块。"""
import pytest
import numpy as np
import pandas as pd
from trade_krono_cli.risk.volatility import calc_volatility_risk
from trade_krono_cli.risk.drawdown import calc_drawdown_risk
from trade_krono_cli.risk.liquidity import calc_liquidity_risk
from trade_krono_cli.risk.concentration import calc_concentration_risk
from trade_krono_cli.risk.market_regime import calc_market_regime_risk
from trade_krono_cli.risk.risk_engine import RiskEngine, RiskScore, DEFAULT_WEIGHTS


# ── 辅助函数：构造测试用 K 线数据 ───────────────────────────────────────────────

def _make_close_series(values):
    return pd.Series(values, dtype=float)


def _make_kline_df(close_values, high_values=None, volume_values=None):
    close = _make_close_series(close_values)
    if high_values is None:
        high = close * (1 + np.random.uniform(0, 0.02, len(close)))
    else:
        high = _make_close_series(high_values)
    if volume_values is None:
        volume = pd.Series([1e7] * len(close), dtype=float)
    else:
        volume = pd.Series(volume_values, dtype=float)
    return pd.DataFrame({
        "open":   close * 0.99,
        "high":   high,
        "low":    close * 0.98,
        "close":  close,
        "volume": volume,
    })


# ═══════════════════════════════════════════════════════
# Volatility 风险测试
# ═══════════════════════════════════════════════════════

class TestVolatilityRisk:
    def test_low_volatility_gives_low_score(self):
        """波动率低 → 风险分低。"""
        close = _make_close_series([100 + i * 0.01 for i in range(50)])  # 几乎无波动
        score, ann_vol = calc_volatility_risk(close)
        assert 0 <= score <= 20
        assert ann_vol < 10  # 年化波动率 < 10%

    def test_high_volatility_gives_high_score(self):
        """波动率高 → 风险分高。"""
        np.random.seed(42)
        close = pd.Series(
            100 * (1 + np.random.randn(50) * 0.04), dtype=float
        )
        score, ann_vol = calc_volatility_risk(close)
        assert score > 50  # 高风险
        assert ann_vol > 50

    def test_insufficient_data_returns_default(self):
        """数据不足时返回默认中等风险分。"""
        close = _make_close_series([100, 101, 102])
        score, ann_vol = calc_volatility_risk(close)
        assert score == 25.0
        assert ann_vol == 0.0

    def test_score_clamped_to_0_100(self):
        """极端高波动时分数不超过 100。"""
        np.random.seed(99)
        close = pd.Series(
            100 * (1 + np.random.randn(50) * 0.15), dtype=float  # 极高波动
        )
        score, _ = calc_volatility_risk(close)
        assert 0 <= score <= 100


# ═══════════════════════════════════════════════════════
# Drawdown 风险测试
# ═══════════════════════════════════════════════════════

class TestDrawdownRisk:
    def test_no_drawdown_gives_low_score(self):
        """无回撤 → 低风险分。"""
        close = _make_close_series([100, 101, 102, 103, 104])
        high = close * 1.01
        score, max_dd = calc_drawdown_risk(high, close)
        assert score <= 20  # 无回撤时风险分 ≤ 20
        assert max_dd < 5

    def test_large_drawdown_gives_high_score(self):
        """大回撤 → 高风险分。"""
        close = _make_close_series(
            [100, 100, 90, 80, 75, 80, 85, 90, 95, 100] * 5
        )
        high = close * 1.02
        score, max_dd = calc_drawdown_risk(high, close)
        assert score > 40
        assert max_dd >= 20  # 最大回撤 >= 20%

    def test_insufficient_data_returns_default(self):
        """数据不足时返回默认风险分。"""
        close = _make_close_series([100, 101])
        high = close * 1.01
        score, max_dd = calc_drawdown_risk(high, close)
        assert score == 20.0
        assert max_dd == 0.0

    def test_score_clamped_to_0_100(self):
        """极端回撤时分数不超过 100。"""
        close = _make_close_series([100, 60, 50, 55, 60, 70, 80, 90, 100] * 5)
        high = close * 1.05
        score, _ = calc_drawdown_risk(high, close)
        assert 0 <= score <= 100


# ═══════════════════════════════════════════════════════
# Liquidity 风险测试
# ═══════════════════════════════════════════════════════

class TestLiquidityRisk:
    def test_high_volume_gives_low_score(self):
        """成交量大 → 低风险分。"""
        volume = pd.Series([5e8] * 30, dtype=float)  # 日均 5 亿股
        score, turnover = calc_liquidity_risk(volume)
        assert score < 30
        assert isinstance(turnover, (int, float, type(None)))

    def test_low_volume_gives_high_score(self):
        """成交量极小 → 高风险分。"""
        volume = pd.Series([100.0] * 30, dtype=float)  # 日均仅 100 股，极不流动
        score, turnover = calc_liquidity_risk(volume)
        assert score > 60  # 极低成交量 → 高风险

    def test_insufficient_data_returns_default(self):
        """数据不足时返回默认风险分。"""
        volume = pd.Series([1e7, 2e7], dtype=float)
        score, turnover = calc_liquidity_risk(volume)
        assert score == 30.0
        assert turnover is None

    def test_market_cap_produces_turnover(self):
        """提供市值时应计算换手率。"""
        volume = pd.Series([5e7] * 30, dtype=float)
        score, turnover = calc_liquidity_risk(volume, market_cap=100.0)  # 100 亿元
        assert score >= 0
        assert turnover is not None
        assert isinstance(turnover, float)


# ═══════════════════════════════════════════════════════
# Concentration 风险测试
# ═══════════════════════════════════════════════════════

class TestConcentrationRisk:
    def test_default_score(self):
        """无 TA 结果时返回默认 10 分。"""
        score = calc_concentration_risk()
        assert score == 10.0

    def test_with_ta_result_returns_10(self):
        """有 TA 结果时也返回默认值（占位实现）。"""
        score = calc_concentration_risk(ta_result="dummy")
        assert score == 10.0


# ═══════════════════════════════════════════════════════
# Market Regime 风险测试
# ═══════════════════════════════════════════════════════

class TestMarketRegimeRisk:
    def test_uptrend_gives_low_score(self):
        """上涨趋势 → 低风险分。"""
        close = _make_close_series([100 + i * 0.5 for i in range(60)])
        score = calc_market_regime_risk(close)
        assert score < 40

    def test_downtrend_gives_high_score(self):
        """下跌趋势 → 高风险分。"""
        close = _make_close_series([100 - i * 0.5 for i in range(60)])
        score = calc_market_regime_risk(close)
        assert score > 50

    def test_insufficient_data_returns_default(self):
        """数据不足时返回默认风险分。"""
        close = _make_close_series([100, 101, 102])
        score = calc_market_regime_risk(close)
        assert score == 30.0

    def test_sideways_gives_mid_score(self):
        """横盘趋势 → 中等风险分。"""
        close = _make_close_series([100] * 40)
        score = calc_market_regime_risk(close)
        assert 20 <= score <= 50


# ═══════════════════════════════════════════════════════
# RiskEngine 集成测试
# ═══════════════════════════════════════════════════════

class TestRiskEngine:
    def test_assess_basic(self):
        """基础风险评估流程。"""
        engine = RiskEngine()
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        risk = engine.assess("sh.600519", "2026-08-11", df)

        assert isinstance(risk, RiskScore)
        assert risk.ticker == "sh.600519"
        assert risk.date == "2026-08-11"
        assert 0 <= risk.total_risk <= 100
        assert 0 <= risk.volatility_score <= 100
        assert 0 <= risk.drawdown_score <= 100
        assert 0 <= risk.liquidity_score <= 100
        assert 0 <= risk.concentration_score <= 100
        assert 0 <= risk.market_regime_score <= 100

    def test_assess_with_quote_data(self):
        """提供实时估值数据时应计算换手率。"""
        engine = RiskEngine()
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        quote = {"market_cap": 200.0}  # 200 亿元
        risk = engine.assess("sh.600519", "2026-08-11", df, quote_data=quote)

        assert risk.avg_turnover is not None
        assert isinstance(risk.avg_turnover, float)

    def test_total_risk_is_weighted_sum(self):
        """总分是各维度加权求和。"""
        engine = RiskEngine(weights=DEFAULT_WEIGHTS)
        # 创建高波动数据
        np.random.seed(123)
        close_vals = 100 * (1 + np.random.randn(60) * 0.03)
        df = _make_kline_df(close_vals.tolist())

        risk = engine.assess("sh.600519", "2026-08-11", df)

        expected = (
            risk.volatility_score * 0.30
            + risk.drawdown_score * 0.25
            + risk.liquidity_score * 0.20
            + risk.concentration_score * 0.10
            + risk.market_regime_score * 0.15
        )
        assert risk.total_risk == pytest.approx(expected, abs=0.1)

    def test_custom_weights(self):
        """自定义权重应生效。"""
        custom_weights = {
            "volatility": 0.50,
            "drawdown":   0.20,
            "liquidity":  0.15,
            "concentration": 0.10,
            "market_regime": 0.05,
        }
        engine = RiskEngine(weights=custom_weights)
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        risk = engine.assess("sh.600519", "2026-08-11", df)

        expected = (
            risk.volatility_score * 0.50
            + risk.drawdown_score * 0.20
            + risk.liquidity_score * 0.15
            + risk.concentration_score * 0.10
            + risk.market_regime_score * 0.05
        )
        assert risk.total_risk == pytest.approx(expected, abs=0.1)

    def test_to_dict(self):
        """RiskScore 序列化应包含所有字段。"""
        engine = RiskEngine()
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        risk = engine.assess("sh.600519", "2026-08-11", df)
        d = risk.to_dict()

        assert d["ticker"] == "sh.600519"
        assert d["date"] == "2026-08-11"
        assert "total_risk" in d
        assert "volatility_score" in d
        assert "drawdown_score" in d
        assert "liquidity_score" in d
        assert "concentration_score" in d
        assert "market_regime_score" in d

    def test_print_report(self):
        """print_report 输出格式正确。"""
        engine = RiskEngine()
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        risk = engine.assess("sh.600519", "2026-08-11", df)
        report = risk.print_report()

        assert "sh.600519" in report
        assert "2026-08-11" in report
        assert "Total Risk" in report
        assert "流动性风险" in report
        assert "波动率风险" in report
        assert "市场环境风险" in report


# ── assess_risk 便捷函数测试 ─────────────────────────────────────────────────

class TestAssessRisk:
    def test_convenience_function(self):
        """便捷函数 assess_risk 应返回 RiskScore。"""
        from trade_krono_cli.risk.risk_engine import assess_risk
        df = _make_kline_df([100 + i * 0.1 for i in range(60)])
        risk = assess_risk("sh.600519", "2026-08-11", df)
        assert isinstance(risk, RiskScore)
        assert 0 <= risk.total_risk <= 100
