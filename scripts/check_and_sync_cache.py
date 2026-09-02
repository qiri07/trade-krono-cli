#!/usr/bin/env python3
"""
每日数据缓存检查与同步脚本。

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
    except Exception as e:
        print(f"❌ 读取缓存失败: {e}", file=sys.stderr)
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


def run_sync(source: str = "mootdx", dry_run: bool = False) -> bool:
    """执行缓存同步。"""
    cmd = [
        sys.executable, "-m", "uv", "run",
        "trade-krono-cli", "sync-universe",
        "--source", source,
        "--no-progress",
    ]
    print(f"🔄 执行数据同步: {' '.join(cmd)}")
    if dry_run:
        print("  [DRY-RUN] 跳过实际执行")
        return True
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=7200)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("❌ 同步超时（>2小时）")
        return False
    except Exception as e:
        print(f"❌ 同步失败: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据缓存检查与同步")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行同步")
    parser.add_argument("--source", default="mootdx", choices=["mootdx", "akshare", "tonghuashun"],
                        help="数据源（默认 mootdx）")
    args = parser.parse_args()

    print("=" * 60)
    print("📊 A 股数据缓存检查")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 获取期望日期
    expected_date = get_expected_date()
    print(f"期望缓存日期: {expected_date}")

    # 检查缓存状态
    latest_date = get_cache_latest_date()
    print(f"当前缓存最新: {latest_date or '无数据'}")

    if latest_date and latest_date >= expected_date:
        print("✅ 缓存已更新，无需同步")
        return 0

    # 需要同步
    print("⚠️  缓存需要更新，准备执行同步...")
    print(f"   目标: {expected_date}")
    print(f"   数据源: {args.source}")

    if args.dry_run:
        print("[DRY-RUN] 跳过实际同步")
        return 0

    # 执行同步
    success = run_sync(source=args.source, dry_run=False)
    if success:
        print("✅ 同步完成")
    else:
        print("❌ 同步失败")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
