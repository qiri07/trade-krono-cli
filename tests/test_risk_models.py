"""Risk Models 单元测试。

覆盖：
  - historical_var / conditional_var
  - var_annualized / cvar_annualized
  - beta / correlation
  - sharpe_ratio
  - expected_return_adjustment
"""

from __future__ import annotations

import numpy as np

from trade_krono_cli.risk.models import (
    beta,
    conditional_var,
    correlation,
    cvar_annualized,
    expected_return_adjustment,
    historical_var,
    sharpe_ratio,
    var_annualized,
)

# ── historical_var ────────────────────────────────────────────────────────────


class TestHistoricalVar:
    def test_normal_distribution_returns_negative(self) -> None:
        """正态分布收益率 → VaR 为负值（表示损失）。"""
        np.random.seed(42)
        returns = np.random.normal(0.05, 1.5, 500)  # 日均 0.05%, 波动 1.5%
        var = historical_var(returns, confidence=0.95)
        assert var < 0

    def test_small_sample_returns_zero(self) -> None:
        """样本 < 20 时返回 0。"""
        result = historical_var([0.1, -0.1, 0.2], confidence=0.95)
        assert result == 0.0

    def test_all_positive_returns_small_var(self) -> None:
        """全正收益 → VaR 接近 0。"""
        returns = np.ones(100) * 0.1
        var = historical_var(returns, confidence=0.95)
        assert var >= -0.1  # 最多等于最小值

    def test_confidence_levels(self) -> None:
        """99% 置信度 VaR 应大于 95%（绝对值更大）。"""
        np.random.seed(42)
        returns = np.random.normal(0, 1.5, 500)
        var_95 = abs(historical_var(returns, confidence=0.95))
        var_99 = abs(historical_var(returns, confidence=0.99))
        assert var_99 >= var_95


# ── conditional_var ───────────────────────────────────────────────────────────


class TestConditionalVar:
    def test_cvar_is_worse_than_var(self) -> None:
        """CVaR 应 <= VaR（尾部平均损失 >= 分位损失）。"""
        np.random.seed(42)
        returns = np.random.normal(0, 2.0, 500)
        var = historical_var(returns, confidence=0.95)
        cvar = conditional_var(returns, confidence=0.95)
        assert cvar <= var  # 更负

    def test_small_sample_returns_zero(self) -> None:
        result = conditional_var([0.1, -0.1], confidence=0.95)
        assert result == 0.0

    def test_extreme_tail_events(self) -> None:
        """含极端损失的序列 → CVaR 显著更低。"""
        returns = list(np.random.normal(0, 1.0, 200)) + [-15.0, -20.0, -25.0]
        var = historical_var(returns, confidence=0.95)
        cvar = conditional_var(returns, confidence=0.95)
        assert cvar <= var
        assert cvar < -5.0  # 尾部极端损失


# ── annualized variants ───────────────────────────────────────────────────────


class TestAnnualized:
    def test_var_annualized_scaling(self) -> None:
        """年化 VaR = 日 VaR × √252。"""
        returns = [-2.0] * 100 + [0.1] * 400
        daily = historical_var(returns, confidence=0.95)
        annual = var_annualized(returns, confidence=0.95)
        expected = daily * (252**0.5)
        assert abs(annual - expected) < 0.01

    def test_cvar_annualized_scaling(self) -> None:
        returns = [-3.0] * 100 + [0.1] * 400
        daily = conditional_var(returns, confidence=0.95)
        annual = cvar_annualized(returns, confidence=0.95)
        expected = daily * (252**0.5)
        assert abs(annual - expected) < 0.01


# ── beta ──────────────────────────────────────────────────────────────────────


