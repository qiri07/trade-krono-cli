"""缓存层基础模块 — 数据库初始化、事务管理、查询工具。"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Callable
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

    # 类级别锁：所有实例共用，保护各自实例的连接集合
    _conn_lock = threading.Lock()

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None) -> None:
        self._db_path = db_path or ((settings or get_settings()).cache_dir / "pipeline_cache.db")
        from trade_krono_cli.config import _validate_test_isolation

        _validate_test_isolation(self._db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程本地存储：每个线程持有自己的 SQLite 连接
        self._local = threading.local()
        # 实例级连接登记：close() 只关闭本实例创建的连接，避免误关其他 Cache 实例
        self._all_conns: set[sqlite3.Connection] = set()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（线程安全）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            # close() 可能由其他线程/清理钩子关闭本线程连接，失效时重连
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error:
                conn = None
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
            with Cache._conn_lock:
                self._all_conns.add(conn)
        return conn

    def close(self) -> None:
        """关闭本实例所有线程的 SQLite 连接（测试清理用）。"""
        # 先关当前线程的连接
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            self._local.conn = None
        # 再关其他线程遗留的连接
        with Cache._conn_lock:
            for c in list(self._all_conns):
                try:
                    c.execute("SELECT 1")
                except sqlite3.Error:
                    # 连接已失效，跳过
                    continue
                with contextlib.suppress(sqlite3.Error):
                    c.close()
            self._all_conns.clear()

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

                -- 性能索引：加速按 ticker 的查询
                CREATE INDEX IF NOT EXISTS idx_kline_cache_ticker ON kline_cache(ticker);
                CREATE INDEX IF NOT EXISTS idx_ta_cache_ticker ON ta_cache(ticker);
                CREATE INDEX IF NOT EXISTS idx_kronos_cache_ticker ON kronos_cache(ticker);
            """)
            # 迁移：为旧表添加新列和索引
        self._transaction(lambda conn: self._run_migrations(conn))

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """执行缓存表结构迁移（向后兼容）。"""
        migrations = [
            # 新增列
            "ALTER TABLE ta_cache ADD COLUMN config_hash  TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE ta_cache ADD COLUMN prompt_ver   TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE ta_cache ADD COLUMN model_ver    TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE ta_cache ADD COLUMN version      INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ta_cache ADD COLUMN trace_id     TEXT",
            "ALTER TABLE ta_cache ADD COLUMN input_hash   TEXT",
            "ALTER TABLE kronos_cache ADD COLUMN config_hash TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE kronos_cache ADD COLUMN model_ver   TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE kline_cache ADD COLUMN adjustflag  TEXT NOT NULL DEFAULT '1'",
            "ALTER TABLE kline_cache ADD COLUMN data_hash   TEXT",
            "ALTER TABLE kline_cache ADD COLUMN source_provider TEXT",
            "ALTER TABLE kline_cache ADD COLUMN source_version TEXT",
            # 新增索引（IF NOT EXISTS 避免重复创建错误）
            "CREATE INDEX IF NOT EXISTS idx_kline_cache_ticker ON kline_cache(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_ta_cache_ticker ON ta_cache(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_kronos_cache_ticker ON kronos_cache(ticker)",
        ]
        for migration in migrations:
            try:
                conn.execute(migration)
                logger.debug("📦 缓存表迁移: 执行成功")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                # 只忽略"已存在"类错误；数据库锁/IO 错误必须暴露，否则后续写入会因缺列失败
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                raise

        # ── 将 adjustflag 加入 kline_cache 主键（旧表 PK 缺 adjustflag）────────
        # 旧表 PK 为 (ticker, start, end, freq)，缺少 adjustflag，
        # 导致同一区间不同复权方式（qfq/hfq/不复权）的数据写入时 PK 冲突，
        # 只能告警跳过，造成部分复权数据缺失。
        self._rebuild_kline_cache_pk_with_adjustflag(conn)

    def _rebuild_kline_cache_pk_with_adjustflag(self, conn: sqlite3.Connection) -> None:
        """重建 kline_cache 表，将 adjustflag 加入主键。

        旧表 PK 为 (ticker, start, end, freq)，缺少 adjustflag。
        重建后 PK 为 (ticker, start, end, freq, adjustflag)，
        同一区间不同复权方式的数据可共存，不再 PK 冲突。
        旧数据的 adjustflag 默认 '1'（前复权，与历史一致）。
        整个过程在调用方的事务中执行（由 _run_migrations 的 _transaction 管理）。
        """
        try:
            info = conn.execute("PRAGMA table_info(kline_cache)").fetchall()
        except sqlite3.OperationalError:
            return  # 表不存在，无需处理
        if not info:
            return
        col_names = [row[1] for row in info]
        pk_cols = [row[1] for row in info if row[5] > 0]  # pk > 0 表示在主键中
        if "adjustflag" not in col_names:
            return  # 列不存在，无需处理（ALTER 尚未执行或表为新建）
        if "adjustflag" in pk_cols:
            return  # adjustflag 已在 PK 中，无需重建

        logger.info("🔄 重建 kline_cache 表：将 adjustflag 加入主键（旧表 PK 缺 adjustflag）")
        try:
            # 创建新表，adjustflag 加入 PK
            conn.execute("""
                CREATE TABLE kline_cache_new (
                    ticker        TEXT NOT NULL,
                    start         TEXT NOT NULL,
                    end           TEXT NOT NULL,
                    freq          TEXT NOT NULL,
                    adjustflag    TEXT NOT NULL DEFAULT '1',
                    ttl           REAL NOT NULL,
                    data          BLOB NOT NULL,
                    created       REAL NOT NULL,
                    data_hash     TEXT,
                    source_provider TEXT,
                    source_version TEXT,
                    PRIMARY KEY (ticker, start, end, freq, adjustflag)
                )
            """)
            # 复制旧数据，adjustflag 默认 '1'
            conn.execute("""
                INSERT INTO kline_cache_new
                (ticker, start, end, freq, adjustflag, ttl, data, created,
                 data_hash, source_provider, source_version)
                SELECT ticker, start, end, freq, '1', ttl, data, created,
                       data_hash, source_provider, source_version
                FROM kline_cache
            """)
            conn.execute("DROP TABLE kline_cache")
            conn.execute("ALTER TABLE kline_cache_new RENAME TO kline_cache")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kline_cache_ticker ON kline_cache(ticker)"
            )
            logger.info("✅ kline_cache 表重建完成：adjustflag 已加入主键")
        except sqlite3.Error as e:
            logger.error(f"❌ kline_cache 表重建失败: {e}")
            raise

    # ── 向后兼容：直接调用方法（委托给子模块）──────────────────────────────────

    def get_kline(self, ticker: str, start: str, end: str, freq: str, adjustflag: str = "1") -> Any:  # noqa: ANN401
        """从缓存中获取指定股票的历史 K 线数据。"""
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
        """将 K 线 DataFrame 写入缓存。"""
        from trade_krono_cli.cache.kline import KlineCache

        KlineCache(self).set_kline(ticker, start, end, freq, df, ttl, adjustflag)

    def warm_history(self, ticker: str, end_date: str, lookback_days: int = 730) -> tuple[int, int]:
        """预加载指定股票的历史 K 线数据（用于冷启动预热）。"""
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
        """从缓存中获取指定股票的 TA 分析结果。"""
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
        """将 TA 分析结果写入缓存。"""
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
        """从缓存中获取指定股票的 Kronos 预测结果。"""
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
        """将 Kronos 预测结果写入缓存。"""
        from trade_krono_cli.cache.kronos import KronosCache

        KronosCache(self).set_kronos(
            ticker, date, pred_len, result, ttl, sample_count, config_hash, model_ver
        )

    def clear_all(self) -> int:
        """清除所有缓存数据，返回清除的记录总数。"""
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).clear_all()

    def export_daily_pv(
        self, parquet_path: str, h5_path: str | None = None, debug_insts: int = 0
    ) -> dict:
        """导出每日 PV（访问量）Parquet + 可选 H5，返回统计字典。"""
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).export_daily_pv(parquet_path, h5_path, debug_insts)

    def stats(self) -> dict:
        """返回缓存统计信息（总记录数、股票数、日期范围等）。"""
        from trade_krono_cli.cache.queries import CacheQueries

        return CacheQueries(self).stats()

    def get_cached_date_range(
        self, ticker: str, freq: str = "d", adjustflag: str = "1"
    ) -> tuple[str, str] | None:
        """查询指定股票在缓存中的最早/最晚日期范围。"""
        from trade_krono_cli.cache.kline import KlineCache

        return KlineCache(self).get_cached_date_range(ticker, freq, adjustflag)
