"""
评估报告生成与持久化。

负责：
  • 将 EvaluationSummary 写入 research DB
  • 从 DB 读取最新评估结果
  • 打印格式化的控制台报告
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from typing import Optional

from trade_krono_cli.eval_data import EvaluationSummary, HorizonMetrics
from loguru import logger


def store_summary(
    summary: EvaluationSummary,
    db_path: str,
    eval_date_range: Optional[str] = None,
) -> None:
    """将评估结果存储到 research database。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_at         REAL NOT NULL,
                eval_date_range TEXT,
                n_records       INTEGER NOT NULL,
                kronos_acc_5d   REAL,
                kronos_acc_10d  REAL,
                kronos_acc_20d  REAL,
                ta_buy_wr_5d    REAL,
                ta_buy_wr_10d   REAL,
                ta_buy_wr_20d   REAL,
                combined_wr_5d  REAL,
                combined_wr_10d REAL,
                combined_wr_20d REAL,
                high_conf_wr_5d REAL,
                high_conf_wr_10d REAL,
                summary_json    TEXT NOT NULL
            )
        """)
        summary_json = json.dumps({
            "kronos_n": summary.kronos_n,
            "kronos_dir_accuracy": {
                str(h): m.kronos_dir_accuracy
                for h, m in summary.horizons.items()
            },
            "ta_buy_win_rate": {
                str(h): m.ta_buy_win_rate
                for h, m in summary.horizons.items()
            },
            "ta_buy_avg_return": {
                str(h): m.ta_buy_avg_return
                for h, m in summary.horizons.items()
            },
            "combined_buy_up_win_rate": {
                str(h): m.combined_buy_up_win_rate
                for h, m in summary.horizons.items()
            },
            "combined_buy_up_avg_return": {
                str(h): m.combined_buy_up_avg_return
                for h, m in summary.horizons.items()
            },
            "high_conf_win_rate": {
                str(h): m.high_conf_win_rate
                for h, m in summary.horizons.items()
            },
            "high_conf_avg_return": {
                str(h): m.high_conf_avg_return
                for h, m in summary.horizons.items()
            },
        }, ensure_ascii=False)

        conn.execute(
            """INSERT INTO evaluation_results
               (eval_at, eval_date_range, n_records,
                kronos_acc_5d, kronos_acc_10d, kronos_acc_20d,
                ta_buy_wr_5d, ta_buy_wr_10d, ta_buy_wr_20d,
                combined_wr_5d, combined_wr_10d, combined_wr_20d,
                high_conf_wr_5d, high_conf_wr_10d, summary_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(), eval_date_range,
                len(summary.records),
                summary.horizons.get(5, HorizonMetrics()).kronos_dir_accuracy,
                summary.horizons.get(10, HorizonMetrics()).kronos_dir_accuracy,
                summary.horizons.get(20, HorizonMetrics()).kronos_dir_accuracy,
                summary.horizons.get(5, HorizonMetrics()).ta_buy_win_rate,
                summary.horizons.get(10, HorizonMetrics()).ta_buy_win_rate,
                summary.horizons.get(20, HorizonMetrics()).ta_buy_win_rate,
                summary.horizons.get(5, HorizonMetrics()).combined_buy_up_win_rate,
                summary.horizons.get(10, HorizonMetrics()).combined_buy_up_win_rate,
                summary.horizons.get(20, HorizonMetrics()).combined_buy_up_win_rate,
                summary.horizons.get(5, HorizonMetrics()).high_conf_win_rate,
                summary.horizons.get(10, HorizonMetrics()).high_conf_win_rate,
                summary_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_evaluation(db_path: str) -> Optional[dict]:
    """获取最新的评估结果。"""
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT id, eval_at, eval_date_range, n_records, summary_json "
                "FROM evaluation_results ORDER BY eval_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return {
        "id": row[0],
        "eval_at": row[1],
        "eval_date_range": row[2],
        "n_records": row[3],
        "summary": json.loads(row[4]),
    }


