"""缓存层基础模块 — 数据库初始化、事务管理、查询工具。"""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from trade_krono_cli.config import Settings, get_settings

if TYPE_CHECKING:
    from pathlib import Path

# Whitelist of allowed cache table names
CACHE_TABLES: frozenset[str] = frozenset({"kline_cache", "ta_cache", "kronos_cache"})

# K 线 TTL 常量
KLINE_HISTORICAL_TTL = 0.0  # 永久（所有历史数据只追加不失效）
KLINE_RECENT_TTL = KLINE_HISTORICAL_TTL  # 已统一为永久，保留符号以便旧代码引用
KLINE_HISTORY_WINDOW_DAYS = 1  # 保留，实际已不用于 TTL 判定


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    if table not in allowed:
        msg = f"Unauthorized table: {table}"
        raise ValueError(msg)
    return table


class Cache:
    """SQLite 缓存，支持 K 线、TA 结果、Kronos 预测三种类型。

    缓存语义：
      · K 线：全量永久缓存，当日数据不设置 TTL
      · TA / Kronos：缓存 key 含 config_hash + 模型版本，配置变更自动失效
    """

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None) -> None:
        self._db_path = db_path or ((settings or get_settings()).cache_dir / "pipeline_cache.db")
        from trade_krono_cli.config import _validate_test_isolation

        _validate_test_isolation(self._db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程本地存储：每个线程持有自己的 SQLite 连接
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（线程安全）。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _transaction(self, fn: Callable[..., None]) -> None:
        """以事务方式执行操作。"""
        conn = self._conn
        with conn:
            fn(conn)

    def _query_one(self, sql: str, params: tuple = ()) -> tuple | None:
        """执行查询并返回单行结果。"""
        return self._conn.execute(sql, params).fetchone()

    def _query_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        """执行查询并返回所有结果。"""
        return self._conn.execute(sql, params).fetchall()

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
        self._transaction(lambda conn: self._run_migrations(conn))

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """执行缓存表结构迁移（向后兼容）。"""
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

    # ── 向后兼容：直接调用方法（委托给子模块）──────────────────────────────────

    def get_kline(self, ticker: str, start: str, end: str, freq: str, adjustflag: str = "1") -> Any:  # noqa: ANN401
        from trade_krono_cli.cache.kline import KlineCache

        return KlineCache(self).get_kline(ticker, start, end, freq, adjustflag)

    def set_kline(
        self,
        ticker: str,
        start: str,
        end: str,
        freq: str,
        df: Any,
        ttl: float = 86400,
        adjustflag: str = "1",
    ) -> None:  # noqa: ANN401
        from trade_krono_cli.cache.kline import KlineCache

        KlineCache(self).set_kline(ticker, start, end, freq, df, ttl, adjustflag)

    def warm_history(self, ticker: str, end_date: str, lookback_days: int = 730) -> tuple[int, int]:
        from trade_krono_cli.cache.kline import KlineCache

        return KlineCache(self).warm_history(ticker, end_date, lookback_days)

    def get_ta(
        self,
        ticker: str,
        date: str,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
    ) -> dict | None:
        from trade_krono_cli.cache.ta import TADataCache

        return TADataCache(self).get_ta(ticker, date, config_hash, prompt_ver, model_ver)

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
        from trade_krono_cli.cache.ta import TADataCache

        TADataCache(self).set_ta(ticker, date, result, config_hash, prompt_ver, model_ver, ttl)

    def get_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> dict | None:
        from trade_krono_cli.cache.kronos import KronosCache

        return KronosCache(self).get_kronos(
            ticker, date, pred_len, sample_count, config_hash, model_ver
        )

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
        from trade_krono_cli.cache.kronos import KronosCache

        KronosCache(self).set_kronos(
            ticker, date, pred_len, result, ttl, sample_count, config_hash, model_ver
        )

    def clear_all(self) -> int:
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).clear_all()

    def export_daily_pv(
        self, parquet_path: str, h5_path: str | None = None, debug_insts: int = 0
    ) -> dict:
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).export_daily_pv(parquet_path, h5_path, debug_insts)

    def stats(self) -> dict:
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).stats()

    def get_cached_date_range(
        self, ticker: str, freq: str = "d", adjustflag: str = "1"
    ) -> tuple[str, str] | None:
        from trade_krono_cli.cache.kline import KlineCache

        return KlineCache(self).get_cached_date_range(ticker, freq, adjustflag)
