"""
风险引擎主模块 — Risk Engine。

聚合所有风险维度，输出 0-100 综合风险分。

输出示例：
  Risk Score for sh.600519 (2026-08-11)
  ====================================
    流动性风险       8
    波动率风险      12
    回撤风险        15
    集中度风险       5
    市场环境风险    10
  ------------------------------------
    Total Risk     50.0
  ====================================
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from trade_krono_cli.configs.schema import RiskConfig
from trade_krono_cli.risk.volatility import calc_volatility_risk
from trade_krono_cli.risk.drawdown import calc_drawdown_risk
from trade_krono_cli.risk.liquidity import calc_liquidity_risk
from trade_krono_cli.risk.concentration import calc_concentration_risk
from trade_krono_cli.risk.market_regime import calc_market_regime_risk


@dataclass
class RiskScore:
    """单只股票的风险评分结果。"""
    ticker: str
    date: str

    volatility_score: float = 0.0
    drawdown_score: float = 0.0
    liquidity_score: float = 0.0
    concentration_score: float = 0.0
    market_regime_score: float = 0.0

    total_risk: float = 0.0

    # 原始数据（供调试）
    annualized_vol: Optional[float] = None
    max_drawdown: Optional[float] = None
    avg_turnover: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def print_report(self) -> str:
        lines = [
            f"{'=' * 36}",
            f"  Risk Score for {self.ticker} ({self.date})",
            f"{'=' * 36}",
            f"  流动性风险     {self.liquidity_score:>4.0f}",
            f"  波动率风险     {self.volatility_score:>4.0f}",
            f"  回撤风险       {self.drawdown_score:>4.0f}",
            f"  集中度风险     {self.concentration_score:>4.0f}",
            f"  市场环境风险   {self.market_regime_score:>4.0f}",
            f"{'─' * 36}",
            f"  Total Risk    {self.total_risk:>5.1f}",
            f"{'=' * 36}",
        ]
        return "\n".join(lines)


class RiskEngine:
    """
    风险引擎：多维度风险量化，输出 0-100 综合风险分。

    用法：
        engine = RiskEngine()
        risk = engine.assess(ticker, date, kline_df, quote_data=None, ta_result=None)
        print(risk.print_report())
    """

    def __init__(self, risk_config: Optional[RiskConfig] = None):
        self._config = risk_config or RiskConfig()
        self._weights = {
            "volatility":    self._config.weights.volatility,
            "drawdown":      self._config.weights.drawdown,
            "liquidity":     self._config.weights.liquidity,
            "concentration": self._config.weights.concentration,
            "market_regime": self._config.weights.market_regime,
        }
        logger.debug(f"RiskEngine initialized | weights={self._weights}")

    def assess(
        self,
        ticker: str,
        date: str,
        kline_df: pd.DataFrame,
        quote_data: Optional[dict] = None,
        ta_result=None,
    ) -> RiskScore:
        """
        对单只股票进行全面风险评估。

        Parameters
        ----------
        ticker      : 股票代码
        date        : 评估日期
        kline_df    : K 线 DataFrame（含 open/high/low/close/volume 列）
        quote_data  : 实时估值数据（可选，含 market_cap）
        ta_result   : StockAnalysisResult（可选，用于集中度分析）

        Returns
        -------
        RiskScore
        """
        close = kline_df["close"].astype(float)
        high = kline_df["high"].astype(float)
        volume = kline_df["volume"].astype(float)

        vol_score, ann_vol = calc_volatility_risk(
            close, thresholds=self._config.volatility
        )
        dd_score, max_dd = calc_drawdown_risk(
            high, close, thresholds=self._config.drawdown
        )

        market_cap = quote_data.get("market_cap") if quote_data else None
        liq_score, avg_turnover = calc_liquidity_risk(
            volume, market_cap, thresholds=self._config.liquidity
        )

        conc_score = calc_concentration_risk(ta_result)
        regime_score = calc_market_regime_risk(
            close, thresholds=self._config.market_regime
        )

        total = (
            vol_score * self._weights["volatility"]
            + dd_score * self._weights["drawdown"]
            + liq_score * self._weights["liquidity"]
            + conc_score * self._weights["concentration"]
            + regime_score * self._weights["market_regime"]
        )

        return RiskScore(
            ticker=ticker,
            date=date,
            volatility_score=vol_score,
            drawdown_score=dd_score,
            liquidity_score=liq_score,
            concentration_score=conc_score,
            market_regime_score=regime_score,
            total_risk=round(total, 1),
            annualized_vol=ann_vol,
            max_drawdown=max_dd,
            avg_turnover=avg_turnover,
        )


def assess_risk(
    ticker: str,
    date: str,
    kline_df: pd.DataFrame,
    quote_data: Optional[dict] = None,
    ta_result=None,
    risk_config: Optional[RiskConfig] = None,
) -> RiskScore:
    """便捷函数：单步评估某只股票的风险。"""
    engine = RiskEngine(risk_config=risk_config)
    return engine.assess(ticker, date, kline_df, quote_data, ta_result)
