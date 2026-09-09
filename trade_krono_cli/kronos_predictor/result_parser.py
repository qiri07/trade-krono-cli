"""结果解析模块。

负责：
- 从预测 DataFrame 解析为结构化结果
- 处理多 sample 的统计计算
- 生成 forecast_dict
- 应用缓存结果
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from trade_krono_cli.prediction_distribution import (
    PredictionDistribution,
    build_distribution,
    build_result_dict,
    compute_multi_sample,
)

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings
    from trade_krono_cli.domain.kronos_result import KronosForecastResult


class ResultParser:
    """Kronos 预测结果解析器。"""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def create_empty_result(
        self,
        ticker: str,
        eval_date: str,
        horizon: int,
        model_name: str,
    ) -> "KronosForecastResult":
        """创建空预测结果对象。"""
        from trade_krono_cli.domain.kronos_result import KronosForecastResult

        return KronosForecastResult(
            ticker=ticker,
            eval_date=eval_date,
            horizon=horizon,
            interval="d",
            model_name=model_name,
        )

    def parse_pred_df(
        self,
        pred_df: pd.DataFrame,
        last_close: float,
        sample_count: int = 1,
    ) -> dict:
        """从预测 DataFrame 解析结果。"""
        closes = pred_df["close"].astype(float).values
        if len(closes) == 0:
            msg = "Kronos 返回空预测"
            raise RuntimeError(msg)
        return build_result_dict(closes, last_close, sample_count=sample_count)

    def pred_df_to_dict(self, pred_df: pd.DataFrame) -> dict:
        """将预测 DataFrame 转为 forecast_dict。"""
        idx = pred_df.index
        if not isinstance(idx, pd.DatetimeIndex):
            idx = pd.date_range("today", periods=len(pred_df), freq="B")
        return {
            "timestamps": [t.isoformat() for t in idx],
            "open": [round(float(x), 4) for x in pred_df.get("open", pd.Series(0)).tolist()],
            "high": [round(float(x), 4) for x in pred_df.get("high", pd.Series(0)).tolist()],
            "low": [round(float(x), 4) for x in pred_df.get("low", pd.Series(0)).tolist()],
            "close": [round(float(x), 4) for x in pred_df.get("close", pd.Series(0)).tolist()],
            "volume": [round(float(x), 2) for x in pred_df.get("volume", pd.Series(0)).tolist()],
        }

    def apply_parsed_to_result(
        self,
        res: "KronosForecastResult",
        parsed: dict,
    ) -> None:
        """将解析结果写入 result 对象。"""
        pu_dict = parsed.pop("prediction_uncertainty", None)
        parsed.pop("prediction_distribution", None)
        for k, v in parsed.items():
            setattr(res, k, v)
        if pu_dict:
            res.prediction_uncertainty = PredictionDistribution(**pu_dict)

    def run_predict(
        self,
        runner: Any,
        x_df: pd.DataFrame,
        x_ts: pd.Series,
        y_ts: pd.Series,
        last_close: float,
        res: "KronosForecastResult",
    ) -> None:
        """执行单次预测。"""
        n_samples = max(1, runner.sample_count)  # type: ignore
        adapter = runner._adapter  # type: ignore

        if n_samples > 1:
            pred_df = adapter.predict(
                df=x_df,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=len(y_ts),
                T=runner._settings_obj.kronos_T,  # type: ignore
                top_p=runner._settings_obj.kronos_top_p,  # type: ignore
                sample_count=n_samples,
            )
            close_vals = pred_df["close"].astype(float).values
            if close_vals.ndim == 2:
                avg_close = close_vals.mean(axis=0)
                stacked = close_vals
            else:
                avg_close = close_vals
                stacked = close_vals.reshape(1, -1)
        else:
            pred_df = adapter.predict(
                df=x_df,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=len(y_ts),
                T=runner._settings_obj.kronos_T,  # type: ignore
                top_p=runner._settings_obj.kronos_top_p,  # type: ignore
                sample_count=1,
            )
            avg_close = pred_df["close"].astype(float).values
            stacked = avg_close.reshape(1, -1)

        if n_samples > 1:
            change_pct, direction, vol, path_dispersion, direction_score, conf_score, percentiles = (
                compute_multi_sample(avg_close, stacked, last_close)
            )
            res.predicted_close_mean = round(float(np.mean(avg_close)), 4)
            res.predicted_close_final = round(float(avg_close[-1]), 4)
            res.expected_change_pct = change_pct
            res.direction = direction
            res.volatility_proxy = vol
            res.confidence_band = {
                "low": round(float(np.percentile(avg_close, 25)), 4),
                "high": round(float(np.percentile(avg_close, 75)), 4),
            }
            _pd = build_distribution(
                change_pct=change_pct,
                direction=direction,
                vol=vol,
                path_dispersion=path_dispersion,
                direction_score=direction_score,
                confidence_score=conf_score,
                sample_count=n_samples,
                percentiles=percentiles,
            )
            res.prediction_uncertainty = _pd
        else:
            parsed = self.parse_pred_df(
                pd.DataFrame({"close": avg_close}),
                last_close,
                sample_count=1,
            )
            res.last_close = last_close
            self.apply_parsed_to_result(res, parsed)

        # 生成 forecast_dict
        y_ts_len = len(y_ts) if hasattr(y_ts, "__len__") else 0
        if y_ts_len == len(avg_close):
            pred_idx = y_ts.reset_index(drop=True)
        else:
            pred_idx = pd.date_range("today", periods=len(avg_close), freq="B")
        pred_df_out = pd.DataFrame({"close": avg_close}, index=pred_idx)
        res.forecast_dict = self.pred_df_to_dict(pred_df_out)

    def apply_cached(
        self,
        res: "KronosForecastResult",
        cached: dict,
    ) -> "KronosForecastResult":
        """应用缓存结果。"""
        for k, v in cached.items():
            setattr(res, k, v)
        if isinstance(res.prediction_uncertainty, dict):
            res.prediction_uncertainty = PredictionDistribution.from_dict(
                res.prediction_uncertainty,
            )
        res.elapsed_sec = 0.0
        return res

    def parse_batch_result(
        self,
        res: "KronosForecastResult",
        pred_df: pd.DataFrame,
        last_close: float,
        sample_count: int,
    ) -> "KronosForecastResult":
        """解析批量预测中的一条结果。"""

        closes = pred_df["close"].astype(float).values
        parsed = self.parse_pred_df(pred_df, last_close, sample_count)
        res = self.create_empty_result(
            ticker=res.ticker,
            eval_date=res.eval_date,
            horizon=res.horizon,
            model_name=res.model_name or "kronos",
        )
        res.last_close = last_close
        self.apply_parsed_to_result(res, parsed)

        # 生成 forecast_dict
        pred_idx = pd.date_range("today", periods=len(closes), freq="B")
        pred_df_out = pd.DataFrame({"close": closes}, index=pred_idx)
        res.forecast_dict = self.pred_df_to_dict(pred_df_out)

        return res