class TestBeta:
    def test_perfect_correlation_beta_1(self) -> None:
        """与市场完全同步 → Beta = 1。"""
        market = np.random.normal(0, 1.0, 200)
        stock = market + np.random.normal(0, 0.01, 200)  # 几乎相同
        b = beta(stock, market)  # noqa: E741
        assert 0.95 <= b <= 1.05

    def test_no_market_returns_default_1(self) -> None:
        """无市场数据 → Beta = 1.0。"""
        stock = np.random.normal(0, 1.0, 100)
        assert beta(stock) == 1.0

    def test_small_sample_returns_1(self) -> None:
        """样本 < 30 → Beta = 1.0。"""
        assert beta([0.1, -0.1], [0.2, -0.2]) == 1.0

    def test_higher_volatility_than_market(self) -> None:
        """高波动股票 → Beta > 1。"""
        market = np.random.normal(0, 1.0, 200)
        stock = market * 1.5 + np.random.normal(0, 0.5, 200)
        b = beta(stock, market)  # noqa: E741
        assert b > 1.0


# ── correlation ───────────────────────────────────────────────────────────────


class TestCorrelation:
    def test_perfect_positive_correlation(self) -> None:
        market = np.arange(100, dtype=float)
        stock = market * 2 + 10
        assert abs(correlation(stock, market) - 1.0) < 0.01

    def test_small_sample_returns_zero(self) -> None:
        assert correlation([0.1, 0.2], [0.3, 0.4]) == 0.0


# ── sharpe_ratio ──────────────────────────────────────────────────────────────


class TestSharpeRatio:
    def test_positive_returns_positive_sharpe(self) -> None:
        """正收益序列 → 正夏普比率。（收益率用小数）"""
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.0001, 252)  # 日收益约0.1%，微小波动
        s = sharpe_ratio(returns)
        assert s > 0

    def test_negative_returns_negative_sharpe(self) -> None:
        """负收益序列 → 负夏普比率。"""
        np.random.seed(43)
        returns = np.random.normal(-0.001, 0.0001, 252)  # 日收益约-0.1%，微小波动
        s = sharpe_ratio(returns)
        assert s < 0

    def test_small_sample_returns_zero(self) -> None:
        assert sharpe_ratio([0.1, -0.1]) == 0.0

    def test_zero_volatility_returns_zero(self) -> None:
        """零波动 → 夏普 = 0（避免除零）。"""
        returns = np.ones(100) * 0.01
        assert sharpe_ratio(returns) == 0.0


# ── expected_return_adjustment ────────────────────────────────────────────────


class TestExpectedReturnAdjustment:
    def test_all_nil_returns_zero(self) -> None:
        """所有指标为空 → 调整因子 = 0。"""
        result = expected_return_adjustment({})
        assert result == 0.0

    def test_high_risk_returns_negative_adjustment(self) -> None:
        """高风险组合 → 负调整（预期收益降低）。"""
        metrics = {
            "var_95": -5.0,  # 日 VaR 5%
            "cvar_95": -7.0,  # CVaR 7%
            "beta": 1.5,  # 高 Beta
            "annualized_vol": 40.0,  # 高波动
            "max_drawdown": -30.0,  # 大回撤
            "liquidity_score": 20.0,  # 低流动性
            "gap_risk": 40.0,  # 高风险缺口
            "event_risk": 30.0,  # 高风险事件
            "valuation_risk": 50.0,  # 高估值风险
            "concentration": 60.0,  # 高集中度
            "market_regime": 40.0,  # 高风险市场
        }
        result = expected_return_adjustment(metrics)
        assert result < 0

    def test_low_risk_returns_mild_adjustment(self) -> None:
        """低风险组合 → 轻微负调整。"""
        metrics = {
            "var_95": -0.5,
            "cvar_95": -0.7,
            "beta": 0.8,
            "annualized_vol": 15.0,
            "max_drawdown": -8.0,
            "liquidity_score": 80.0,
            "gap_risk": 5.0,
            "event_risk": 5.0,
            "valuation_risk": 10.0,
            "concentration": 10.0,
            "market_regime": 10.0,
        }
        result = expected_return_adjustment(metrics)
        assert -1.0 < result < 0

    def test_missing_optional_fields_uses_defaults(self) -> None:
        """缺失可选字段时不应报错。"""
        metrics = {"var_95": -1.0, "cvar_95": -1.5}
        result = expected_return_adjustment(metrics)
        assert isinstance(result, float)
