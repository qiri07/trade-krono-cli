#!/usr/bin/env python3
"""scripts 目录共享工具函数。

提供数据库访问、股票列表读取、Provider 链构建、数据新鲜度检查等通用功能，
避免各脚本重复定义相同逻辑。

用法：
  from scripts._utils import get_all_tickers, get_provider_chain, CACHE_DB, check_data_freshness
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DB = Path("outputs/cache/pipeline_cache.db")


def get_all_tickers() -> list[str]:
    """从 SQLite 缓存数据库读取所有不重复的 ticker 列表。

    Returns:
        按字母序排序的 ticker 列表。
    """
    conn = sqlite3.connect(str(CACHE_DB))
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()]
    conn.close()
    return sorted(tickers)


def get_provider_chain(ticker: str) -> list[str]:
    """根据股票类型返回 Provider 优先级链。

    Args:
        ticker: 股票代码，支持 sh.600519 / sz.000001 / bj.920001 格式。

    Returns:
        Provider 名称列表，按优先级降序排列。
    """
    if ticker.startswith("bj."):
        return ["tonghuashun", "baostock"]
    elif ticker.startswith("sh.") or ticker.startswith("sz."):
        return ["tonghuashun", "baostock", "mootdx"]
    return ["baostock", "tonghuashun"]


def _get_expected_date() -> str:
    """计算期望的缓存日期（最近交易日，优先昨天，周末回退到周五）。"""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    if today.weekday() >= 5:  # 5=周六, 6=周日
        days_back = today.weekday() - 4  # 周六→1, 周日→2
        yesterday = today - timedelta(days=days_back)
    return yesterday.strftime("%Y-%m-%d")


def _get_cache_latest_date() -> str | None:
    """获取缓存中最新交易日期。"""
    if not CACHE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(CACHE_DB))
        latest = conn.execute("SELECT MAX(end) FROM kline_cache").fetchone()[0]
        conn.close()
        return latest
    except Exception:
        return None


def _trigger_sync() -> bool:
    """触发增量同步（tonghuashun 主，多源 fallback），返回是否成功。"""
    project_root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["uv", "run", "python", "scripts/check_and_sync_cache.py"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode == 0:
            return True
        print(f"⚠️  增量同步返回码 {result.returncode}：{result.stderr.strip()[:200]}", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print("⚠️  增量同步超时（>30min）", flush=True)
        return False
    except FileNotFoundError:
        print("⚠️  无法找到 uv，跳过自动同步", flush=True)
        return False


def check_data_freshness(logger_fn=None) -> str:
    """检查 K 线缓存数据是否已更新至预期日期，未达标则自动触发增量同步。

    Parameters
    ----------
    logger_fn : callable | None
        日志函数，接受 (msg: str) -> None；为 None 时使用 print

    Returns
    -------
    str
        状态描述，如 "✅ 缓存已更新至 2026-09-24"、"⚠️ 缓存较旧，触发增量同步中..." 等
    """
    log = logger_fn or print
    expected = _get_expected_date()
    latest = _get_cache_latest_date()

    if latest is None:
        log("⚠️  缓存为空，触发增量同步...")
        if _trigger_sync():
            latest = _get_cache_latest_date() or "未知"
            return f"✅ 同步完成，最新日期: {latest}"
        return "❌ 同步失败，继续执行但数据可能不完整"

    if latest >= expected:
        return f"✅ 缓存已更新至 {latest}"

    log(f"⚠️  缓存较旧（最新 {latest}，期望 {expected}），触发增量同步...")
    if _trigger_sync():
        latest = _get_cache_latest_date() or latest
        return f"✅ 同步完成，最新日期: {latest}"

    log("⚠️  同步失败，继续执行但数据可能不完整")
    return f"⚠️  缓存较旧（最新 {latest}，期望 {expected}），已尝试同步但失败"
