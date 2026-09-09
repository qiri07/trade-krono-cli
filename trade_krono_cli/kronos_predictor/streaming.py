"""流式预测模块。

用于 StreamPipeline，跳过缓存检查和 fetch_lookback，
直接使用预取的 K 线 DataFrame 进行预测。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger

from trade_krono_cli.kronos_predictor.result_parser import ResultParser
from trade_krono_cli.security import validate_date, validate_ticker

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings
    from trade_krono_cli.domain.kronos_result import KronosForecastResult


class StreamingPredictor:
    """流式预测器（用于 StreamPipeline）。"""

    def __init__(self, settings: "Settings", runner: Any) -> None:
        self._settings = settings
        self._runner = runner
        self._result_parser = ResultParser(settings)

    def predict(
        self,
        ticker: str,
        eval_date: str,
        df: pd.DataFrame,
    ) -> "KronosForecastResult":
        """流式预测：直接使用预取的 K 线 DataFrame。"""
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)

        res = self._result_parser.create_empty_result(
            ticker=ticker,
            eval_date=eval_date,
            horizon=self._settings.kronos_pred_len,
            model_name=getattr(self._runner, "model_name", "kronos"),
        )

        t0 = time.time()
        try:
            self._runner._load()  # type: ignore
            x_df, x_ts, y_ts, last_close = self._prepare_stream(df, ticker, eval_date)
            self._result_parser.run_predict(self._runner, x_df, x_ts, y_ts, last_close, res)
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            logger.error(f"❌ Kronos 流式预测失败 {ticker}: {res.error}")
        finally:
            res.elapsed_sec = round(time.time() - t0, 2)

        return res

    def _prepare_stream(
        self,
        df: pd.DataFrame,
        ticker: str,
        eval_date: str,
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """从预取 DataFrame 直接构造预测数据。"""
        lookback = len(df)
        x_df = df.iloc[-lookback:][
            ["open", "high", "low", "close", "volume", "amount"]
        ].reset_index(drop=True)
        x_ts = df.iloc[-lookback:]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        from trade_krono_cli.data import next_business_days

        pred_len = self._settings.kronos_pred_len
        future = next_business_days(eval_date, pred_len)[:pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close
