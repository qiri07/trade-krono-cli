"""
研究数据库 base — ResearchDatabase 核心基础设施（连接、建表、迁移）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.research_db.migrations import migrate_schema
from trade_krono_cli.research_db.schema import CREATE_SCRIPT


class ResearchDatabase:
    """
    投研数据持久化层。

    表结构见模块文档。
    """

    # 版本追踪列（jobs 表）— 供 migrations 使用
    _VERSION_COLS: tuple[str, ...] = (
        "run_id", "data_version", "model_versions",
        "prompt_version", "strategy_version", "config_hash",
        "external_repos",
    )

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None):
        self._db_path = db_path or (
            (settings or get_settings()).cache_dir / "pipeline_cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        """持久连接（懒初始化），避免每次读写新建连接。"""
        conn = sqlite3.connect(self._db_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """创建所有表（幂等，IF NOT EXISTS）。"""
        with self._conn as conn:
            conn.executescript(CREATE_SCRIPT)

    def _migrate_schema(self) -> None:
        """向后兼容：为已有表动态添加新版本列。"""
        with self._conn as conn:
            migrate_schema(conn)
