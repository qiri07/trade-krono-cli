"""
Risk Models — 风险量化模型。

提供：
  VaR (Value at Risk)           历史模拟法，给定置信水平的最大损失
  CVaR (Conditional VaR)        尾部条件期望损失
  Beta                          个股相对市场的系统性风险
  SharpeRatio                   风险调整后收益
  ExpectedReturnAdjustment      综合风险 → 预期收益调整因子

权重来源：RISK_NORMALIZATION_WEIGHTS（模块级常量，同时驱动总风险分加权
和预期收益调整，确保两处权重一致）。

使用方式：
    from trade_krono_cli.risk.models import (
        historical_var, conditional_var, beta,
        sharpe_ratio, expected_return_adjustment,
    )

    returns = (close.pct_change() * 100).dropna().values
    var_95 = historical_var(returns, confidence=0.95)   # → -2.3 (每日)
    cvar_95 = conditional_var(returns, confidence=0.95) # → -3.1 (每日)
    b = beta(returns, market_returns)                    # → 1.2
    adj = expected_return_adjustment({
        "var_95": var_95, "cvar_95": cvar_95, "beta": b,
        "annualized_vol": 35.0, "max_drawdown": -18.0,
        "liquidity_score": 70.0, "gap_risk": 25.0,
    })
    # → -0.08 (预期收益降低 8%)
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════
# 共享权重 — 同时驱动总风险分加权和预期收益调整
# ═══════════════════════════════════════════════════════

#: 各风险维度在 expected_return_adjustment 中的归一化权重。
#: 键名须与 RiskMetrics 字段对齐（score 类字段用 _score 后缀）。
#: 与 RiskWeights 字段对应关系：
#:   var_95/cvar_95/beta/annualized_vol/max_drawdown → 原始值维度
#:   liquidity_score     → weights.liquidity
#:   gap_risk            → weights.gap_risk
#:   event_risk          → weights.event_risk
#:   valuation_risk      → weights.valuation_risk
#:   concentration       → weights.concentration
#:   market_regime       → weights.market_regime
RISK_NORMALIZATION_WEIGHTS: dict[str, float] = {
    "var_95": 0.15,
    "cvar_95": 0.15,
    "beta": 0.10,
    "annualized_vol": 0.15,
    "max_drawdown": 0.15,
    "liquidity_score": 0.10,
    "gap_risk": 0.05,
    "event_risk": 0.05,
    "valuation_risk": 0.03,
    "concentration": 0.02,
    "market_regime": 0.05,
}


# ═══════════════════════════════════════════════════════
# VaR / CVaR
# ═══════════════════════════════════════════════════════


def historical_var(
    returns: np.ndarray | list[float],
    confidence: float = 0.95,
) -> float:
    """
    历史模拟法 VaR。

    Parameters
    ----------
    returns    : 日收益率序列（百分比，如 [0.5, -1.2, ...]）
    confidence : 置信水平（0.90 / 0.95 / 0.99）

    Returns
    -------
    float : VaR（负值表示损失，如 -2.3 表示 95% 分位损失 2.3%）
    """
    arr = np.asarray(returns, dtype=float)
    if len(arr) < 20:
        return 0.0
    alpha = 1.0 - confidence
    var = float(np.percentile(arr, alpha * 100))
    return round(var, 4)


def conditional_var(
    returns: np.ndarray | list[float],
    confidence: float = 0.95,
) -> float:
    """
    CVaR（Expected Shortfall）— VaR 以下的平均损失。

    Parameters
    ----------
    returns    : 日收益率序列
    confidence : 置信水平

    Returns
    -------
    float : CVaR（负值）
    """
    arr = np.asarray(returns, dtype=float)
    if len(arr) < 20:
        return 0.0
    var = historical_var(arr, confidence)
    tail = arr[arr <= var]
    if len(tail) == 0:
        return var
    return round(float(tail.mean()), 4)


def var_annualized(returns: np.ndarray | list[float], confidence: float = 0.95) -> float:
    """年化 VaR（日 VaR × √252）。"""
    daily = historical_var(returns, confidence)
    return round(daily * math.sqrt(252), 4)


def cvar_annualized(returns: np.ndarray | list[float], confidence: float = 0.95) -> float:
    """年化 CVaR（日 CVaR × √252）。"""
    daily = conditional_var(returns, confidence)
    return round(daily * math.sqrt(252), 4)


# ═══════════════════════════════════════════════════════
# Beta & Sharpe
# ═══════════════════════════════════════════════════════


def beta(
    stock_returns: np.ndarray | list[float],
    market_returns: np.ndarray | list[float] | None = None,
) -> float:
    """
    计算股票相对市场的 Beta。

    若无市场数据，返回 1.0（默认值）。

    Parameters
    ----------
    stock_returns    : 股票日收益率序列
    market_returns   : 市场（如沪深300）日收益率序列，可选

    Returns
    -------
    float : Beta 值（>1 高系统风险，<1 低系统风险）
    """
    stock = np.asarray(stock_returns, dtype=float)
    if len(stock) < 30:
        return 1.0

    if market_returns is None:
        return 1.0

    market = np.asarray(market_returns, dtype=float)
    min_len = min(len(stock), len(market))
    stock = stock[:min_len]
    market = market[:min_len]

    cov = np.cov(stock, market)[0, 1]
    var_m = np.var(market, ddof=1)
    if var_m == 0:
        return 1.0
    return round(float(cov / var_m), 4)


def correlation(
    stock_returns: np.ndarray | list[float],
    market_returns: np.ndarray | list[float],
) -> float:
    """股票与市场的相关系数。"""
    stock = np.asarray(stock_returns, dtype=float)
    market = np.asarray(market_returns, dtype=float)
    n = min(len(stock), len(market))
    if n < 20:
        return 0.0
    return round(float(np.corrcoef(stock[:n], market[:n])[0, 1]), 4)


def sharpe_ratio(
    returns: np.ndarray | list[float],
    risk_free: float = 0.02 / 252,
) -> float:
    """
    年化夏普比率。

    Parameters
    ----------
    returns    : 日收益率（小数，如 0.001 表示 0.1%）
    risk_free  : 无风险利率（年化，默认 2%）

    Returns
    -------
    float : 年化夏普比率
    """
    arr = np.asarray(returns, dtype=float)
    if len(arr) < 20:
        return 0.0
    std = np.std(arr, ddof=1)
    if std < 1e-12 or np.isnan(std):
        return 0.0
    excess = arr - risk_free
    return round(float(np.mean(excess) / std * math.sqrt(252)), 4)


# ═══════════════════════════════════════════════════════
# Expected Return Adjustment
# ═══════════════════════════════════════════════════════


def expected_return_adjustment(
    risk_metrics: dict,
    weights: Optional[dict] = None,
) -> float:
    """
    根据多维风险指标计算预期收益调整因子。

    公式：
      adj = exp( - Σ w_i × score_i )
      其中 score_i ∈ [0, 1] 为各风险维度的归一化得分

    Parameters
    ----------
    risk_metrics : dict，包含以下字段（全部可选）：
      - var_95         : 日 VaR（负值，如 -2.3 表示损失 2.3%）
      - cvar_95        : 日 CVaR（负值）
      - beta           : 市场 Beta（默认 1.0）
      - annualized_vol : 年化波动率（%，如 35.0）
      - max_drawdown   : 最大回撤（负值，如 -18.0）
      - liquidity_score: 流动性风险分（0-100，已有）
      - gap_risk       : 缺口风险分（0-100，已有）
      - event_risk     : 事件风险分（0-100，已有）
      - valuation_risk : 估值风险分（0-100，已有）
      - concentration  : 集中度风险分（0-100，已有）
      - market_regime  : 市场环境风险分（0-100，已有）
    weights : 各维度权重 dict，可选（默认使用 RISK_NORMALIZATION_WEIGHTS）

    Returns
    -------
    float : 预期收益调整因子（∈ [-0.25, 0]）
    """
    w = {**RISK_NORMALIZATION_WEIGHTS, **(weights or {})}
    total_w = sum(w.values())
    w = {k: v / total_w for k, v in w.items()}

    def _normalize(key: str, raw) -> float:
        """将原始风险值归一化到 [0, 1]。"""
        if raw is None:
            return 0.5  # 中性
        if key == "var_95":
            return max(0.0, min(1.0, (-raw / 10.0)))
        elif key == "cvar_95":
            return max(0.0, min(1.0, (-raw / 15.0)))
        elif key == "beta":
            return max(0.0, min(1.0, (raw - 0.5) / 1.5))
        elif key == "annualized_vol":
            return max(0.0, min(1.0, raw / 60.0))
        elif key == "max_drawdown":
            return max(0.0, min(1.0, (-raw / 30.0)))
        elif key in (
            "liquidity_score",
            "gap_risk",
            "event_risk",
            "valuation_risk",
            "concentration",
            "market_regime",
        ):
            return max(0.0, min(1.0, raw / 100.0))
        return 0.5

    weighted_sum = sum(
        w[key] * _normalize(key, score) for key, score in risk_metrics.items() if key in w
    )

    return round(math.exp(-weighted_sum) - 1.0, 4)


def adjust_expected_return(
    raw_return: float,
    risk_metrics: dict,
    weights: Optional[dict] = None,
) -> float:
    """
    对原始预期收益率施加风险调整。

    Parameters
    ----------
    raw_return  : 原始预期收益率（%，如 15.0 表示 15%）
    risk_metrics: 风险指标 dict（同 expected_return_adjustment）
    weights     : 可选权重 dict

    Returns
    -------
    float : 风险调整后的预期收益率（%）
    """
    adj = expected_return_adjustment(risk_metrics, weights=weights)
    return round(raw_return * (1.0 + adj), 4)


# ═══════════════════════════════════════════════════════
# Gap Risk
# ═══════════════════════════════════════════════════════


def gap_risk_score(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    min_gap_pct: float = 3.0,
) -> float:
    """
    计算缺口风险分（0-100）。

    逻辑：统计历史 N 日内出现跳空缺口（Gap > threshold）的频率，
    频率越高 → 风险分越高。

    Parameters
    ----------
    close        : 收盘价序列
    high         : 最高价序列
    low          : 最低价序列
    min_gap_pct  : 最小缺口百分比阈值（默认 3%）

    Returns
    -------
    float : 缺口风险分 0-100
    """
    if len(close) < 30:
        return 50.0

    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return 50.0

    large_moves = (returns.abs() > min_gap_pct / 100.0).sum()
    freq = large_moves / len(returns)

    score = min(100.0, freq * 100.0 / 0.1)
    return round(max(0.0, score), 1)


# ═══════════════════════════════════════════════════════
# Event Risk（波动率突变）
# ═══════════════════════════════════════════════════════


def event_risk_score(
    close: pd.Series,
    short_window: int = 10,
    long_window: int = 60,
) -> float:
    """
    计算事件风险分（0-100）。

    逻辑：比较短期波动率和长期波动率的比值，
    短/长 >> 1 表示近期波动异常加剧（事件驱动）。

    Parameters
    ----------
    close        : 收盘价序列
    short_window : 短期窗口（默认 10 日）
    long_window  : 长期窗口（默认 60 日）

    Returns
    -------
    float : 事件风险分 0-100
    """
    if len(close) < long_window:
        return 50.0

    returns = close.pct_change().dropna()
    if len(returns) < short_window:
        return 50.0

    short_vol = returns.tail(short_window).std()
    long_vol = returns.tail(long_window).std()

    if long_vol == 0 or short_vol == 0:
        return 50.0

    ratio = short_vol / long_vol
    score = 50.0 * ratio
    return round(max(0.0, min(100.0, score)), 1)


# ═══════════════════════════════════════════════════════
# Valuation Risk（估值风险）
# ═══════════════════════════════════════════════════════


def valuation_risk_score(
    pe_ttm: Optional[float],
    pb: Optional[float],
    market_cap_billion: Optional[float] = None,
) -> float:
    """
    计算估值风险分（0-100）。

    逻辑：PE/PB 过高 → 估值风险高；市值过小 → 流动性/估值双高风险。

    Parameters
    ----------
    pe_ttm               : 市盈率 TTM（None 表示无数据）
    pb                   : 市净率（None 表示无数据）
    market_cap_billion   : 总市值（亿元），用于补充小市值风险

    Returns
    -------
    float : 估值风险分 0-100
    """
    components: list[tuple[str, float, float]] = []

    if pe_ttm is not None and pe_ttm > 0:
        pe_score = min(100.0, max(0.0, (pe_ttm - 10.0) / 90.0 * 100.0))
        components.append(("pe", pe_score, 0.4))
    elif pe_ttm is not None and pe_ttm <= 0:
        components.append(("pe_loss", 80.0, 0.4))
    else:
        components.append(("pe_missing", 50.0, 0.2))

    if pb is not None and pb > 0:
        pb_score = min(100.0, max(0.0, (pb - 0.5) / 4.5 * 100.0))
        components.append(("pb", pb_score, 0.3))
    else:
        components.append(("pb_missing", 50.0, 0.1))

    if market_cap_billion is not None and market_cap_billion > 0:
        if market_cap_billion < 20:
            cap_score = 80.0
        elif market_cap_billion < 50:
            cap_score = 50.0
        elif market_cap_billion < 100:
            cap_score = 30.0
        else:
            cap_score = 10.0
        components.append(("market_cap", cap_score, 0.2))

    if not components:
        return 50.0

    total_score = sum(s * w for _, s, w in components)
    total_weight = sum(w for _, _, w in components)
    return round(total_score / total_weight, 1) if total_weight > 0 else 50.0
