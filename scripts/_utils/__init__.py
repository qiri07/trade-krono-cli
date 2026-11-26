#!/usr/bin/env python3
"""scripts 目录共享工具函数（兼容重导出层）。

本模块为兼容层：核心功能已按职责拆分至子模块，此处统一重导出，
保证现有 `from scripts._utils import ...` 不做任何改动即可继续工作。

子模块职责：
  - scripts._utils.constants：共享常量（ST/北交所/ETF 前缀、PE/市值阈值、STOCK_NAMES）
  - scripts._utils.ticker：Ticker 归一化与市场判断
  - scripts._utils.schedule：交易日判断（expected_trading_day）
  - scripts._utils.metadata：metadata.json 元数据索引与剔除原因
  - scripts._utils.universe：统一股票池过滤引擎
  - scripts._utils.provider：Provider 链配置（providers.yaml 解析，保留在本模块）
"""

from __future__ import annotations

import logging
import sqlite3  # noqa: F401  (重导出：测试 patch scripts._utils.sqlite3.connect 需要)
import subprocess
from collections.abc import Callable
from datetime import datetime  # noqa: F401  (重导出：测试 patch scripts._utils.datetime 需要)
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

try:
    import yaml  # noqa: PLC0415

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# ════════════════════════════════════════════════════════
# 从子模块重导出所有公共名称（保持向后兼容）
# ════════════════════════════════════════════════════════

# ── constants ──
from scripts._utils.constants import (  # noqa: E402, F401
    BSE_CODE_PREFIXES,
    BSE_TICKER_PREFIXES,
    ETF_SH_PREFIXES,
    ETF_SZ_PREFIXES,
    FAILED_MIN_RECORDS,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MIN_MARKET_CAP_YI,
    PE_MAX_TECH,
    PE_MAX_VALUE,
    ST_NAME_KEYWORDS,
    ST_NAME_KEYWORDS_MINIMAL,
    ST_NAME_PREFIXES,
    STOCK_NAMES,
)

# ── schedule ──
from scripts._utils.constants import (  # noqa: E402, F401
    MARKET_CLOSE_HOUR as _MCH,
)
from scripts._utils.constants import (  # noqa: E402, F401
    MARKET_CLOSE_MINUTE as _MCM,
)

# ── metadata ──
from scripts._utils.metadata import (  # noqa: E402, F401
    exclusion_reason,
    get_cache_db_path,
    load_metadata_index,
)


def expected_trading_day(now: datetime | None = None) -> str:
    """返回此刻应已落库的最近交易日（YYYY-MM-DD）。

    收盘后（>= 15:00）返回当日；盘中返回上一交易日；周末回退到周五。
    本函数在 ``__init__.py`` 命名空间内直接实现（而非委托 ``schedule.py``），
    使测试 patch ``scripts._utils.datetime`` 能正确生效。

    Args:
        now: 用于测试注入的时间，缺省取 ``datetime.now()``。

    Returns:
        日期字符串，如 ``2026-11-10``。
    """
    from datetime import timedelta

    current = now or datetime.now()
    day = current.date()
    after_close = (current.hour, current.minute) >= (_MCH, _MCM)
    if day.weekday() >= 5:  # 周六/周日 → 回退到周五
        day -= timedelta(days=day.weekday() - 4)
    elif not after_close:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")

# ── ticker ──
from scripts._utils.ticker import (  # noqa: E402, F401
    is_bse_code,
    is_bse_or_hk,
    is_etf_ticker,
    is_st_name,
    market_key,
    normalize_ticker_key,
    strip_ticker_prefix,
    ticker_to_thscode,
)

# ── universe ──
from scripts._utils.universe import (  # noqa: E402, F401
    filter_stock_universe,
    get_all_tickers,
    get_astock_tickers,
    get_sqlite_conn,
)

# ════════════════════════════════════════════════════════
# Provider 链配置（保留在本模块，未单独拆分子模块）
# ════════════════════════════════════════════════════════

