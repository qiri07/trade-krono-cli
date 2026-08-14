"""
Prediction — 预测相关的领域对象。

包含：
  · PredictionDistribution  — 完整的概率分布描述（p10–p90）
  · TAAnalysis               — 技术面/基本面分析结果
  · KronosPrediction        — 时序模型预测结果
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trade_krono_cli.domain.types import Direction


# ═══════════════════════════════════════════════════════
#  PredictionDistribution
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class PredictionDistribution:
    """
    预测结果的概率分布描述。

    包含两个层次的统计信息：
      1. 摘要指标（expected_return / direction / confidence_score 等）
      2. 完整分位数（p10/p25/p50/p75/p90）

    单样本时百分位退化为最终价；多样本时从路径矩阵计算。
    """
    expected_return: Optional[float] = None       # 预期收益率（%）
    direction: Optional[Direction] = None          # UP / DOWN / FLAT
    direction_score: Optional[float] = None        # 方向强度 0-1
    volatility: Optional[float] = None             # 预测路径标准差
    path_dispersion: Optional[float] = None        # 归一化路径分散度
    confidence_score: Optional[float] = None       # 综合置信度 0-100
    sample_count_used: int = 1

    # 分位数（多样本时填充）
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None

    @property
    def predicted_final(self) -> Optional[float]:
        """预测最终价（取 p50，退化为 expected_return + last_close）。"""
        if self.p50 is not None:
            return self.p50
        return None

    def to_dict(self) -> dict:
        return {
            "expected_return": self.expected_return,
            "direction": self.direction.value if self.direction else None,
            "direction_score": self.direction_score,
            "volatility": self.volatility,
            "path_dispersion": self.path_dispersion,
            "confidence_score": self.confidence_score,
            "sample_count_used": self.sample_count_used,
            "p10": self.p10, "p25": self.p25, "p50": self.p50,
            "p75": self.p75, "p90": self.p90,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PredictionDistribution":
        direction_val = data.get("direction")
        direction = Direction.UP if direction_val == "UP" else (
            Direction.DOWN if direction_val == "DOWN" else Direction.FLAT
        ) if direction_val else None
        return cls(
            expected_return=data.get("expected_return"),
            direction=direction,
            direction_score=data.get("direction_score"),
            volatility=data.get("volatility"),
            path_dispersion=data.get("path_dispersion"),
            confidence_score=data.get("confidence_score"),
            sample_count_used=data.get("sample_count_used", 1),
            p10=data.get("p10"),
            p25=data.get("p25"),
            p50=data.get("p50"),
            p75=data.get("p75"),
            p90=data.get("p90"),
        )

    @classmethod
    def empty(cls) -> "PredictionDistribution":
        return cls()


# ═══════════════════════════════════════════════════════
#  TAAnalysis
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class TAAnalysis:
    """
    技术面 / 基本面分析结果。

    由 TradingAgents 产出，代表从多个维度对股票的分析结论。
    """
    ticker: str
    eval_date: str
    signal: "domain.Signal"                    # BUY / HOLD / SELL
    confidence: float                          # 0–100
    thesis: str = ""                           # 投资论点摘要
    reasoning: str = ""                        # 详细推理过程
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)

    # 多因子评分
    valuation_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    technical_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    capital_flow_score: Optional[float] = None
    macro_score: Optional[float] = None

    # 催化剂
    catalysts: list[str] = field(default_factory=list)

    # 错误信息（分析失败时填充）
    error: Optional[str] = None
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "signal": self.signal.value,
            "confidence": self.confidence,
            "thesis": self.thesis,
            "reasoning": self.reasoning,
            "risks": self.risks,
            "invalidations": self.invalidations,
            "valuation_score": self.valuation_score,
            "fundamental_score": self.fundamental_score,
            "technical_score": self.technical_score,
            "sentiment_score": self.sentiment_score,
            "capital_flow_score": self.capital_flow_score,
            "macro_score": self.macro_score,
            "catalysts": self.catalysts,
            "error": self.error,
            "elapsed_sec": self.elapsed_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TAAnalysis":
        from trade_krono_cli.domain import Signal
        signal_val = data.get("signal", "HOLD")
        if isinstance(signal_val, str):
            try:
                signal = Signal(signal_val)
            except ValueError:
                signal = Signal.HOLD
        else:
            signal = signal_val
        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            signal=signal,
            confidence=float(data.get("confidence", 50.0)),
            thesis=data.get("thesis", ""),
            reasoning=data.get("reasoning", ""),
            risks=data.get("risks", []),
            invalidations=data.get("invalidations", []),
            valuation_score=data.get("valuation_score"),
            fundamental_score=data.get("fundamental_score"),
            technical_score=data.get("technical_score"),
            sentiment_score=data.get("sentiment_score"),
            capital_flow_score=data.get("capital_flow_score"),
            macro_score=data.get("macro_score"),
            catalysts=data.get("catalysts", []),
            error=data.get("error"),
            elapsed_sec=float(data.get("elapsed_sec", 0.0)),
        )

    @classmethod
    def failed(cls, ticker: str, eval_date: str, error: str) -> "TAAnalysis":
        return cls(
            ticker=ticker, eval_date=eval_date,
            signal=Signal.HOLD, confidence=0.0, error=error,
        )


# ═══════════════════════════════════════════════════════
#  KronosPrediction
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class KronosPrediction:
    """
    Kronos 时序预测结果。

    与 TAAnalysis 并行，提供基于时间序列的概率预测。
    """
    ticker: str
    eval_date: str
    horizon: int                                  # 预测周期（交易日）
    direction: Direction                          # UP / DOWN / FLAT
    expected_return: float                        # 预期收益率（%）
    predicted_close: float                        # 预测最终价
    distribution: PredictionDistribution          # 完整概率分布

    # 元数据
    model_name: str = ""
    sample_count_used: int = 1
    elapsed_sec: float = 0.0
    error: Optional[str] = None

    @property
    def p10(self) -> float | None:
        return self.distribution.p10

    @property
    def p25(self) -> float | None:
        return self.distribution.p25

    @property
    def p50(self) -> float | None:
        return self.distribution.p50

    @property
    def p75(self) -> float | None:
        return self.distribution.p75

    @property
    def p90(self) -> float | None:
        return self.distribution.p90

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "horizon": self.horizon,
            "direction": self.direction.value,
            "expected_return": self.expected_return,
            "predicted_close": self.predicted_close,
            "distribution": self.distribution.to_dict(),
            "model_name": self.model_name,
            "sample_count_used": self.sample_count_used,
            "elapsed_sec": self.elapsed_sec,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KronosPrediction":
        dist_data = data.get("distribution", {})
        dist = PredictionDistribution.from_dict(dist_data) if dist_data else PredictionDistribution()
        direction_val = data.get("direction", "FLAT")
        direction = Direction.UP if direction_val == "UP" else (
            Direction.DOWN if direction_val == "DOWN" else Direction.FLAT
        )
        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            horizon=int(data.get("horizon", 30)),
            direction=direction,
            expected_return=float(data.get("expected_return", 0.0)),
            predicted_close=float(data.get("predicted_close", 0.0)),
            distribution=dist,
            model_name=data.get("model_name", ""),
            sample_count_used=int(data.get("sample_count_used", 1)),
            elapsed_sec=float(data.get("elapsed_sec", 0.0)),
            error=data.get("error"),
        )

    @classmethod
    def failed(cls, ticker: str, eval_date: str, horizon: int, error: str) -> "KronosPrediction":
        return cls(
            ticker=ticker, eval_date=eval_date, horizon=horizon,
            direction=Direction.FLAT, expected_return=0.0,
            predicted_close=0.0, distribution=PredictionDistribution.empty(),
            error=error,
        )


# 向后兼容别名
PredictionUncertainty = PredictionDistribution
