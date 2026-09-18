"""analytics_db.writer — Parquet 写入器。

将分析结果写入 Parquet 文件，支持特征、预测和回测三种数据。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from trade_krono_cli.analytics_db.paths import ParquetPaths


class ParquetWriter:
    """将分析结果写入 Parquet 文件。"""

    def __init__(self, paths: ParquetPaths) -> None:
        self.paths = paths

    def write_feature(
        self,
        ticker: str,
        date: str,
        data: dict,
    ) -> Path:
        """写入单只股票的 TA 分析特征到 Parquet。"""
        path = self.paths.feature_path(ticker, date)
        df = pd.DataFrame([data])
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 特征 Parquet 已写入: {path}")
        return path

    def write_prediction(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        data: dict,
    ) -> Path:
        """写入单只股票的 Kronos 预测到 Parquet。"""
        path = self.paths.prediction_path(ticker, date, pred_len)
        df = pd.DataFrame([data])
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 预测 Parquet 已写入: {path}")
        return path

    def write_backtest(
        self,
        job_id: str,
        records: list[dict],
    ) -> Path:
        """写入回测结果到 Parquet（支持多记录）。"""
        path = self.paths.backtest_path(job_id)
        if records:
            df = pd.DataFrame(records)
        else:
            df = pd.DataFrame(
                columns=[
                    "ticker",
                    "action",
                    "entry_price",
                    "exit_price",
                    "entry_date",
                    "exit_date",
                    "return_pct",
                    "horizon",
                ],
            )
        df.to_parquet(path, engine="pyarrow", index=False)
        logger.debug(f"📦 回测 Parquet 已写入: {path} ({len(records)} 条)")
        return path
