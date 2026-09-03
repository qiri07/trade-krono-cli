"""风险引擎主模块 — Risk Engine v2。

聚合所有风险维度，输出多维风险指标（VaR/CVaR/Beta/Gap/Event/Valuation），
并通过 expected_return_adjustment 计算预期收益调整因子。

架构：
  Expected Return
       │
       ▼
  Risk Model
       ├── VaR / CVaR     （尾部风险）
       ├── Beta            （系统性风险）
       ├── Volatility      （总波动率）
       ├── Max Drawdown    （最大回撤）
       ├── Liquidity       （流动性风险）
       ├── Gap Risk        （跳空缺口风险）
       ├── Event Risk      （事件驱动异常）
       ├── Valuation Risk  （估值风险）
       └── Market Regime   （市场环境风险）

权重来源：models.RISK_NORMALIZATION_WEIGHTS（与 expected_return_adjustment 共享）。

输出示例：
  RiskMetrics for sh.600519 (2026-08-11)
  ======================================
    VaR(95%)        -2.34%
    CVaR(95%)       -3.12%
    Beta            1.15
    Ann. Volatility 32.5%
    Max Drawdown   -18.3%
    Gap Risk         25
    Event Risk       42
    Valuation Risk   30
    Liquidity Risk   12
    Market Regime    28
  --------------------------------------
    Total Risk      45.2
    Return Adj      -0.062  (预期收益降低 6.2%)
  ======================================
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from loguru import logger

from trade_krono_cli.configs.risk import RiskConfig
from trade_krono_cli.risk.concentration import calc_concentration_risk
from trade_krono_cli.risk.drawdown import calc_drawdown_risk
from trade_krono_cli.risk.event_risk import calc_event_risk
from trade_krono_cli.risk.gap_risk import calc_gap_risk
from trade_krono_cli.risk.liquidity import calc_liquidity_risk
from trade_krono_cli.risk.market_regime import calc_market_regime_risk
from trade_krono_cli.risk.models import (
    beta as calc_beta,
)
from trade_krono_cli.risk.models import (
    conditional_var,
    expected_return_adjustment,
    historical_var,
)
from trade_krono_cli.risk.valuation_risk import calc_valuation_risk
from trade_krono_cli.risk.volatility import calc_volatility_risk

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

# 评分维度权重（从 RiskWeights 映射到 models 键名，beta 除外——它在
# expected_return_adjustment 中单独处理，不与 total_risk 双重计入）
_SCORE_WEIGHT_KEYS: tuple[tuple[str, str], ...] = (
    ("volatility", "volatility"),
    ("drawdown", "drawdown"),
    ("liquidity", "liquidity_score"),
    ("concentration", "concentration"),
    ("market_regime", "market_regime"),
    ("gap_risk", "gap_risk"),
    ("event_risk", "event_risk"),
    ("valuation_risk", "valuation_risk"),
)


@dataclass
class RiskScore:
    """向后兼容的风险评分结果（0-100 综合分）。

    仅保留原始 5 个维度 + 总分 + 3 个原始值，不含 VaR/Beta/新风险分。
    新架构请使用 RiskMetrics。
    """

    ticker: str
    date: str

    volatility_score: float = 0.0
    drawdown_score: float = 0.0
    liquidity_score: float = 0.0
    concentration_score: float = 0.0
    market_regime_score: float = 0.0

    total_risk: float = 0.0

    annualized_vol: float | None = None
    max_drawdown: float | None = None
    avg_turnover: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def print_report(self) -> str:
        lines = [
            f"{'=' * 40}",
            f"  Risk Score for {self.ticker} ({self.date})",
            f"{'=' * 40}",
            f"  流动性风险       {self.liquidity_score:>4.0f}",
            f"  波动率风险       {self.volatility_score:>4.0f}",
            f"  回撤风险         {self.drawdown_score:>4.0f}",
            f"  集中度风险       {self.concentration_score:>4.0f}",
            f"  市场环境风险     {self.market_regime_score:>4.0f}",
            f"{'─' * 40}",
            f"  Total Risk      {self.total_risk:>5.1f}",
            f"{'=' * 40}",
        ]
        return "\n".join(lines)


@dataclass
class RiskMetrics:
    """多维度风险指标（新架构输出）。

    包含：
      - VaR / CVaR          尾部风险度量
      - Beta                系统性风险
      - 年化波动率           总风险水平
      - 最大回撤             极端损失
      - 流动性/缺口/事件/估值/市场环境风险分
      - 预期收益调整因子     综合风险 → 预期收益映射
    """

    ticker: str
    date: str

    var_95: float | None = None
    cvar_95: float | None = None
    beta: float | None = None
    annualized_vol: float | None = None
    max_drawdown: float | None = None

    volatility_score: float = 0.0
    drawdown_score: float = 0.0
    liquidity_score: float = 0.0
    concentration_score: float = 0.0
    market_regime_score: float = 0.0
    gap_risk_score: float = 0.0
    event_risk_score: float = 0.0
    valuation_risk_score: float = 0.0

    total_risk: float = 0.0
    return_adjustment: float = 0.0

    avg_turnover: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_adjustment_input(self) -> dict:
        """转换为 expected_return_adjustment() 所需的 dict 格式。"""
        return {
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "beta": self.beta,
            "annualized_vol": self.annualized_vol,
            "max_drawdown": self.max_drawdown,
            "liquidity_score": self.liquidity_score,
            "gap_risk": self.gap_risk_score,
            "event_risk": self.event_risk_score,
            "valuation_risk": self.valuation_risk_score,
            "concentration": self.concentration_score,
            "market_regime": self.market_regime_score,
        }

    def print_report(self) -> str:
        lines = [
            f"{'=' * 44}",
            f"  Risk Metrics for {self.ticker} ({self.date})",
            f"{'=' * 44}",
            (
                f"  VaR(95%)          {self.var_95:>7.2f}%"
                if self.var_95
                else "  VaR(95%)         n/a"
            ),
            (
                f"  CVaR(95%)         {self.cvar_95:>7.2f}%"
                if self.cvar_95
                else "  CVaR(95%)        n/a"
            ),
            (f"  Beta              {self.beta:>7.2f}" if self.beta else "  Beta             n/a"),
            (
                f"  Ann. Volatility   {self.annualized_vol:>7.1f}%"
                if self.annualized_vol
                else "  Ann. Vol.        n/a"
            ),
            (
                f"  Max Drawdown      {self.max_drawdown:>7.1f}%"
                if self.max_drawdown
                else "  Max DD           n/a"
            ),
            f"{'─' * 44}",
            f"  Gap Risk          {self.gap_risk_score:>6.0f}",
            f"  Event Risk        {self.event_risk_score:>6.0f}",
            f"  Valuation Risk    {self.valuation_risk_score:>6.0f}",
            f"  Liquidity Risk    {self.liquidity_score:>6.0f}",
            f"  Market Regime     {self.market_regime_score:>6.0f}",
            f"{'─' * 44}",
            f"  Total Risk        {self.total_risk:>6.1f}",
            (f"  Return Adj        {self.return_adjustment:>6.3f}  "
            f"({self.return_adjustment * 100:+.1f}%)"),
            f"{'=' * 44}",
        ]
        return "\n".join(lines)


class RiskEngine:
    """风险引擎 v2：多维度风险量化 + VaR/CVaR/Beta + 预期收益调整。

    用法：
        engine = RiskEngine()
        score, metrics = engine.assess(ticker, date, kline_df, quote_data=None)
        print(metrics.print_report())

        # 调整预期收益
        adj = metrics.return_adjustment  # e.g. -0.062
        raw_return = 15.0
        adjusted = raw_return * (1 + adj)  # ≈ 14.07%
    """

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._config = risk_config or RiskConfig()
        # 评分维度权重（不含 beta——beta 已通过 expected_return_adjustment 处理）
        w = self._config.weights
        self._weights = {
            "volatility": w.volatility,
            "drawdown": w.drawdown,
            "liquidity": w.liquidity,
            "concentration": w.concentration,
            "market_regime": w.market_regime,
            "gap_risk": w.gap_risk,
            "event_risk": w.event_risk,
            "valuation_risk": w.valuation_risk,
        }
        logger.debug(f"RiskEngine v2 initialized | weights={self._weights}")

    def assess(
        self,
        ticker: str,
        date: str,
        kline_df: pd.DataFrame,
        quote_data: dict | None = None,
        ta_result=None,
        market_returns: np.ndarray | None = None,
    ) -> tuple[RiskScore, RiskMetrics]:
        """对单只股票进行全面风险评估。

        Returns
        -------
        (RiskScore, RiskMetrics)
          RiskScore  : 向后兼容的 0-100 综合风险分
          RiskMetrics: 详细风险指标（VaR/CVaR/Beta/各风险分/预期收益调整）

        """
        close = kline_df["close"].astype(float)
        high = kline_df["high"].astype(float)
        low = kline_df["low"].astype(float)
        volume = kline_df["volume"].astype(float)

        # ── 基础风险分 ───────────────────────────────────────────────────────
        vol_score, ann_vol = calc_volatility_risk(close, thresholds=self._config.volatility)
        dd_score, max_dd = calc_drawdown_risk(high, close, thresholds=self._config.drawdown)

        market_cap = quote_data.get("market_cap") if quote_data else None
        liq_score, avg_turnover = calc_liquidity_risk(
            volume, market_cap, thresholds=self._config.liquidity,
        )

        conc_score = calc_concentration_risk(ta_result)
        regime_score = calc_market_regime_risk(close, thresholds=self._config.market_regime)

        # ── 新增风险分 ───────────────────────────────────────────────────────
        gap_score = calc_gap_risk(
            close,
            high,
            low,
            min_gap_pct=self._config.gap_risk.min_gap_pct,
        )
        event_score = calc_event_risk(
            close,
            short_window=self._config.event_risk.short_window,
            long_window=self._config.event_risk.long_window,
        )

        pe_ttm = quote_data.get("pe_ttm") if quote_data else None
        pb = quote_data.get("pb") if quote_data else None
        val_score = calc_valuation_risk(pe_ttm, pb, market_cap)

        # ── VaR / CVaR ───────────────────────────────────────────────────────
        returns_pct = close.pct_change().dropna() * 100
        conf = self._config.var_confidence
        lookback = min(self._config.var_lookback, len(returns_pct))
        recent_returns = returns_pct.tail(lookback).values

        var_95 = historical_var(recent_returns, confidence=conf)
        cvar_95 = conditional_var(recent_returns, confidence=conf)

        # ── Beta ─────────────────────────────────────────────────────────────
        beta_val = self._config.beta_default
        if market_returns is not None and len(recent_returns) >= 30:
            beta_val = calc_beta(recent_returns, market_returns)

        # ── 综合风险分（加权求和，不含 beta）────────────────────────────────
        total = sum(
            score * self._weights[dim]
            for dim, score in [
                ("volatility", vol_score),
                ("drawdown", dd_score),
                ("liquidity", liq_score),
                ("concentration", conc_score),
                ("market_regime", regime_score),
                ("gap_risk", gap_score),
                ("event_risk", event_score),
                ("valuation_risk", val_score),
            ]
        )

        # ── 预期收益调整（含 beta，使用共享权重）─────────────────────────────
        adjustment_input = {
            "var_95": var_95,
            "cvar_95": cvar_95,
            "beta": beta_val,
            "annualized_vol": ann_vol,
            "max_drawdown": max_dd,
            "liquidity_score": liq_score,
            "gap_risk": gap_score,
            "event_risk": event_score,
            "valuation_risk": val_score,
            "concentration": conc_score,
            "market_regime": regime_score,
        }
        return_adj = expected_return_adjustment(adjustment_input)

        # ── 构建 RiskScore（向后兼容）────────────────────────────────────────
        risk_score = RiskScore(
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

        # ── 构建 RiskMetrics（新架构）────────────────────────────────────────
        risk_metrics = RiskMetrics(
            ticker=ticker,
            date=date,
            var_95=var_95,
            cvar_95=cvar_95,
            beta=beta_val,
            annualized_vol=ann_vol,
            max_drawdown=max_dd,
            volatility_score=vol_score,
            drawdown_score=dd_score,
            liquidity_score=liq_score,
            concentration_score=conc_score,
            market_regime_score=regime_score,
            gap_risk_score=gap_score,
            event_risk_score=event_score,
            valuation_risk_score=val_score,
            total_risk=round(total, 1),
            return_adjustment=round(return_adj, 4),
            avg_turnover=avg_turnover,
        )

        return risk_score, risk_metrics


def assess_risk(
    ticker: str,
    date: str,
    kline_df: pd.DataFrame,
    quote_data: dict | None = None,
    ta_result=None,
    risk_config: RiskConfig | None = None,
) -> tuple[RiskScore, RiskMetrics]:
    """便捷函数：单步评估某只股票的风险，返回 (RiskScore, RiskMetrics)。"""
    engine = RiskEngine(risk_config=risk_config)
    return engine.assess(ticker, date, kline_df, quote_data, ta_result)
