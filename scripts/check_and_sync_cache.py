#!/usr/bin/env python3
"""每日数据缓存检查与同步脚本。

功能：
  1. 检查 K 线缓存是否已更新至预期日期
  2. 若未更新，自动执行增量同步

触发方式：
  - cron 定时任务（每天 10:00 和 16:00）
  - 手动运行：python scripts/check_and_sync_cache.py

用法：
  python scripts/check_and_sync_cache.py [--dry-run] [--source mootdx]
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DB = PROJECT_ROOT / "outputs" / "cache" / "pipeline_cache.db"


def get_cache_latest_date() -> str | None:
    """获取缓存中最新的交易日。"""
    if not CACHE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(CACHE_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(end) FROM kline_cache")
        result = cursor.fetchone()[0]
        conn.close()
        return result
    except Exception:
        return None


def get_expected_date() -> str:
    """获取期望的缓存日期（最近的交易日，优先昨天，周末则回退到周五）。"""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    # 如果昨天是周末，回退到周五
    if yesterday.weekday() >= 5:  # 5=周六, 6=周日
        yesterday = yesterday - timedelta(days=yesterday.weekday() - 4)
    return yesterday.strftime("%Y-%m-%d")


def is_cache_up_to_date(expected_date: str) -> bool:
    """检查缓存是否已更新至预期日期。"""
    latest = get_cache_latest_date()
    if latest is None:
        return False
    # 缓存日期应该 >= 预期日期
    return latest >= expected_date


def run_sync(source: str = "tonghuashun", dry_run: bool = False) -> bool:
    """执行缓存同步。"""
    # uv 是独立二进制，不能通过 python -m uv 调用
    uv_cmd = subprocess.run(
        ["uv", "--version"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    if uv_cmd.returncode != 0:
        # 尝试完整路径
        uv_paths = [
            Path.home() / ".local" / "bin" / "uv",
            Path("/usr/local/bin/uv"),
            Path("/usr/bin/uv"),
        ]
        for p in uv_paths:
            if p.exists():
                cmd = [str(p), "run", "trade-krono-cli", "sync-universe",
                       "--source", source, "--no-progress"]
                break
        else:
            logger.error("无法找到 uv 二进制")
            return False
    else:
        cmd = ["uv", "run", "trade-krono-cli", "sync-universe",
               "--source", source, "--no-progress"]

    if dry_run:
        logger.info(f"[dry-run] 将执行: {' '.join(cmd)}")
        return True
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=7200)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("同步超时（>2h）")
        return False
    except Exception as e:
        logger.error(f"同步失败: {e}")
        return False


def run_sync_with_fallback(dry_run: bool = False) -> bool:
    """尝试多个数据源进行同步，按优先级依次尝试。"""
    sources = ["tonghuashun", "akshare", "mootdx"]
    for source in sources:
        logger.info(f"尝试数据源: {source}")
        if run_sync(source=source, dry_run=dry_run):
            logger.info(f"✅ 数据源 {source} 同步成功")
            return True
        logger.warning(f"⚠️  数据源 {source} 同步失败，尝试下一个...")
    logger.error("所有数据源同步均失败")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据缓存检查与同步")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行同步")
    parser.add_argument(
        "--source",
        default="tonghuashun",
        choices=["mootdx", "akshare", "tonghuashun"],
        help="数据源（默认 tonghuashun）",
    )
    args = parser.parse_args()


    # 获取期望日期
    expected_date = get_expected_date()

    # 检查缓存状态
    latest_date = get_cache_latest_date()

    if latest_date and latest_date >= expected_date:
        return 0

    # 需要同步

    if args.dry_run:
        return 0

    # 执行同步（带多源 fallback）
    success = run_sync_with_fallback(dry_run=False)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
