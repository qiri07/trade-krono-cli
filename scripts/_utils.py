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

import json
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    import yaml  # noqa: PLC0415

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

CACHE_DB = Path("outputs/cache/pipeline_cache.db")

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


# ── 共享常量 ────────────────────────────────────────────────────────────────
# ST / 异常股名称关键词（各脚本统一使用，避免多处重复定义）
# 全市场扫描脚本用完整列表（含前缀识别的新股标识），白名单分析用精简版（仅 ST 类）。
# 注意：ST/*ST/退市 按子串匹配；N/C（新股/次新）只按名称首个字符匹配，
# 否则 "TCL科技"、"MSCI中国A股ETF" 等含字母 N/C 的正常名称会被误杀。
ST_NAME_KEYWORDS: tuple[str, ...] = ("ST", "*ST", "退市")
ST_NAME_KEYWORDS_MINIMAL: tuple[str, ...] = ("ST", "*ST", "退市")
# 新股（N）/次新（C）名称前缀，仅用于完整规则（keywords 缺省时）
ST_NAME_PREFIXES: tuple[str, ...] = ("N", "C")

# ETF 代码段：沪市 51/52/53/55/56/58 开头，深市 159/156/158 开头
ETF_SH_PREFIXES: tuple[str, ...] = ("51", "52", "53", "55", "56", "58")
ETF_SZ_PREFIXES: tuple[str, ...] = ("159", "156", "158")

# 北交所股票代码前缀（6 位裸代码以 920 开头，或带交易所前缀 bj.）
# 注意：用 "920" 而非 "92"，避免误杀 92 开头的其他代码段（如 920xxx 北交所新股）
BSE_CODE_PREFIXES: tuple[str, ...] = ("920",)
BSE_TICKER_PREFIXES: tuple[str, ...] = ("bj.",)

# 历史 K 线记录数低于此值视为同步失败（历史数据严重不足），用于全量回补判定
FAILED_MIN_RECORDS: int = 100

# 收盘 + 落库缓冲：此刻之后当日 K 线应可获取
MARKET_CLOSE_HOUR: int = 15
MARKET_CLOSE_MINUTE: int = 30


def expected_trading_day(now: datetime | None = None) -> str:
    """返回此刻应已落库的最近交易日（YYYY-MM-DD）。

    收盘后（>= 15:30）返回当日；盘中返回上一交易日；周末回退到周五。

    Args:
        now: 用于测试注入的时间，缺省取 ``datetime.now()``。

    Returns:
        日期字符串，如 ``2026-11-10``。
    """
    current = now or datetime.now()
    day = current.date()
    after_close = (current.hour, current.minute) >= (
        MARKET_CLOSE_HOUR,
        MARKET_CLOSE_MINUTE,
    )
    if day.weekday() >= 5:  # 周六/周日 → 回退到周五
        day -= timedelta(days=day.weekday() - 4)
    elif not after_close:
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")


# ── 选股阈值（各筛选脚本统一引用，禁止在脚本内重复硬编码）──────────────────
# 技术面选股线（双均线 / MACD+均线 / 综合交集）的 PE 上限与盈利口径
PE_MAX_TECH: float = 30.0
# 价值线（巴菲特六闸门）的 PE 上限
PE_MAX_VALUE: float = 16.0
# 技术面选股线的最小总市值（亿元）
MIN_MARKET_CAP_YI: float = 50.0


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
    if code[:2] in ETF_SH_PREFIXES or code[:3] in ETF_SZ_PREFIXES or code.startswith("5"):
        # ETF 代码：5x 开头（沪市 ETF 全段）或 159/156/158 开头（深市 ETF）
        # 归一化为带交易所前缀格式，使 baostock 能直接识别
        if code.startswith("5") or code[:2] in ETF_SH_PREFIXES:
            # 沪市 ETF（5x 段含 50x/51x/52x/53x/55x/56x/57x/58x）→ sh.<code>
            return f"sh.{code}"
        # 深市 ETF（15x/16x/18x 段）→ sz.<code>
        return f"sz.{code}"
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


def is_etf_ticker(ticker: str) -> bool:
    """判断 ticker 是否为 ETF 基金。

    沪市 51/52/53/56/58 开头、深市 159/156/158 开头视为 ETF。

    Args:
        ticker: 带交易所前缀的 ticker，如 "sh.510300"、"sz.159915"。

    Returns:
        True 表示该 ticker 是 ETF 基金代码。
    """
    if "." not in ticker:
        return False
    prefix, code = ticker.split(".", 1)
    if prefix == "sh":
        # 沪市 ETF 统一以 5x 开头（51/52/53/55/56/57/58 + 50x 等），一视同仁
        return code.startswith("5") or code[:2] in ETF_SH_PREFIXES
    if prefix == "sz":
        return code[:3] in ETF_SZ_PREFIXES
    return False


