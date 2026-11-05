#!/usr/bin/env python3
"""combined_stock_analysis 数据模型模块。

包含所有选股候选数据模型：
- TechView: 技术分析边界视图
- FundView: 基本面数据边界视图
- KronosView: Kronos 预测边界视图
- BacktestView: 历史回测边界视图
- StockCandidate: 交集候选股票
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_krono_cli.domain.kronos_result import KronosForecastResult


@dataclass
class TechView:
    """analyze_stock() 返回结果的技术分析边界视图。

    将松散的 dict[str, object] 收敛为带类型字段的 dataclass，
    供卡片构建 / LLM 核实 / 结果落盘统一消费，避免反复 an.get(...)。
    """

    name: str = ""
    trend: str = ""
    ma_signal: str = ""
    vol_signal: str = ""
    score: int | float = 0

    @classmethod
    def from_analysis(cls, an: dict[str, object] | None) -> TechView:
        """从 analyze_stock() 的 dict 结果构建 TechView，缺失字段取默认值。"""
        if an is None:
            return cls()
        raw = an.get("score", 0)
        if isinstance(raw, (int, float)):
            score: int | float = raw
        elif isinstance(raw, str):
            try:
                score = float(raw)
            except ValueError:
                score = 0
        else:
            score = 0
        return cls(
            name=str(an.get("name", "")),
            trend=str(an.get("trend", "")),
            ma_signal=str(an.get("ma_signal", "")),
            vol_signal=str(an.get("vol_signal", "")),
            score=score,
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "name": self.name,
            "trend": self.trend,
            "ma_signal": self.ma_signal,
            "vol_signal": self.vol_signal,
            "score": self.score,
        }


@dataclass
class FundView:
    """基本面数据边界视图（来自 analyze_stock 的估值字段）。"""

    pe_ttm: float | None = None
    pb: float | None = None
    valuation_score: float | None = None
    valuation_conclusion: str = ""

    @classmethod
    def from_analysis(cls, an: dict[str, object] | None) -> FundView:
        """从 analyze_stock() 结果构建 FundView，缺失字段取默认值。

        信任调用方类型契约（analyze_stock 返回的 dict schema 固定），
        用 _coerce_float 收敛 int/float 到 float | None。
        """
        if an is None:
            return cls()

        def _coerce(key: str) -> float | None:
            """将 dict 值收敛为 float | None（int 提升为 float，bool/非数字视为 None）。"""
            v = an.get(key)
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return float(v)
            return None

        return cls(
            pe_ttm=_coerce("pe_ttm"),
            pb=_coerce("pb"),
            valuation_score=_coerce("valuation_score"),
            valuation_conclusion=str(an.get("valuation_conclusion") or ""),
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "valuation_score": self.valuation_score,
            "valuation_conclusion": self.valuation_conclusion,
        }

    def summary_line(self) -> str:
        """单行基本面摘要（用于飞书卡片）。"""
        parts: list[str] = []
        if self.pe_ttm is not None:
            parts.append(f"PE={self.pe_ttm:.1f}")
        if self.pb is not None:
            parts.append(f"PB={self.pb:.1f}")
        if self.valuation_score is not None:
            parts.append(f"估值分={self.valuation_score:.0f}")
        if self.valuation_conclusion:
            parts.append(self.valuation_conclusion)
        return "  ".join(parts) if parts else "无基本面数据"


@dataclass
class KronosView:
    """Kronos 预测边界视图（来自 KronosForecastResult）。

    将 Kronos 预测结果收敛为带类型字段的 dataclass，
    供卡片构建 / LLM 核实 / 结果落盘统一消费。
    """

    direction: str = ""  # UP / DOWN / FLAT
    expected_change_pct: float | None = None
    last_close: float | None = None
    predicted_close: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    volatility_proxy: float | None = None
    horizon: int = 0

    @classmethod
    def from_result(cls, res: KronosForecastResult | None) -> KronosView:
        """从 KronosForecastResult 构建 KronosView，缺失字段取默认值。"""
        if res is None:
            return cls()
        band = res.confidence_band or {}
        low = band.get("low")
        high = band.get("high")
        return cls(
            direction=res.direction or "",
            expected_change_pct=(
                float(res.expected_change_pct)
                if isinstance(res.expected_change_pct, (int, float))
                else None
            ),
            last_close=(
                float(res.last_close) if isinstance(res.last_close, (int, float)) else None
            ),
            predicted_close=(
                float(res.predicted_close_final)
                if isinstance(res.predicted_close_final, (int, float))
                else None
            ),
            confidence_low=(
                float(low)
                if isinstance(low, (int, float)) and not isinstance(low, bool)
                else None
            ),
            confidence_high=(
                float(high)
                if isinstance(high, (int, float)) and not isinstance(high, bool)
                else None
            ),
            volatility_proxy=(
                float(res.volatility_proxy)
                if isinstance(res.volatility_proxy, (int, float))
                else None
            ),
            horizon=int(res.horizon) if isinstance(res.horizon, int) else 0,
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "direction": self.direction,
            "expected_change_pct": self.expected_change_pct,
            "last_close": self.last_close,
            "predicted_close": self.predicted_close,
            "confidence_low": self.confidence_low,
            "confidence_high": self.confidence_high,
            "volatility_proxy": self.volatility_proxy,
            "horizon": self.horizon,
        }

    @property
    def available(self) -> bool:
        """是否有有效预测数据。"""
        return bool(self.direction) or self.expected_change_pct is not None

    def summary_line(self) -> str:
        """单行 Kronos 预测摘要（用于飞书卡片）。"""
        if not self.available:
            return "无 Kronos 预测数据"
        parts: list[str] = []
        if self.direction:
            parts.append(f"方向={self.direction}")
        if self.expected_change_pct is not None:
            seg = f"预期 {self.expected_change_pct:+.1f}%"
            if self.last_close is not None and self.predicted_close is not None:
                seg += f" ({self.last_close:.2f}→{self.predicted_close:.2f})"
            parts.append(seg)
        if self.confidence_low is not None and self.confidence_high is not None:
            parts.append(f"置信[{self.confidence_low:+.1f}%,{self.confidence_high:+.1f}%]")
        if self.horizon:
            parts.append(f"预测{self.horizon}日")
        return "  ".join(parts)


@dataclass
class BacktestView:
    """该股信号的历史回测边界视图。

    由 attach_backtest 从 research DB 的信号 + 实际价格构建，
    展示该股历史 TA/Kronos 信号的累计回测表现。
    """

    total_return_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    win_rate_pct: float | None = None
    n_trades: int = 0
    n_signals: int = 0
    horizon_days: int = 0
    window_days: int = 0
    available: bool = False

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
            "n_trades": self.n_trades,
            "n_signals": self.n_signals,
            "horizon_days": self.horizon_days,
            "window_days": self.window_days,
            "available": self.available,
        }

    def summary_line(self) -> str:
        """单行回测摘要（用于飞书卡片）。"""
        if not self.available:
            return "无历史回测数据"
        m = {
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
        }
        parts: list[str] = [
            f"近{self.window_days}天 {self.n_signals} 笔信号 "
            f"(horizon {self.horizon_days}日, {self.n_trades} 笔成交)"
        ]
        if self.total_return_pct is not None:
            parts.append(f"组合总收益 {self.total_return_pct:+.1f}%")
        if m["sharpe_ratio"] is not None:
            parts.append(f"夏普 {m['sharpe_ratio']:.2f}")
        if m["max_drawdown_pct"] is not None:
            parts.append(f"最大回撤 {m['max_drawdown_pct']:.1f}%")
        if m["win_rate_pct"] is not None:
            parts.append(f"胜率 {m['win_rate_pct']:.0f}%")
        return "  ".join(parts)


@dataclass
class StockCandidate:
    """交集候选股票，命中多条选股线后做技术分析。"""

    code: str
    sources: list[str] = field(default_factory=list)
    hit_count: int = 0
    analysis: dict[str, object] | None = None
    kronos: KronosForecastResult | None = None
    backtest: BacktestView | None = None

    @property
    def tech(self) -> TechView:
        """分析结果的技术边界视图（无数据时为默认 TechView）。"""
        return TechView.from_analysis(self.analysis)

    @property
    def fund(self) -> FundView:
        """分析结果的基本面边界视图（无数据时为默认 FundView）。"""
        return FundView.from_analysis(self.analysis)

    @property
    def kronos_view(self) -> KronosView:
        """Kronos 预测边界视图（未开启时为默认 KronosView）。"""
        return KronosView.from_result(self.kronos)

    @property
    def rank_score(self) -> int:
        """共振加权排序分：命中条数 * 10 + 技术分。"""
        return self.hit_count * 10 + int(self.tech.score)
