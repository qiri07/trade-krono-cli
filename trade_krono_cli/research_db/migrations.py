"""研究数据库 schema 迁移 — 向后兼容的动态列添加。"""

from __future__ import annotations

import sqlite3

from loguru import logger

from trade_krono_cli.research_db.schema import RESEARCH_TABLES, validate_table_name

# 版本追踪列（jobs 表）
VERSION_COLS: tuple[str, ...] = (
    "run_id",
    "data_version",
    "model_versions",
    "prompt_version",
    "strategy_version",
    "config_hash",
    "external_repos",
)


def migrate_schema(conn: sqlite3.Connection) -> None:
    """向后兼容：为已有表动态添加新版本列。
    不破坏任何现有数据。
    """
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    existing_cols = {row[1] for row in info}

    # 批量迁移旧版本缺失的列（run_id 不存在时全部加）
    if "run_id" not in existing_cols:
        for col in VERSION_COLS:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
                logger.debug(f"📐 Schema 迁移: jobs.{col}")
            except sqlite3.OperationalError:
                pass  # 列已存在

    # 增量迁移：逐个检查新版本列是否存在
    for col in ("external_repos",):
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
                logger.debug(f"📐 Schema 迁移: jobs.{col}")
            except sqlite3.OperationalError:
                pass

    # 迁移 backtest_results.scoring_strategy 列
    bt_cols = {row[1] for row in conn.execute("PRAGMA table_info(backtest_results)").fetchall()}
    if "scoring_strategy" not in bt_cols:
        try:
            conn.execute("ALTER TABLE backtest_results ADD COLUMN scoring_strategy TEXT")
            logger.debug("📐 Schema 迁移: backtest_results.scoring_strategy")
        except sqlite3.OperationalError:
            pass

    # 迁移 strategy_runs.config_hash 列
    sr_cols = {row[1] for row in conn.execute("PRAGMA table_info(strategy_runs)").fetchall()}
    if "config_hash" not in sr_cols:
        try:
            conn.execute("ALTER TABLE strategy_runs ADD COLUMN config_hash TEXT")
            logger.debug("📐 Schema 迁移: strategy_runs.config_hash")
        except sqlite3.OperationalError:
            pass

    # 迁移 signals 表新增领域字段
    sig_cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    for col in ("signal_assessment_json", "expected_value", "conflict"):
        if col not in sig_cols:
            try:
                conn.execute(f"ALTER TABLE signals ADD COLUMN {col} TEXT")
                logger.debug(f"📐 Schema 迁移: signals.{col}")
            except sqlite3.OperationalError:
                pass
    # 迁移：ranking_score 列（若 composite_score 已存在但 ranking_score 缺失）
    if "ranking_score" not in sig_cols and "composite_score" in sig_cols:
        try:
            conn.execute("ALTER TABLE signals ADD COLUMN ranking_score REAL")
            # 将 composite_score 复制到 ranking_score
            conn.execute(
                "UPDATE signals SET ranking_score = composite_score WHERE ranking_score IS NULL",
            )
            logger.debug("📐 Schema 迁移: signals.ranking_score")
        except sqlite3.OperationalError:
            pass

    # 确保其他表存在
    for table in (
        "ta_analysis",
        "kronos_forecast",
        "signals",
        "decisions",
        "raw_reports",
        "backtest_results",
        "strategy_runs",
    ):
        validated = validate_table_name(table, RESEARCH_TABLES)
        try:
            conn.execute(f"SELECT 1 FROM {validated} LIMIT 0")
        except sqlite3.OperationalError:
            pass  # CREATE TABLE IF NOT EXISTS 已在 _init_db 中处理

    conn.commit()