# CACHE_DB 通过 get_cache_db_path() 获取，尊重 TRADING_KRONO_CACHE_DIR 环境变量（测试隔离）。
# 模块加载时先尝试读取 settings，失败时回退到默认路径。
def _default_cache_db() -> Path:
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        return Path(get_settings().cache_dir) / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


CACHE_DB: Path = _default_cache_db()

# providers.yaml 路径与回退硬编码链（configs/providers.yaml 缺失/损坏时使用，
# 与历史行为一致，保证向后兼容）
PROVIDERS_CONFIG_FILE = Path("configs/providers.yaml")

_HARDCODED_PROVIDER_CHAINS: dict[str, list[str]] = {
    "bj": ["tonghuashun", "efinance", "baostock"],
    "hk": ["akshare_hk", "tonghuashun", "akshare", "easyquotation"],
    "sh": ["tonghuashun", "efinance", "akshare", "easyquotation", "mootdx", "baostock"],
    "sz": ["tonghuashun", "efinance", "akshare", "easyquotation", "mootdx", "baostock"],
    "default": ["baostock", "tonghuashun", "efinance"],
}

_provider_config_cache: dict | None = None


def set_providers_config_file(path: Path) -> None:
    """设置 providers.yaml 路径（测试用），并强制下次重载。"""
    global PROVIDERS_CONFIG_FILE, _provider_config_cache
    PROVIDERS_CONFIG_FILE = path
    _provider_config_cache = None


def reset_providers_config() -> None:
    """恢复默认 providers.yaml 路径并清空缓存（测试隔离用）。"""
    global PROVIDERS_CONFIG_FILE, _provider_config_cache
    PROVIDERS_CONFIG_FILE = Path("configs/providers.yaml")
    _provider_config_cache = None


def _hardcoded_chain(ticker: str) -> list[str]:
    """按前缀返回回退硬编码 provider 链（configs/providers.yaml 不可用时的兜底）。"""
    prefix = ticker.split(".", 1)[0] if "." in ticker else ""
    if prefix == "bj":
        return list(_HARDCODED_PROVIDER_CHAINS["bj"])
    if prefix == "hk":
        return list(_HARDCODED_PROVIDER_CHAINS["hk"])
    if prefix in ("sh", "sz"):
        # ETF 代码（51/58 沪、159/156/158 深）与普通沪深 A 股 provider 链一致
        return list(_HARDCODED_PROVIDER_CHAINS[prefix])
    return list(_HARDCODED_PROVIDER_CHAINS["default"])


