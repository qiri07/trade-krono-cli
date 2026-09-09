"""数据准备模块。

负责：
- 拉取历史K线数据
- 构造训练样本 (x_df, x_ts, y_ts)
- DataFrame padding 到统一长度
- 批量分割
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from trade_krono_cli.data import fetch_lookback, next_business_days

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings


class DataPreparator:
    """Kronos 数据准备器。"""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def prepare(
        self, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """准备单只股票的预测数据。

        Returns
        -------
        (x_df, x_ts, y_ts, last_close)
        """
        adjustflag = "1"  # 前复权

        # 拉取历史数据
        df = fetch_lookback(
            ticker,
            eval_date,
            lookback=self._settings.kronos_lookback,
            frequency="d",
            adjustflag=adjustflag,
        )

        if len(df) < self._settings.kronos_lookback:
            msg = f"数据不足: {ticker} 仅 {len(df)} 行 < {self._settings.kronos_lookback}"
            raise RuntimeError(msg)

        # 构造 x_df, x_ts
        x_df = df.iloc[-self._settings.kronos_lookback :][
            ["open", "high", "low", "close", "volume", "amount"]
        ].reset_index(drop=True)
        x_ts = df.iloc[-self._settings.kronos_lookback :]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        # 构造 y_ts（从 eval_date 起算）
        future = next_business_days(eval_date, self._settings.kronos_pred_len)
        future = future[: self._settings.kronos_pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close

    @staticmethod
    def pad_df_to_length(df: pd.DataFrame, target_len: int) -> pd.DataFrame:
        """将 DataFrame 填充到目标长度（前面补最后一行）。"""
        if len(df) >= target_len:
            return df.iloc[-target_len:]
        pad_rows = pd.concat([df.iloc[[-1]] * (target_len - len(df)), df], ignore_index=True)
        return pad_rows.tail(target_len)

    @staticmethod
    def split_batches(items: list[tuple], batch_size: int) -> list[list[tuple]]:
        """将 items 列表分割为多个批次。"""
        return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

    @staticmethod
    def prepare_from_df(
        df: pd.DataFrame,
        ticker: str,
        eval_date: str,
        settings: Any,
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """从预取 DataFrame 直接构造预测数据（流式模式）。"""
        from trade_krono_cli.data import next_business_days

        lookback = min(len(df), settings.kronos_lookback)
        x_df = df.iloc[-lookback:][
            ["open", "high", "low", "close", "volume", "amount"]
        ].reset_index(drop=True)
        x_ts = df.iloc[-lookback:]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        future = next_business_days(eval_date, settings.kronos_pred_len)
        future = future[: settings.kronos_pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close
