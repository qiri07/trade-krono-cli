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

# ── 共享常量 ────────────────────────────────────────────────────────────────
# ST / 异常股名称关键词（各脚本统一使用，避免多处重复定义）
# 全市场扫描脚本用完整列表（含 N/C 新股标识），白名单分析用精简版（仅 ST 类）
ST_NAME_KEYWORDS: tuple[str, ...] = ("ST", "*ST", "退市", "N", "C")
ST_NAME_KEYWORDS_MINIMAL: tuple[str, ...] = ("ST", "*ST", "退市")

# 北交所股票代码前缀（6 位裸代码以 920 开头，或带交易所前缀 bj.）
# 注意：用 "920" 而非 "92"，避免误杀 92 开头的其他代码段（如 920xxx 北交所新股）
BSE_CODE_PREFIXES: tuple[str, ...] = ("920",)
BSE_TICKER_PREFIXES: tuple[str, ...] = ("bj.",)


def is_bse_code(code: str) -> bool:
    """判断 6 位裸代码是否为北交所股票（920 开头）。"""
    return code.startswith(BSE_CODE_PREFIXES)


def is_bse_or_hk(ticker: str) -> bool:
    """判断带前缀 ticker 或 6 位裸代码是否为需排除的北交所/港股。

    兼容 ``bj.``/``hk.`` 前缀与 6 位裸代码（920 开头北交所）两种形式。
    """
    if ticker.upper().startswith(("BJ.", "HK.")):
        return True
    bare = ticker.split(".")[-1]
    return is_bse_code(bare)

# ── 共享股票名称映射 ────────────────────────────────────────────────────────
# 白名单常用股票的 6 位代码 → 名称静态映射（A 股 + 港股）。
# daily_analysis._get_stock_name 会动态补充未命中的名称（baostock 查询缓存）。
# daily_ta_push.py 等只读场景直接引用此常量，避免各自维护副本。
STOCK_NAMES: dict[str, str] = {
    "000001": "平安银行",
    "000050": "中国联通",
    "000538": "云南白药",
    "000651": "格力电器",
    "000858": "五粮液",
    "002027": "分众传媒",
    "002120": "韵达股份",
    "002461": "珠江啤酒",
    "003369": "秦港股份",
    "00386": "中国石油化工股份",
    "00390": "中国中铁",
    "01448": "福寿园",
    "01658": "邮储银行",
    "01919": "中远海控",
    "00700": "腾讯控股",
    "00941": "中国移动",
    "09988": "阿里巴巴-W",
    "09992": "泡泡玛特",
    "600007": "中国国贸",
    "600011": "华能国际",
    "600028": "中国石化",
    "600036": "招商银行",
    "600210": "紫江企业",
    "600519": "贵州茅台",
    "600887": "伊利股份",
    "600900": "长江电力",
    "600938": "中国海油",
    "600941": "中国移动",
    "601088": "中国神华",
    "601166": "兴业银行",
    "601225": "陕西煤业",
    "601326": "秦港股份",
    "601668": "中国建筑",
    "601899": "紫金矿业",
    "601919": "中远海控",
    "603993": "洛阳钼业",
}


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
    if code[:2] in ("51", "58") or code[:3] in ("159", "156", "158"):
        # ETF 代码：51xxxx/588xxx(沪)、159xxx/156xxx(深) → "etf.<code>"
        return f"etf.{code}"
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
        ticker: 股票代码，支持 sh.600519 / sz.000001 / bj.920001 / hk.00700 格式。

    Returns:
        Provider 名称列表，按优先级降序排列。
    """
    if ticker.startswith("bj."):
        return ["tonghuashun", "baostock"]
    elif ticker.startswith("hk."):
        return ["akshare_hk", "tonghuashun", "mootdx", "akshare"]
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
