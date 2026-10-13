#!/usr/bin/env python3
"""scripts 目录共享工具函数。

提供数据库访问、股票列表读取、Provider 链构建、数据新鲜度检查等通用功能，
避免各脚本重复定义相同逻辑。

用法：
  from scripts._utils import (
      get_all_tickers, get_provider_chain, CACHE_DB, check_data_freshness,
      get_cache_db_path, ticker_to_thscode,
  )
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DB = Path("outputs/cache/pipeline_cache.db")


def get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）。"""
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        return Path(settings.cache_dir) / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


def ticker_to_thscode(ticker: str) -> str:
    """将 sh.600887 / sz.000001 转换为同花顺 thscode 格式 (600887.SH / 000001.SZ)。"""
    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    if ticker.startswith("sh."):
        return f"{code}.SH"
    elif ticker.startswith("sz."):
        return f"{code}.SZ"
    return code


def normalize_ticker_key(ticker: str) -> str:
    """统一 ticker 键格式：纯数字 → 带交易所前缀。

    metadata.json 中可能同时存在 "sh.600519" 和 "600519" 两种键格式，
    此函数将所有格式归一化为标准前缀格式，避免键不匹配导致数据丢失。

    Args:
        ticker: 任意格式的 ticker，如 "sh.600519"、"sz.000001"、"600519"、"bj.920001"。

    Returns:
        标准格式 ticker，如 "sh.600519"、"sz.000001"、"bj.920001"。
    """
    if "." in ticker:
        return ticker
    code = ticker.strip()
    if not code.isdigit():
        return ticker
    # 纯数字：根据代码范围推断交易所前缀，保持原始字符串以保留前导零
    # A股代码范围：000xxx~002xxx(深主板) 003xxx~399xxx(深创业板) 600xxx~688xxx(沪主板/科创) 8xxxxx(北交所/新三板) 9xxxxx(北交所)
    num = int(code)
    if num >= 920000:
        return f"bj.{code}"
    elif num >= 830000:
        return f"bj.{code.zfill(6)}"
    elif num >= 600000:
        return f"sh.{code}"
    elif num >= 300000:
        return f"sz.{code}"
    elif num >= 1:
        # 000xxx / 001xxx / 002xxx 深市主板，始终带前导零
        return f"sz.{code.zfill(6)}"
    return ticker


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


def check_data_freshness(logger_fn: Callable[[str], None] | None = None) -> str:
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