def get_all_tickers() -> list[str]:
    """从 SQLite 缓存数据库读取所有不重复的 ticker 列表。

    Returns:
        按字母序排序的 ticker 列表。
    """
    conn = get_sqlite_conn(CACHE_DB)
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()]
    conn.close()
    return sorted(tickers)


def get_astock_tickers(db_path: Path, logger_fn: Callable[[str], None] | None = None) -> list[str]:
    """从指定缓存数据库读取全部 ticker，并排除北交所与港股。

    保留 A 股 + ETF（ETF 以纯技术面参与各筛选线）。

    Args:
        db_path: kline_cache 数据库路径。
        logger_fn: 可选日志函数（如 ``logger.info``），用于输出扫描统计。

    Returns:
        排除北交所/港股后的 ticker 列表（按字母序）。
    """
    conn = get_sqlite_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    all_tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    astock_tickers = [t for t in all_tickers if not is_bse_or_hk(t)]
    if logger_fn is not None:
        etf_count = sum(1 for t in astock_tickers if is_etf_ticker(t))
        logger_fn(
            f"📊 全市场扫描：共 {len(all_tickers)} 只，排除北交所/港股后 {len(astock_tickers)} 只"
            f"（含ETF {etf_count} 只，纯技术面参与）"
        )
    return astock_tickers


def strip_ticker_prefix(ticker: str) -> str:
    """去除交易所前缀，返回 6 位纯数字代码（``sh.600519`` → ``600519``）。"""
    return ticker.split(".", 1)[1] if "." in ticker else ticker


def is_st_name(name: str, keywords: tuple[str, ...] | None = None) -> bool:
    """判断股票名称是否命中 ST / 异常股规则。

    ``keywords`` 为 None 时使用完整规则：关键词按子串匹配（ST/*ST/退市），
    并对名称首字符做 N/C 前缀匹配（新股/次新）。传入自定义 ``keywords``
    时只做子串匹配，不做前缀匹配。

    Args:
        name: 股票名称（如 ``*ST 金钰``、``N 新股``）。
        keywords: 自定义关键词元组；缺省用完整版 :data:`ST_NAME_KEYWORDS`。

    Returns:
        名称命中任一规则时返回 True。
    """
    kws = ST_NAME_KEYWORDS if keywords is None else keywords
    if any(kw in name for kw in kws):
        return True
    if keywords is not None:
        return False
    head = name.lstrip()
    return any(head.startswith(p) for p in ST_NAME_PREFIXES)


def load_metadata_index(
    cache_dir: Path | None = None, *, include_bare_keys: bool = True
) -> dict[str, dict[str, object]]:
    """加载 ``metadata.json`` 并归一化键格式。

    返回的字典默认同时包含带前缀键（``sh.600519``）与 6 位裸代码键（``600519``），
    两种写法指向同一份元数据，便于各脚本用任意格式查询，避免键不匹配丢数据。

    Args:
        cache_dir: 缓存目录；缺省用 ``get_settings().cache_dir``，回退 ``outputs/cache``。
        include_bare_keys: 是否额外写入 6 位裸代码键。关闭时仅保留带前缀键，
            与历史 ``load_metadata`` 行为一致（条数统计不会翻倍）。

    Returns:
        元数据字典；文件缺失或解析失败时返回空字典。
    """
    if cache_dir is None:
        cache_dir = get_cache_db_path().parent
    meta_path = Path(cache_dir) / "metadata.json"
    try:
        with meta_path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    index: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        normalized = normalize_ticker_key(str(key))
        index.setdefault(normalized, value)
        if include_bare_keys:
            index.setdefault(strip_ticker_prefix(normalized), value)
    return index


def _lookup_meta(metadata: dict[str, dict[str, object]] | None, ticker: str) -> dict[str, object]:
    """在元数据索引中查单只票，兼容带前缀与裸代码两种键。"""
    if metadata is None:
        return {}
    normalized = normalize_ticker_key(ticker)
    bare = strip_ticker_prefix(normalized)
    for key in (ticker, normalized, bare):
        value = metadata.get(key)
        if isinstance(value, dict):
            return value
    return {}


