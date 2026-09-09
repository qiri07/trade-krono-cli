"""缓存查询与分析操作 — stats / export_daily_pv / clear_all / get_cached_date_range。"""

from __future__ import annotations

import sqlite3
import subprocess
from io import BytesIO
from pathlib import Path

import pandas as pd
from loguru import logger

from trade_krono_cli.cache.base import CACHE_TABLES, Cache, _validate_table_name


class CacheQueries:
    """缓存统计与导出操作集。"""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def clear_all(self) -> int:
        """清空所有缓存表（kline_cache / ta_cache / kronos_cache），返回删除的行数。"""
        count = 0

        def _clear(conn: sqlite3.Connection) -> None:
            nonlocal count
            for table in CACHE_TABLES:
                r = conn.execute(
                    f"DELETE FROM {_validate_table_name(table, CACHE_TABLES)}",
                ).rowcount
                count += r

        self._cache._transaction(_clear)
        logger.info(f"🧹 清除缓存 {count} 条（research 数据不受影响）")
        return count

    def export_daily_pv(
        self,
        parquet_path: str,
        h5_path: str | None = None,
        debug_insts: int = 0,
    ) -> dict:
        """将 kline_cache 全量导出为 RD-Agent daily_pv 格式（parquet + 可选 h5）。

        Returns
        -------
        dict
            {"rows": int, "stocks": int, "date_range": (str, str), ...}
        """
        rows_raw: list[pd.DataFrame] = []
        rows = self._cache._query_all("SELECT ticker, data FROM kline_cache")
        total = self._cache._query_one("SELECT COUNT(*) FROM kline_cache")[0]  # type: ignore[index]

        for i, (ticker, blob) in enumerate(rows, 1):
            df = pd.read_pickle(BytesIO(blob))
            df["instrument"] = ticker.replace(".", "").upper()
            rows_raw.append(df)
            if i % 1000 == 0:
                logger.info(f"  读取缓存 {i}/{total} 只...")

        combined = pd.concat(rows_raw, ignore_index=True)
        logger.info(f"导出原始: {len(combined):,} 行, {combined['instrument'].nunique()} 只")

        # 转换为 RD-Agent 格式
        df = combined.copy()
        df["date"] = pd.to_datetime(df["timestamps"]).dt.normalize()
        df = df.rename(
            columns={
                "open": "$open",
                "high": "$high",
                "low": "$low",
                "close": "$close",
                "volume": "$volume",
            }
        )
        df["$factor"] = 1.0
        df = df.dropna(subset=["$open", "$close", "$volume"])
        df = df[df["$high"] > 0]
        df = df.set_index(["date", "instrument"]).sort_index()
        df.index.names = ["date", "instrument"]
        df = df[["$open", "$close", "$high", "$low", "$volume", "$factor"]]

        before = len(df)
        df = df[~df.index.duplicated(keep="first")]
        if len(df) < before:
            logger.warning(
                f"导出去重: {before:,} → {len(df):,} 行（移除 {before - len(df):,} 重复行）"
            )

        stocks = int(df.index.get_level_values("instrument").nunique())
        date_min = df.index.get_level_values("date").min().strftime("%Y-%m-%d")
        date_max = df.index.get_level_values("date").max().strftime("%Y-%m-%d")

        Path(parquet_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet_path, engine="pyarrow")
        logger.info(
            f"✅ parquet 已写入: {parquet_path} ({Path(parquet_path).stat().st_size / 1024 / 1024:.1f} MB)"
        )

        result: dict = {
            "rows": len(df),
            "stocks": stocks,
            "date_range": (date_min, date_max),
            "parquet_path": parquet_path,
        }

        if h5_path is not None:
            df.to_hdf(h5_path, key="data", mode="w")
            result["h5_path"] = h5_path
            logger.info(
                f"✅ h5 已写入: {h5_path} ({Path(h5_path).stat().st_size / 1024 / 1024:.1f} MB)"
            )

        if debug_insts > 0:
            debug_dir = Path(parquet_path).parent / "debug"
            debug_path = debug_dir / "daily_pv.parquet"
            debug_h5_path = debug_dir / "daily_pv.h5"
            debug_dir.mkdir(parents=True, exist_ok=True)
            insts = df.index.get_level_values("instrument").unique()[:debug_insts]
            debug_df = df.loc[pd.IndexSlice[:, insts], :]
            debug_df.to_parquet(str(debug_path), engine="pyarrow")
            result["debug_path"] = str(debug_path)
            result["debug_rows"] = len(debug_df)
            result["debug_stocks"] = len(insts)
            logger.info(
                f"✅ debug parquet: {debug_path} "
                f"({debug_df.index.get_level_values('instrument').nunique()} 只, "
                f"{debug_df.index.get_level_values('date').min()} ~ "
                f"{debug_df.index.get_level_values('date').max()})"
            )
            # 同时生成 debug h5
            try:
                import os

                _base = Path(__file__).resolve().parents[2]
                env_py = _base / "RD-Agent-Work" / "rdagent-env" / "bin" / "python"
                if not env_py.exists():
                    env_py = _base / "rdagent-env" / "bin" / "python"
                if env_py.exists():
                    r = subprocess.run(
                        [
                            str(env_py),
                            "-c",
                            "import os, pandas as pd; "
                            "df=pd.read_parquet(os.environ['PARQUET']); "
                            "df.to_hdf(os.environ['H5'], key='data', mode='w')",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env={**os.environ, "PARQUET": str(debug_path), "H5": str(debug_h5_path)},
                    )
                    if r.returncode == 0:
                        result["debug_h5_path"] = str(debug_h5_path)
                        logger.info(f"✅ debug h5: {debug_h5_path}")
                    else:
                        logger.warning(f"debug h5 生成失败: {r.stderr.strip()}")
                else:
                    logger.warning("未找到 rdagent-env/python，跳过 debug h5 生成")
            except Exception as e:
                logger.warning(f"debug h5 生成异常: {e}")

        return result

    def stats(self) -> dict:
        """返回各缓存表的记录数，格式为 {"cache_kline_cache": N, ...}。"""
        return {
            f"cache_{t}": self._cache._query_one(
                f"SELECT COUNT(*) FROM {_validate_table_name(t, CACHE_TABLES)}",
            )[0]  # type: ignore[index]
            for t in CACHE_TABLES
        }
