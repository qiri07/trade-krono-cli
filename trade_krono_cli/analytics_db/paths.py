"""analytics_db.paths — Parquet 文件路径构造。

管理 Parquet 数据文件的目录结构，按 ticker/date 组织特征和预测文件。
"""

from __future__ import annotations

from pathlib import Path


class ParquetPaths:
    """管理 Parquet 数据文件的目录结构。"""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.features_dir = data_root / "features"
        self.predictions_dir = data_root / "predictions"
        self.backtest_dir = data_root / "backtest"
        for d in (self.features_dir, self.predictions_dir, self.backtest_dir):
            d.mkdir(parents=True, exist_ok=True)

    def feature_path(self, ticker: str, date: str) -> Path:
        """data/features/{year}/{month}/{ticker}_{date}.parquet."""
        safe = ticker.replace(".", "_")
        from datetime import datetime

        dt = datetime.strptime(date, "%Y-%m-%d")
        p = self.features_dir / str(dt.year) / f"{dt.month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{safe}_{date}.parquet"

    def prediction_path(self, ticker: str, date: str, pred_len: int) -> Path:
        """data/predictions/{year}/{month}/{ticker}_{date}_{predlen}.parquet."""
        safe = ticker.replace(".", "_")
        from datetime import datetime

        dt = datetime.strptime(date, "%Y-%m-%d")
        p = self.predictions_dir / str(dt.year) / f"{dt.month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{safe}_{date}_{pred_len}.parquet"

    def backtest_path(self, job_id: str) -> Path:
        """data/backtest/{job_id}.parquet."""
        p = self.backtest_dir
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{job_id}.parquet"