def exclusion_reason(
    ticker: str,
    metadata: dict[str, dict[str, object]] | None = None,
    *,
    exclude_st: bool = True,
    exclude_bse: bool = True,
    exclude_hk: bool = True,
    exclude_etf: bool = False,
) -> str | None:
    """返回单只票被剔除的结构性原因（不含估值判断）。

    供需要按原因分类统计的脚本使用（如 ``macd_ma_screener`` 的 skipped 计数器），
    其余场景直接用 :func:`filter_stock_universe` 即可。

    Args:
        ticker: 带前缀或 6 位裸代码。
        metadata: :func:`load_metadata_index` 返回的元数据索引，可为 ``None``。
        exclude_st: 是否剔除 ST 股（优先读 ``is_st`` 标记，否则按名称关键词判断）。
        exclude_bse: 是否剔除北交所。
        exclude_hk: 是否剔除港股。
        exclude_etf: 是否剔除 ETF。

    Returns:
        命中的原因键（``"hk"`` / ``"bse"`` / ``"etf"`` / ``"st"``），未命中返回 ``None``。
    """
    if exclude_hk and ticker.startswith("hk."):
        return "hk"
    if exclude_bse and (ticker.startswith("bj.") or is_bse_code(strip_ticker_prefix(ticker))):
        return "bse"
    if exclude_etf and is_etf_ticker(ticker):
        return "etf"
    if exclude_st and metadata is not None:
        meta = _lookup_meta(metadata, ticker)
        if bool(meta.get("is_st")):
            return "st"
        name = meta.get("name")
        if isinstance(name, str) and is_st_name(name, ST_NAME_KEYWORDS_MINIMAL):
            return "st"
    return None


def filter_stock_universe(
    tickers: list[str],
    metadata: dict[str, dict[str, object]] | None = None,
    *,
    exclude_st: bool = True,
    exclude_bse: bool = True,
    exclude_hk: bool = True,
    exclude_etf: bool = False,
    max_pe: float | None = None,
    min_pb: float | None = None,
    min_market_cap: float | None = None,
) -> list[str]:
    """统一股票池过滤引擎。

    各选股/扫描脚本在扫描全市场前应统一调用本函数，避免重复实现
    ST / 北交所 / 港股 / ETF / PE / PB / 市值过滤逻辑，保证口径一致。

    Args:
        tickers: 带前缀 ticker 列表，如 ``["sh.600519", "hk.00700", "bj.920001"]``。
        metadata: :func:`load_metadata_index` 返回的元数据索引。为 ``None`` 时
            只做代码/前缀层面的过滤，不做 ST / 估值 / 市值过滤。
        exclude_st: 是否剔除 ST 股（优先读 ``is_st`` 标记，否则按名称关键词判断）。
        exclude_bse: 是否剔除北交所（``bj.`` 前缀或 920 开头裸代码）。
        exclude_hk: 是否剔除港股（``hk.`` 前缀）。
        exclude_etf: 是否剔除 ETF（``sh.5x`` / ``sh.58x`` / ``sz.15x`` 等前缀）。
        max_pe: PE 上限。``pe >= max_pe`` 或 ``pe <= 0``（亏损/无效）时剔除。
        min_pb: PB 下限，``pb < min_pb`` 时剔除（用于资不抵债过滤）。
        min_market_cap: 最小市值（亿元），低于此值时剔除。

    Returns:
        过滤后的 ticker 列表，保持原始顺序。
    """
    result: list[str] = []

    for ticker in tickers:
        if (
            exclusion_reason(
                ticker,
                metadata,
                exclude_st=exclude_st,
                exclude_bse=exclude_bse,
                exclude_hk=exclude_hk,
                exclude_etf=exclude_etf,
            )
            is not None
        ):
            continue

        if metadata is None:
            result.append(ticker)
            continue

        meta = _lookup_meta(metadata, ticker)

        # PE 过滤（<=0 视为亏损/无效，与既有 PE∈(0,max) 口径一致）
        if max_pe is not None:
            pe = meta.get("pe_ttm")
            if isinstance(pe, (int, float)) and (pe <= 0 or pe >= max_pe):
                continue

        if min_pb is not None:
            pb = meta.get("pb")
            if isinstance(pb, (int, float)) and pb < min_pb:
                continue

        if min_market_cap is not None:
            cap = meta.get("market_cap")
            if isinstance(cap, (int, float)) and cap < min_market_cap:
                continue

        result.append(ticker)

    return result


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
    """计算期望的缓存日期（最近交易日，优先昨天，周末回退到周五）。"""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    if today.weekday() >= 5:  # 5=周六, 6=周日
        days_back = today.weekday() - 4  # 周六→1, 周日→2
        yesterday = today - timedelta(days=days_back)
    return yesterday.strftime("%Y-%m-%d")


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


def get_sqlite_conn(db_path: Path | str = CACHE_DB, readonly: bool = False) -> sqlite3.Connection:
    """获取一个配置好的 SQLite 连接。

    使用与 Cache 类相同的 WAL 模式和 busy_timeout 设置，确保多线程环境下
    并发访问时不会出现 database is locked 错误。

    Args:
        db_path: 数据库文件路径，默认使用 CACHE_DB。
        readonly: 是否以只读模式打开，默认 False。

    Returns:
        配置好的 sqlite3.Connection 实例。
    """
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass  # 测试中可能 mock 连接对象，不支持 PRAGMA
    return conn
