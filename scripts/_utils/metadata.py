"""scripts._utils.metadata — metadata.json 元数据索引与过滤原因。

提供元数据加载、键归一化、单只票元数据查询、剔除原因判定等功能。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts._utils.constants import ST_NAME_KEYWORDS_MINIMAL
from scripts._utils.ticker import (
    is_etf_ticker,
    is_st_name,
    normalize_ticker_key,
    strip_ticker_prefix,
)


def get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）。

    尊重 ``TRADING_KRONO_CACHE_DIR`` 环境变量（测试隔离）。
    模块加载时先尝试读取 settings，失败时回退到默认路径。
    """
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        return Path(settings.cache_dir) / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


def load_metadata_index(
    cache_dir: Path | None = None,
    *,
    include_bare_keys: bool = True,
    include_non_astock: bool = False,
) -> dict[str, dict[str, object]]:
    """加载 ``metadata.json`` 并归一化键格式。

    返回的字典默认同时包含带前缀键（``sh.600519``）与 6 位裸代码键（``600519``），
    两种写法指向同一份元数据，便于各脚本用任意格式查询，避免键不匹配丢数据。

    Args:
        cache_dir: 缓存目录；缺省用 ``get_settings().cache_dir``，回退 ``outputs/cache``。
        include_bare_keys: 是否额外写入 6 位裸代码键。关闭时仅保留带前缀键，
            与历史 ``load_metadata`` 行为一致（条数统计不会翻倍）。
        include_non_astock: 是否保留非 A 股（港股/北交所/ETF）键。
            默认 False，仅保留 A 股（沪深主板/创业板/科创板）键，便于今日流程等
            A 股专用场景自动过滤。历史遗留的港股/北交所/ETF 元数据键将被忽略。

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
        if not include_non_astock:
            # 过滤非 A 股：港股、北交所、ETF
            if normalized.startswith(("hk.", "bj.")) or is_etf_ticker(normalized):
                continue
        index.setdefault(normalized, value)
        if include_bare_keys:
            index.setdefault(strip_ticker_prefix(normalized), value)
    return index


def _lookup_meta(
    metadata: dict[str, dict[str, object]] | None, ticker: str
) -> dict[str, object]:
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
    from scripts._utils.ticker import is_bse_code

    if exclude_hk and ticker.startswith("hk."):
        return "hk"
    if exclude_bse and is_bse_code(ticker.split(".")[-1]):
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


def _dict_get_any(d: dict[str, Any] | None, *keys: str) -> Any:
    """从 dict 中按优先级获取第一个非 None 值。"""
    if not d:
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None