def print_report(summary: EvaluationSummary, horizons: list[int] = (5, 10, 20)) -> None:
    """打印评估报告到控制台。"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("  📊 预测评估报告")
    logger.info("=" * 60)
    logger.info("")

    _print_kronos_section(summary, horizons)
    _print_ta_section(summary, horizons)
    _print_combined_section(summary, horizons)
    _print_high_conf_section(summary, horizons)
    _print_constraints_section(summary)
    _print_baseline_section()


def _print_kronos_section(summary: EvaluationSummary, horizons: list[int]) -> None:
    logger.info("┌─ Kronos 方向准确率 ─────────────────────────────────┐")
    logger.info(f"│  样本数: {summary.kronos_n}                              │")
    for h in horizons:
        m = summary.horizons.get(h)
        acc = m.kronos_dir_accuracy if m else 0.0
        marker = "✅" if acc > 55 else "⚠️" if acc > 50 else "❌"
        logger.info(f"│  {marker} {h}D 准确率: {acc:5.1f}%                       │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def _print_ta_section(summary: EvaluationSummary, horizons: list[int]) -> None:
    logger.info("┌─ TA BUY 信号表现 ───────────────────────────────────┐")
    logger.info(f"│  样本数: {summary.ta_buy_n}                              │")
    for h in horizons:
        m = summary.horizons.get(h)
        wr = m.ta_buy_win_rate if m else 0.0
        avg_ret = m.ta_buy_avg_return if m else 0.0
        marker = "✅" if wr > 55 else "⚠️" if wr > 50 else "❌"
        logger.info(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
              f"平均收益: {avg_ret:+.2f}%                    │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def _print_combined_section(summary: EvaluationSummary, horizons: list[int]) -> None:
    logger.info("┌─ 综合信号（TA BUY + Kronos UP）─────────────────────┐")
    logger.info(f"│  样本数: {summary.combined_buy_up_n}                          │")
    for h in horizons:
        m = summary.horizons.get(h)
        wr = m.combined_buy_up_win_rate if m else 0.0
        avg_ret = m.combined_buy_up_avg_return if m else 0.0
        marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
        logger.info(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
              f"平均收益: {avg_ret:+.2f}%                    │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def _print_high_conf_section(summary: EvaluationSummary, horizons: list[int]) -> None:
    logger.info("┌─ 高置信信号（综合分 ≥ 70）──────────────────────────┐")
    logger.info(f"│  样本数: {summary.high_conf_n}                              │")
    for h in horizons:
        m = summary.horizons.get(h)
        wr = m.high_conf_win_rate if m else 0.0
        avg_ret = m.high_conf_avg_return if m else 0.0
        marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
        logger.info(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
              f"平均收益: {avg_ret:+.2f}%                    │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")


def _print_constraints_section(summary: EvaluationSummary) -> None:
    if summary.entry_limit_up_blocked or summary.exit_limit_down_blocked or summary.cost_applied_n:
        logger.info("┌─ 交易约束统计 ──────────────────────────────────────┐")
        logger.info(f"│  交易成本已扣减: {summary.cost_applied_n} 条记录                 │")
        if summary.entry_limit_up_blocked:
            logger.info(f"│  🚫 买入日涨停拦截: {summary.entry_limit_up_blocked} 条                  │")
        if summary.exit_limit_down_blocked:
            logger.info(f"│  🚫 退出日跌停拦截: {summary.exit_limit_down_blocked} 条                  │")
        logger.info("└" + "─" * 58 + "┘")
        logger.info("")


def _print_baseline_section() -> None:
    logger.info("┌─ 基准对比（50% 随机基准）───────────────────────────┐")
    logger.info("│  方向准确率 > 50% = 超越随机                          │")
    logger.info("│  胜率 > 50% = 正向 alpha                              │")
    logger.info("└" + "─" * 58 + "┘")
    logger.info("")
