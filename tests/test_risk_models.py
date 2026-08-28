"""测试风险模型模块（VaR/CVaR/Beta/Gap/Event/Valuation）。"""

import numpy as np
import pandas as pd

from trade_krono_cli.risk.models import (
    adjust_expected_return,
    beta,
    conditional_var,
    correlation,
    cvar_annualized,
    event_risk_score,
    expected_return_adjustment,
    gap_risk_score,
    historical_var,
    sharpe_ratio,
    valuation_risk_score,
    var_annualized,
)

# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _make_returns(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=float)


def _make_close(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


# ═══════════════════════════════════════════════════════
# VaR / CVaR 测试
# ═══════════════════════════════════════════════════════


class TestHistoricalVar:
    def test_normal_distribution(self):
        """正态分布收益率 → 合理的 VaR。"""
        np.random.seed(42)
        returns = np.random.normal(0.05, 1.5, 500)
        var_95 = historical_var(returns, confidence=0.95)
        # VaR 应为负值（损失）
        assert var_95 < 0
        assert var_95 > -10  # 合理范围

    def test_insufficient_data(self):
        """数据不足时返回 0。"""
        returns = _make_returns([1.0, -0.5])
        assert historical_var(returns) == 0.0

    def test_confidence_levels(self):
        """不同置信水平：99% > 95% > 90%（绝对值）。"""
        np.random.seed(0)
        returns = np.random.normal(0.0, 1.2, 1000)
        var_90 = historical_var(returns, confidence=0.90)
        var_95 = historical_var(returns, confidence=0.95)
        var_99 = historical_var(returns, confidence=0.99)
        assert var_99 < var_95 < var_90 < 0  # 都是负值，99%最保守


class TestConditionalVar:
    def test_cvar_is_worse_than_var(self):
        """CVaR 应 ≤ VaR（更保守）。"""
        np.random.seed(42)
        returns = np.random.normal(0.05, 1.5, 500)
        var_95 = historical_var(returns, confidence=0.95)
        cvar_95 = conditional_var(returns, confidence=0.95)
        assert cvar_95 <= var_95

    def test_insufficient_data(self):
        """数据不足时返回 0。"""
        returns = _make_returns([1.0, -0.5])
        assert conditional_var(returns) == 0.0


class TestAnnualizedVar:
    def test_annualized_scaling(self):
        """年化 VaR = 日 VaR × √252。"""
        returns = _make_returns([-1.0, -2.0, -1.5, -0.5] * 50)
        daily = historical_var(returns, confidence=0.95)
        annual = var_annualized(returns, confidence=0.95)
        assert abs(annual - daily * np.sqrt(252)) < 0.01

    def test_annualized_cvar(self):
        """年化 CVaR 计算正确。"""
        returns = _make_returns([-1.0, -2.0, -1.5, -0.5] * 50)
        daily = conditional_var(returns, confidence=0.95)
        annual = cvar_annualized(returns, confidence=0.95)
        assert abs(annual - daily * np.sqrt(252)) < 0.01


# ═══════════════════════════════════════════════════════
# Beta & Sharpe 测试
# ═══════════════════════════════════════════════════════


class TestBeta:
    def test_perfect_correlation(self):
        """完全相关 → Beta = 1。"""
        market = np.random.normal(0.05, 1.0, 200)
        stock = market + np.random.normal(0, 0.1, 200)
        b = beta(stock, market)
        assert abs(b - 1.0) < 0.2

    def test_no_market_returns(self):
        """无市场数据 → 返回默认值 1.0。"""
        returns = _make_returns([0.5, -0.3, 0.2, -0.1] * 50)
        assert beta(returns) == 1.0

    def test_insufficient_data(self):
        """数据不足 → 返回默认值 1.0。"""
        returns = _make_returns([1.0, -0.5])
        market = _make_returns([0.5, -0.3])
        assert beta(returns, market) == 1.0

    def test_high_beta(self):
        """高 Beta 股票。"""
        market = np.random.normal(0.05, 1.0, 200)
        stock = market * 2.0 + np.random.normal(0, 0.5, 200)
        b = beta(stock, market)
        assert b > 1.5


class TestCorrelation:
    def test_perfect_positive(self):
        """完全正相关 → 1.0。"""
        arr = np.random.randn(100)
        assert correlation(arr, arr) == 1.0

    def test_insufficient_data(self):
        """数据不足 → 返回 0。"""
        assert correlation([1.0], [1.0]) == 0.0


class TestSharpeRatio:
    def test_positive_sharpe(self):
        """正收益且有波动 → 正夏普。"""
        np.random.seed(0)
        returns = np.full(300, 0.001) + np.random.normal(0, 0.0005, 300)
        sr = sharpe_ratio(returns)
        assert sr > 0

    def test_zero_volatility(self):
        """零波动 → 夏普为 0。"""
        returns = np.full(100, 0.001)
        assert sharpe_ratio(returns) == 0.0

    def test_insufficient_data(self):
        """数据不足 → 返回 0。"""
        assert sharpe_ratio([1.0, -0.5]) == 0.0


# ═══════════════════════════════════════════════════════
# Expected Return Adjustment 测试
# ═══════════════════════════════════════════════════════


class TestExpectedReturnAdjustment:
    def test_low_risk_positive_adj(self):
        """低风险 → 调整因子接近 0（轻微加成）。"""
        metrics = {
            "var_95": -0.5,
            "cvar_95": -0.7,
            "beta": 0.6,
            "annualized_vol": 10.0,
            "max_drawdown": -5.0,
            "liquidity_score": 10.0,
            "gap_risk": 10.0,
            "event_risk": 10.0,
            "valuation_risk": 10.0,
            "concentration": 5.0,
            "market_regime": 15.0,
        }
        adj = expected_return_adjustment(metrics)
        # 低风险 → adj 应接近 0（小幅惩罚，不为正）
        assert -0.1 < adj < 0.05

    def test_high_risk_negative_adj(self):
        """高风险 → 调整因子显著为负。"""
        metrics = {
            "var_95": -5.0,
            "cvar_95": -7.0,
            "beta": 2.0,
            "annualized_vol": 60.0,
            "max_drawdown": -40.0,
            "liquidity_score": 90.0,
            "gap_risk": 80.0,
            "event_risk": 90.0,
            "valuation_risk": 85.0,
            "concentration": 80.0,
            "market_regime": 80.0,
        }
        adj = expected_return_adjustment(metrics)
        assert adj < -0.1

    def test_all_none_neutral(self):
        """全 None → 中性调整（≈0）。"""
        adj = expected_return_adjustment({})
        # 全部 None → 每个维度归一化为 0.5，加权后 ≈ -0.05 ~ 0
        assert -0.15 < adj <= 0.0

    def test_adjust_expected_return(self):
        """adjust_expected_return 返回调整后的收益率。"""
        metrics = {
            "var_95": -1.0,
            "cvar_95": -1.5,
            "beta": 1.0,
            "annualized_vol": 20.0,
            "max_drawdown": -10.0,
            "liquidity_score": 30.0,
        }
        raw = 15.0  # 预期收益 15%
        adjusted = adjust_expected_return(raw, metrics)
        assert adjusted < raw  # 有风险时应降低


# ═══════════════════════════════════════════════════════
# Gap Risk 测试
# ═══════════════════════════════════════════════════════


class TestGapRisk:
    def test_stable_prices_low_gap_risk(self):
        """平稳价格 → 低缺口风险。"""
        close = _make_close([100 + i * 0.01 for i in range(60)])
        high = close * 1.001
        low = close * 0.999
        score = gap_risk_score(close, high, low, min_gap_pct=3.0)
        assert score < 30

    def test_high_volatility_high_gap_risk(self):
        """高波动 → 高缺口风险。"""
        np.random.seed(42)
        close = _make_close(100 * (1 + np.random.randn(60) * 0.05))
        high = close * 1.02
        low = close * 0.98
        score = gap_risk_score(close, high, low, min_gap_pct=3.0)
        assert score > 30

    def test_insufficient_data(self):
        """数据不足 → 返回中性分 50。"""
        close = _make_close([100, 101])
        high = close * 1.01
        low = close * 0.99
        assert gap_risk_score(close, high, low) == 50.0


# ═══════════════════════════════════════════════════════
# Event Risk 测试
# ═══════════════════════════════════════════════════════


class TestEventRisk:
    def test_stable_event_risk(self):
        """稳定波动 → 中等事件风险。"""
        np.random.seed(0)
        close = _make_close(100 * (1 + np.random.randn(80) * 0.01))
        score = event_risk_score(close)
        assert 30 <= score <= 70

    def test_recent_vol_spike_high_event_risk(self):
        """近期波动率骤升 → 高事件风险。"""
        # 前 50 日平稳，后 30 日高波动
        vals = [100.0] * 50
        vals += [vals[-1] * (1 + np.random.randn() * 0.04) for _ in range(30)]
        close = _make_close(vals)
        score = event_risk_score(close, short_window=10, long_window=60)
        assert score > 50

    def test_insufficient_data(self):
        """数据不足 → 返回 50。"""
        close = _make_close([100, 101, 102])
        assert event_risk_score(close) == 50.0


# ═══════════════════════════════════════════════════════
# Valuation Risk 测试
# ═══════════════════════════════════════════════════════


class TestValuationRisk:
    def test_reasonable_valuation_low_risk(self):
        """合理估值 → 低估值风险。"""
        score = valuation_risk_score(pe_ttm=15.0, pb=1.5, market_cap_billion=500.0)
        assert score < 40

    def test_overvalued_high_risk(self):
        """高 PE → 高估值风险。"""
        score = valuation_risk_score(pe_ttm=150.0, pb=10.0)
        assert score > 60

    def test_loss_making_high_risk(self):
        """亏损股 → 高估值风险。"""
        score = valuation_risk_score(pe_ttm=-5.0, pb=1.0)
        assert score > 50

    def test_small_cap_extra_risk(self):
        """小市值 → 更高估值风险。"""
        score_large = valuation_risk_score(pe_ttm=20.0, pb=2.0, market_cap_billion=500.0)
        score_small = valuation_risk_score(pe_ttm=20.0, pb=2.0, market_cap_billion=10.0)
        assert score_small > score_large

    def test_missing_data_neutral(self):
        """全部缺失 → 中性分 50。"""
        assert valuation_risk_score(None, None, None) == 50.0