def load_provider_config(force_reload: bool = False) -> dict:
    """读取 configs/providers.yaml，解析失败或缺失时回退到内置硬编码结构。

    返回统一结构：
        {
            "defaults": {"sh": [...], "sz": [...], "bj": [...], "hk": [...]},
            "etf": {"sh": [...], "sz": [...]},
            "override": {"sh.600519": [...], ...},
            "fetch": {"timeout_seconds": int, "workers": int},
        }

    解析结果缓存于模块级 `_provider_config_cache`，`force_reload=True` 时强制重读。
    """
    global _provider_config_cache
    if _provider_config_cache is not None and not force_reload:
        return _provider_config_cache

    fallback = {
        "defaults": {k: list(v) for k, v in _HARDCODED_PROVIDER_CHAINS.items()},
        "etf": {
            "sh": list(_HARDCODED_PROVIDER_CHAINS["sh"]),
            "sz": list(_HARDCODED_PROVIDER_CHAINS["sz"]),
        },
        "override": {},
        "fetch": {"timeout_seconds": 45, "workers": 8},
    }

    if not _YAML_AVAILABLE:
        _provider_config_cache = fallback
        return fallback

    try:
        if not PROVIDERS_CONFIG_FILE.exists():
            _provider_config_cache = fallback
            return fallback
        raw = yaml.safe_load(PROVIDERS_CONFIG_FILE.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            _provider_config_cache = fallback
            return fallback
        cfg: dict = {
            "defaults": {k: list(v) for k, v in (raw.get("defaults") or {}).items()},
            "etf": {k: list(v) for k, v in (raw.get("etf") or {}).items()},
            "override": {k: list(v) for k, v in (raw.get("override") or {}).items()},
            "fetch": dict(raw.get("fetch") or {}),
        }
        # 缺省段补齐：defaults 未定义的交易所前缀回退到硬编码
        for key, chain in _HARDCODED_PROVIDER_CHAINS.items():
            cfg["defaults"].setdefault(key, list(chain))
        for etf_key in ("sh", "sz"):
            cfg["etf"].setdefault(etf_key, list(_HARDCODED_PROVIDER_CHAINS[etf_key]))
        cfg["fetch"].setdefault("timeout_seconds", 45)
        cfg["fetch"].setdefault("workers", 8)
        _provider_config_cache = cfg
        return cfg
    except (ImportError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        # YAML 未安装 / 文件读取失败 / 结构异常：回退硬编码，保证同步脚本可运行
        _provider_config_cache = fallback
        return fallback


def get_provider_chain(ticker: str) -> list[str]:
    """根据 configs/providers.yaml 返回 Provider 优先级链。

    解析顺序：
      1. ``override[ticker]``  —— 单只票精确覆盖
      2. ``etf[ticker_type]``  —— ETF（``is_etf_ticker``）走 etf 段
      3. ``defaults[ticker_type]`` —— 普通票按前缀 sh/sz/bj/hk 路由
      4. 回退到内置硬编码链（``configs/providers.yaml`` 缺失或解析失败时）

    Args:
        ticker: 股票代码，支持 sh.600519 / sz.000001 / bj.920001 / hk.00700 格式。

    Returns:
        Provider 名称列表，按优先级降序排列。

    Notes:
        沪深 A 股与 ETF 的 K 线同步默认 baostock 优先（configs/providers.yaml）。
        baostock 的 adjustflag='1'（前复权）基准稳定，而 tonghuashun 同参数实际
        返回后复权口径（实测 sh.600674 同日 2024-12-31 前复权 284.67 vs 后复权
        16.35，差 17.4 倍）。同步侧若混合两源会向缓存写入两套复权体系，造成
        "伪除权跳变"污染 MA/MACD 等技术指标。tonghuashun 仅作 baostock 失败时
        的降级兜底；降级发生时应在同步后运行 scripts/fix_dirty_kline.py 对混源
        记录做基准对齐清理。
    """
    cfg = load_provider_config()

    # 1. 单只票精确覆盖
    override = cfg.get("override") or {}
    if ticker in override:
        return list(override[ticker])

    prefix = ticker.split(".", 1)[0] if "." in ticker else ""

    # 2. ETF 走 etf 段
    if prefix in ("sh", "sz") and is_etf_ticker(ticker):
        etf_cfg = (cfg.get("etf") or {}).get(prefix)
        if etf_cfg:
            return list(etf_cfg)

    # 3. 普通票按前缀路由到 defaults
    defaults = cfg.get("defaults") or {}
    if prefix in defaults:
        return list(defaults[prefix])

    # 4. 兜底：内置硬编码链（含 unknown 前缀）
    return _hardcoded_chain(ticker)


def _get_expected_date() -> str:
    """计算期望的缓存日期（最近交易日，收盘后 ≥15:00 返回当日，盘中返回上一交易日，周末回退到周五）。

    与 :func:`expected_trading_day` 口径保持一致，避免 check_data_status /
    retry_missing_history / data_backfill / check_data_freshness 与 flow_runner /
    fill_missing 使用两套日期判定逻辑。
    """
    return expected_trading_day()


def check_kline_regime(ticker: str, kline: pd.DataFrame, window: int = 20) -> None:
    """校验 K 线最近 window 日的复权基准一致性。

    不同数据源（tonghuashun 前复权 / baostock 前复权）的复权基准会随拉取时间漂移，
    同一只股票的历史段与增量段可能混入两套复权体系，导致最近某日出现 -50%~-94%
    的非交易性跳变（实际是复权基准切换而非真实除权）。本函数检测最近 window 日
    内相邻收盘的涨跌幅绝对值，超过 A 股单日 ±11% 涨跌停限制（含 ST ±5%、新股 ±44%）
    的合理上界即判定为复权基准漂移。

    真实除权日：除权跳空一般 ≤ 30%（大盘股分红），且伴随 volume/amount 正常，
    但本函数不区分真实除权与基准漂移 —— 两者在纯 K 线视角下都表现为"价格体系切换"，
    都应触发数据重拉（用单一数据源重新对齐），由上层决定重拉策略。

    Args:
        ticker: 股票代码（带前缀，用于日志）
        kline: 至少含 close 列的 DataFrame（timestamps/date 列需可解析）
        window: 校验最近多少个交易日（默认 20）

    Raises:
        DataInconsistencyError: 最近 window 日内出现单日涨跌幅绝对值 > 50% 的跳变
            （真实 A 股除权/复权或数据源漂移都会落入此区间，新股首日除外）
    """
    from trade_krono_cli.errors import DataInconsistencyError

    if kline is None or len(kline) < 2:
        return

    ts_col = "timestamps" if "timestamps" in kline.columns else "date"
    df = kline.copy()
    if ts_col in df.columns:
        df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
        df = df.sort_values(ts_col).reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    if close.dropna().empty:
        return

    tail = close.iloc[-window:]
    if len(tail) < 2:
        return
    pct = tail.pct_change().abs()
    for i in range(1, len(tail)):
        p = pct.iloc[i]
        if pd.notna(p) and p > 0.50:
            date = str(df[ts_col].iloc[-window + i])[:10] if ts_col in df.columns else "n/a"
            msg = (
                f"K线复权基准漂移 {ticker}: {date} 单日涨跌幅 {p * 100:.1f}% "
                f"(close={tail.iloc[i]:.2f} prev={tail.iloc[i - 1]:.2f})，"
                f"疑似不同数据源复权体系混入，需重拉对齐"
            )
            raise DataInconsistencyError(msg)


def _get_cache_latest_date() -> str | None:
    """获取缓存中最新交易日期。"""
    if not CACHE_DB.exists():
        return None
    try:
        conn = get_sqlite_conn(CACHE_DB)
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


def fetch_hk_pe_snapshot(
    tickers: list[str], batch_size: int = 50
) -> dict[str, dict[str, Any]]:
    """批量获取港股的 PE_TTM 和名称（通过腾讯行情接口 qt.gtimg.cn/q=hk{code}）。

    这是专门为港股设计的元数据获取函数，因为港股不使用同花顺 Fuyao API，
    而使用腾讯行情接口。每批最多 50 只股票。

    Parameters
    ----------
    tickers : list[str]
        港股 ticker 列表，格式如 ``hk.00700``、``hk.09988``。
    batch_size : int
        每批请求数量，默认 50。

    Returns
    -------
    dict[str, dict[str, Any]]
        6位代码到信息的映射，如 ``{"00700": {"pe": 12.5, "name": "腾讯控股"}}``。
        获取失败或 PE 为空的股票不会出现在结果中。
    """
    import time as _time

    result: dict[str, dict[str, Any]] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        codes = ",".join(t.replace("hk.", "") for t in batch)
        url = f"https://qt.gtimg.cn/q=hk{codes}"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            for line in r.text.strip().split(";"):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw_code = ""
                    prefix, data = line.split("=", 1)
                    raw_code = prefix.replace("v_", "").lower()
                    parts = data.strip('"').split("~")
                    if len(parts) < 40:
                        continue
                    name = parts[1].strip() if len(parts) > 1 else ""
                    pe_raw = parts[39]
                    if not pe_raw or pe_raw == "--":
                        continue
                    try:
                        pe_f = float(pe_raw)
                    except (TypeError, ValueError):
                        continue
                    result[raw_code] = {"pe": pe_f, "name": name}
                except Exception:
                    logger.debug(f"港股 PE 解析失败: {raw_code}")
                    continue
        except Exception as e:
            logger.debug(f"港股 PE 获取失败（批次 {i // batch_size + 1}）: {str(e)[:80]}")
            continue
        # 避免频繁请求触发腾讯限流
        if i + batch_size < len(tickers):
            _time.sleep(0.5)
    return result
