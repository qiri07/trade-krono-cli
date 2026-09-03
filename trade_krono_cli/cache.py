"""缓存层 — TTL 驱动的 SQLite 缓存。

支持三种缓存类型：
  kline_cache  — K 线数据（pd.DataFrame pickle）
  ta_cache     — TA 分析结果（JSON dict）
  kronos_cache — Kronos 预测结果（JSON dict）

缓存语义：
  · K 线：全量永久缓存，当日数据不设置 TTL
  · TA / Kronos：缓存 key 含 config_hash + 模型版本，配置变更自动失效
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from io import BytesIO
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from trade_krono_cli.config import Settings, get_settings

if TYPE_CHECKING:
    from pathlib import Path

# Whitelist of allowed cache table names
_CACHE_TABLES: frozenset[str] = frozenset({"kline_cache", "ta_cache", "kronos_cache"})

# K 线 TTL 常量
_KLINE_HISTORICAL_TTL = 0.0  # 永久（所有历史数据只追加不失效）
_KLINE_RECENT_TTL = _KLINE_HISTORICAL_TTL  # 已统一为永久，保留符号以便旧代码引用
_KLINE_HISTORY_WINDOW_DAYS = 1  # 保留，实际已不用于 TTL 判定


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    if table not in allowed:
        msg = f"Unauthorized table: {table}"
        raise ValueError(msg)
    return table


class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。"""

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None) -> None:
        self._db_path = db_path or ((settings or get_settings()).cache_dir / "pipeline_cache.db")
        from trade_krono_cli.config import _validate_test_isolation

        _validate_test_isolation(self._db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kline_cache (
                    ticker    TEXT NOT NULL,
                    start     TEXT NOT NULL,
                    end       TEXT NOT NULL,
                    freq      TEXT NOT NULL,
                    ttl       REAL NOT NULL,
                    data      BLOB NOT NULL,
                    created   REAL NOT NULL,
                    PRIMARY KEY (ticker, start, end, freq)
                );

                CREATE TABLE IF NOT EXISTS ta_cache (
                    ticker       TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    config_hash  TEXT NOT NULL DEFAULT '',
                    prompt_ver   TEXT NOT NULL DEFAULT '',
                    model_ver    TEXT NOT NULL DEFAULT '',
                    ttl          REAL NOT NULL,
                    data         BLOB NOT NULL,
                    created      REAL NOT NULL,
                    PRIMARY KEY (ticker, date, config_hash, prompt_ver, model_ver)
                );

                CREATE TABLE IF NOT EXISTS kronos_cache (
                    ticker       TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    pred_len     INTEGER NOT NULL,
                    sample_cnt   INTEGER NOT NULL DEFAULT 1,
                    config_hash  TEXT NOT NULL DEFAULT '',
                    model_ver    TEXT NOT NULL DEFAULT '',
                    ttl          REAL NOT NULL,
                    data         BLOB NOT NULL,
                    created      REAL NOT NULL,
                    PRIMARY KEY (ticker, date, pred_len, sample_cnt, config_hash, model_ver)
                );
            """)
            # 迁移：为旧表添加新列
            for col_sql in [
                "ALTER TABLE ta_cache ADD COLUMN config_hash  TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ta_cache ADD COLUMN prompt_ver   TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ta_cache ADD COLUMN model_ver    TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE kronos_cache ADD COLUMN config_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE kronos_cache ADD COLUMN model_ver   TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE kline_cache ADD COLUMN adjustflag  TEXT NOT NULL DEFAULT '1'",
            ]:
                try:
                    conn.execute(col_sql)
                    logger.debug("📦 缓存表迁移: 新增列")
                except sqlite3.OperationalError:
                    pass

    # ── K 线缓存 ──────────────────────────────────────

    def get_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        adjustflag: str = "1",
    ) -> pd.DataFrame | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kline_cache "
                "WHERE ticker=? AND start=? AND end=? AND freq=? AND adjustflag=?",
                (ticker, start, end, freq, adjustflag),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        try:
            return pd.read_pickle(BytesIO(data))
        except (ModuleNotFoundError, AttributeError, TypeError):
            # pyarrow 未安装或旧版 pandas pickle 兼容回退
            import pickle

            return pickle.loads(data)

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: pd.DataFrame,
        ttl: float = 86400,
        adjustflag: str = "1",
    ) -> None:
        buf = BytesIO()
        df.to_pickle(buf)
        buf.seek(0)
        with self._conn as conn:
            if ttl == _KLINE_HISTORICAL_TTL:
                # 永久缓存：先删除被新段完全覆盖的旧段，再插入新段
                conn.execute(
                    "DELETE FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=? AND end <= ? AND start >= ?",
                    (ticker, freq, adjustflag, end, start),
                )
                conn.execute(
                    "INSERT INTO kline_cache "
                    "(ticker, start, end, freq, adjustflag, ttl, data, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, start, end, freq, adjustflag, ttl, buf.read(), time.time()),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO kline_cache "
                    "(ticker, start, end, freq, adjustflag, ttl, data, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, start, end, freq, adjustflag, ttl, buf.read(), time.time()),
                )
            conn.commit()

    def warm_history(self, ticker: str, end_date: str, lookback_days: int = 730) -> tuple[int, int]:
        """预热 K 线缓存：拉取历史数据，全部以永久缓存写入。"""
        from datetime import datetime, timedelta

        from trade_krono_cli.data import fetch_kline

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=lookback_days)
        start_s = start.strftime("%Y-%m-%d")

        logger.info(f"🔥 预热 K 线缓存: {ticker} {start_s}~{end_date}（全部永久缓存）")
        df = fetch_kline(ticker, start_s, end_date, frequency="d", adjustflag="1", use_cache=True)
        if df is None or len(df) == 0:
            return 0, 0

        fetched = len(df)
        seg_start = df["timestamps"].iloc[0].strftime("%Y-%m-%d")
        seg_end = df["timestamps"].iloc[-1].strftime("%Y-%m-%d")
        self.set_kline(ticker, seg_start, seg_end, "d", df, ttl=_KLINE_HISTORICAL_TTL)
        logger.debug(f"  📦 永久缓存: {ticker} {seg_start}~{seg_end}")

        logger.info(f"✅ K 线缓存预热完成: {ticker} {fetched}行 → 1段（永久）")
        return fetched, 1

    # ── TA 缓存 ───────────────────────────────────────

    def get_ta(
        self,
        ticker: str,
        date: str,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
    ) -> dict | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM ta_cache "
                "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
                (ticker, date, config_hash, prompt_ver, model_ver),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_ta(
        self,
        ticker: str,
        date: str,
        result: dict,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
        ttl: float = 86400,
    ) -> None:
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    config_hash,
                    prompt_ver,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
            conn.commit()

    # ── Kronos 缓存 ───────────────────────────────────

    def get_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> dict | None:
        with self._conn as conn:
            row = conn.execute(
                "SELECT data, created, ttl FROM kronos_cache "
                "WHERE ticker=? AND date=? AND pred_len=? AND sample_cnt=? "
                "AND config_hash=? AND model_ver=?",
                (ticker, date, pred_len, sample_count, config_hash, model_ver),
            ).fetchone()
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        result: dict,
        ttl: float = 86400,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> None:
        with self._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, sample_cnt, config_hash, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    pred_len,
                    sample_count,
                    config_hash,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
            conn.commit()

    # ── 工具方法 ──────────────────────────────────────

    def get_cached_date_range(
        self,
        ticker: str,
        freq: str = "d",
        adjustflag: str = "1",
    ) -> tuple[str, str] | None:
        """查询某只股票的已有 K 线缓存覆盖的日期范围。

        该Ticker 的所有缓存条目会被合并成一个连续的 [start, end] 区间。
        若没有缓存或缓存已过期，返回 None。

        Parameters
        ----------
        ticker : 股票代码
        freq : 频率（"d"/"w"/"m"）
        adjustflag : 复权方式（"1"=前复权），与 get_kline/set_kline 保持一致

        Returns
        -------
        (start_date, end_date) 或 None

        """
        with self._conn as conn:
            rows = conn.execute(
                "SELECT start, end, created, ttl FROM kline_cache WHERE ticker=? AND freq=? AND adjustflag=?",
                (ticker, freq, adjustflag),
            ).fetchall()

        if not rows:
            return None

        # 过滤掉已 TTL 过期的条目
        now = time.time()
        valid: list[tuple[str, str]] = []
        for start_s, end_s, created, ttl in rows:
            if ttl >= 0 and (ttl > 0 and now - created > ttl):
                continue
            valid.append((start_s, end_s))

        if not valid:
            return None

        # 合并连续区间：对不重叠区间做归并，避免空洞区间被误判为覆盖
        valid_sorted = sorted(valid, key=lambda r: r[0])
        merged: list[tuple[str, str]] = [valid_sorted[0]]
        for s, e in valid_sorted[1:]:
            if s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        # 返回整体覆盖范围（用于快速判断）
        start_s = merged[0][0]
        end_s = merged[-1][1]
        return (start_s, end_s)

    def clear_all(self) -> int:
        """清空所有缓存表（kline_cache / ta_cache / kronos_cache），返回删除的行数。"""
        with self._conn as conn:
            count = 0
            for table in _CACHE_TABLES:
                r = conn.execute(
                    f"DELETE FROM {_validate_table_name(table, _CACHE_TABLES)}",
                ).rowcount
                count += r
            conn.commit()
        logger.info(f"🧹 清除缓存 {count} 条（research 数据不受影响）")
        return count

    # ── RD-Agent 兼容导出 ─────────────────────────────────

    def export_daily_pv(
        self,
        parquet_path: str,
        h5_path: str | None = None,
        debug_insts: int = 0,
    ) -> dict:
        """将 kline_cache 全量导出为 RD-Agent daily_pv 格式（parquet + 可选 h5）。

        Parameters
        ----------
        parquet_path : str
            输出 parquet 文件路径（必填）。
        h5_path : str, optional
            输出 HDF5 文件路径。若提供则额外生成 h5。
        debug_insts : int, optional
            若 > 0，额外生成 debug parquet（前 N 只股票）。

        Returns
        -------
        dict
            {"rows": int, "stocks": int, "date_range": (str, str), ...}
        """
        from pathlib import Path

        # ── 读取所有 kline_cache 条目 ──────────────────────────────────────
        rows_raw: list[pd.DataFrame] = []
        with self._conn as conn:
            cursor = conn.execute("SELECT ticker, data FROM kline_cache")
            total = conn.execute("SELECT COUNT(*) FROM kline_cache").fetchone()[0]

        for i, (ticker, blob) in enumerate(cursor, 1):
            df = pd.read_pickle(BytesIO(blob))
            df["instrument"] = ticker.replace(".", "").upper()
            rows_raw.append(df)
            if i % 1000 == 0:
                logger.info(f"  读取缓存 {i}/{total} 只...")

        combined = pd.concat(rows_raw, ignore_index=True)
        logger.info(f"导出原始: {len(combined):,} 行, {combined['instrument'].nunique()} 只")

        # ── 转换为 RD-Agent 格式 ──────────────────────────────────────────
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
            "date_min": date_min,
            "date_max": date_max,
            "parquet_path": parquet_path,
        }

        # ── 可选：生成 h5 ─────────────────────────────────────────────────
        if h5_path:
            Path(h5_path).parent.mkdir(parents=True, exist_ok=True)
            try:
                import subprocess

                _base = Path(__file__).resolve().parents[2]
                env_py = _base / "RD-Agent-Work" / "rdagent-env" / "bin" / "python"
                if not env_py.exists():
                    env_py = _base / "rdagent-env" / "bin" / "python"
                if env_py.exists():
                    r = subprocess.run(
                        [
                            str(env_py),
                            "-c",
                            f"import pandas as pd; "
                            f"df=pd.read_parquet('{parquet_path}'); "
                            f"df.to_hdf('{h5_path}', key='data', mode='w')",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    if r.returncode == 0:
                        h5_size = Path(h5_path).stat().st_size / 1024 / 1024
                        logger.info(f"✅ h5 已写入: {h5_path} ({h5_size:.1f} MB)")
                        result["h5_path"] = h5_path
                        result["h5_size_mb"] = round(h5_size, 1)
                    else:
                        logger.warning(f"h5 生成失败: {r.stderr.strip()}")
                else:
                    logger.warning("未找到 rdagent-env/python，跳过 h5 生成")
            except Exception as e:
                logger.warning(f"h5 生成异常: {e}")

        # ── 可选：debug 数据集（前 N 只股票）───────────────────────────────
        if debug_insts > 0:
            debug_dir = Path(parquet_path).parent.parent / (
                Path(parquet_path).parent.name + "_debug"
            )
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
            debug_h5_path = debug_dir / "daily_pv.h5"
            try:
                import subprocess

                _base = Path(__file__).resolve().parents[2]
                env_py = _base / "RD-Agent-Work" / "rdagent-env" / "bin" / "python"
                if not env_py.exists():
                    env_py = _base / "rdagent-env" / "bin" / "python"
                if env_py.exists():
                    r = subprocess.run(
                        [
                            str(env_py),
                            "-c",
                            f"import pandas as pd; "
                            f"df=pd.read_parquet('{debug_path}'); "
                            f"df.to_hdf('{debug_h5_path}', key='data', mode='w')",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
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
        with self._conn as conn:
            return {
                f"cache_{t}": conn.execute(
                    f"SELECT COUNT(*) FROM {_validate_table_name(t, _CACHE_TABLES)}",
                ).fetchone()[0]
                for t in _CACHE_TABLES
            }


_cache: Cache | None = None
_cache_lock = threading.Lock()


def get_cache() -> Cache:
    """获取全局 Cache 单例，首次调用时自动初始化。"""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = Cache()
    return _cache


def clear_cache_singleton() -> None:
    """清除 Cache 全局单例（测试隔离用）。"""
    global _cache
    _cache = None
