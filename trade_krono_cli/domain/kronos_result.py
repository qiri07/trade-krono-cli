"""KronosForecastResult — 单只股票的 Kronos 预测结果数据类。

迁移自 kronos_runner.py（原行 81-164）。

领域层数据对象：纯数据，无外部依赖。
业务逻辑（模型调度、缓存、批量推理）保留在 kronos_runner.KronosRunner。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_krono_cli.prediction_distribution import PredictionDistribution


class KronosForecastResult:
    """单只股票的 Kronos 预测结果。

    Attributes
    ----------
    ticker                股票代码（sh.600519 格式）
    eval_date             评估日期（YYYY-MM-DD）
    horizon               预测周期（交易日）
    interval              数据频率，默认 "d"
    last_close            最后收盘价
    predicted_close_mean  均值预测收盘价
    predicted_close_final 最终预测收盘价（同 predicted_close_mean）
    expected_change_pct   预期收益率（%）
    direction             方向（"UP"/"DOWN"/"FLAT"）
    volatility_proxy      波动率代理
    confidence_band       置信区间 {"low": ..., "high": ...}
    forecast_dict         完整预测序列（含 timestamps/open/high/low/volume）
    model_name            模型名称
    error                 错误信息（预测失败时填充）
    elapsed_sec           预测耗时（秒）
    prediction_uncertainty 预测不确定性分布

    """

    __slots__ = (
        "confidence_band",
        "direction",
        "elapsed_sec",
        "error",
        "eval_date",
        "expected_change_pct",
        "forecast_dict",
        "horizon",
        "interval",
        "last_close",
        "model_name",
        "predicted_close_final",
        "predicted_close_mean",
        "prediction_uncertainty",
        "ticker",
        "volatility_proxy",
    )

    def __init__(
        self,
        ticker: str,
        eval_date: str,
        horizon: int,
        interval: str = "d",
        last_close: float | None = None,
        predicted_close_mean: float | None = None,
        predicted_close_final: float | None = None,
        expected_change_pct: float | None = None,
        direction: str | None = None,
        volatility_proxy: float | None = None,
        confidence_band: dict | None = None,
        forecast_dict: dict | None = None,
        model_name: str | None = None,
        error: str | None = None,
        elapsed_sec: float = 0.0,
        prediction_uncertainty: PredictionDistribution | None = None,
    ) -> None:
        self.ticker = ticker
        self.eval_date = eval_date
        self.horizon = horizon
        self.interval = interval
        self.last_close = last_close
        self.predicted_close_mean = predicted_close_mean
        self.predicted_close_final = predicted_close_final
        self.expected_change_pct = expected_change_pct
        self.direction = direction
        self.volatility_proxy = volatility_proxy
        self.confidence_band = confidence_band
        self.forecast_dict = forecast_dict
        self.model_name = model_name
        self.error = error
        self.elapsed_sec = elapsed_sec
        self.prediction_uncertainty = prediction_uncertainty

    def to_dict(self) -> dict:
        """序列化为 dict（用于缓存写入和 JSON 落盘）。"""
        return {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "horizon": self.horizon,
            "interval": self.interval,
            "last_close": self.last_close,
            "predicted_close_mean": self.predicted_close_mean,
            "predicted_close_final": self.predicted_close_final,
            "expected_change_pct": self.expected_change_pct,
            "direction": self.direction,
            "volatility_proxy": self.volatility_proxy,
            "confidence_band": self.confidence_band,
            "forecast_dict": self.forecast_dict,
            "model_name": self.model_name,
            "error": self.error,
            "elapsed_sec": self.elapsed_sec,
            "prediction_uncertainty": (
                self.prediction_uncertainty.to_dict()
                if self.prediction_uncertainty is not None
                else None
            ),
        }
