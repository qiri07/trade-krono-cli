"""市场环境风险模块 — Market Regime Risk。

计算基于趋势动量的市场环境风险分，映射为 0-100 风险分。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trade_krono_cli.configs.risk import MarketRegimeThresholds

if TYPE_CHECKING:
    import pandas as pd


def calc_market_regime_risk(
    close: pd.Series,
    thresholds: MarketRegimeThresholds | None = None,
) -> float:
    """计算市场环境风险分。

    逻辑：
      1. 计算 20 日动量（短期涨跌）
      2. 计算 60 日趋势（中长期方向）
      3. 趋势越弱/下跌，风险越高

    Parameters
    ----------
    close      : pd.Series 收盘价序列
    thresholds : MarketRegimeThresholds  分段映射参数（可选，默认使用 schema 默认值）

    Returns
    -------
    risk_score : 0-100，越高越危险

    """
    th = thresholds or MarketRegimeThresholds()

    if len(close) < th.insufficient_data_min_rows:
        return th.insufficient_data_score

    momentum_20 = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
    if len(close) >= 60:
        momentum_60 = (close.iloc[-1] - close.iloc[-60]) / close.iloc[-60] * 100
    else:
        momentum_60 = momentum_20

    avg_momentum = (momentum_20 + momentum_60) / 2.0

    # 动量映射（阈值来自配置）：
    #   <= bear_threshold → bear_score 分（强烈下跌趋势，高风险）
    #   bear_threshold~neutral_low → 递减风险
    #   neutral_low~neutral_high  → 温和区间
    #   > neutral_high   → 递减至 bull_base_score
    if avg_momentum <= th.bear_threshold:
        risk_score = th.bear_score
    elif avg_momentum <= th.neutral_low:
        risk_score = th.neutral_mid_score + (
            (th.neutral_low - avg_momentum)
            / (th.neutral_low - th.bear_threshold)
            * (th.bear_score - th.neutral_mid_score)
        )
    elif avg_momentum <= th.neutral_high:
        risk_score = max(
            0.0,
            th.neutral_mid_score
            - (
                (avg_momentum - th.neutral_low)
                / (th.neutral_high - th.neutral_low)
                * th.neutral_mid_score
            ),
        )
    else:
        risk_score = max(
            0.0,
            th.bull_base_score
            - ((avg_momentum - th.neutral_high) / th.neutral_high * (th.bull_base_score)),
        )

    return round(max(0.0, min(100.0, risk_score)), 1)
