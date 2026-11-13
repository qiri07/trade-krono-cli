"""研究数据库 base — ResearchDatabase 核心基础设施（连接、建表、迁移）。"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from typing import TYPE_CHECKING

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.research_db.migrations import migrate_schema
from trade_krono_cli.research_db.schema import CREATE_SCRIPT

if TYPE_CHECKING:
    from pathlib import Path


class ResearchDatabase:
    """投研数据持久化层。

    表结构见模块文档。
    """

    # 版本追踪列（jobs 表）— 供 migrations 使用
    _VERSION_COLS: tuple[str, ...] = (
        "run_id",
        "data_version",
        "model_versions",
        "prompt_version",
        "strategy_version",
        "config_hash",
        "external_repos",
    )

    # 类级别锁：所有实例共用，保护各自实例的连接集合
    _conn_lock = threading.Lock()

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None) -> None:
        # 优先使用显式传入的 db_path，其次使用 settings.research_db_path，最后回退到 pipeline_cache.db
        if db_path is not None:
            self._db_path = db_path
        elif settings is not None:
            self._db_path = settings.research_db_path
        else:
            self._db_path = get_settings().research_db_path
        from trade_krono_cli.config import _validate_test_isolation

        _validate_test_isolation(self._db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程本地存储必须在 _init_db 之前初始化，因为 _init_db 会访问 _conn
        self._local = threading.local()
        # 实例级连接登记：close() 只关闭本实例的连接，避免误关其他 ResearchDatabase 实例
        self._all_conns: set[sqlite3.Connection] = set()
        self._init_db()
        self._migrate_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（线程安全）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error:
                conn = None
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            # schema 声明的 FOREIGN KEY 只有显式开启才会被 SQLite 强制，
            # 否则孤立子表数据（signal 指向不存在的 job）可被静默写入。
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with ResearchDatabase._conn_lock:
                self._all_conns.add(conn)
        return conn

    def close(self) -> None:
        """关闭本实例所有线程的 SQLite 连接（测试清理用）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            self._local.conn = None
        with ResearchDatabase._conn_lock:
            for c in list(self._all_conns):
                try:
                    c.execute("SELECT 1")
                except sqlite3.Error:
                    # 连接已失效，跳过
                    continue
                with contextlib.suppress(sqlite3.Error):
                    c.close()
            self._all_conns.clear()

    def _init_db(self) -> None:
        with self._conn as conn:
            conn.executescript(CREATE_SCRIPT)

    def _migrate_schema(self) -> None:
        """向后兼容：为已有表动态添加新版本列。"""
        with self._conn as conn:
            migrate_schema(conn)
