"""缓存层 — TTL 驱动的 SQLite 缓存。

支持三种缓存类型：
  kline_cache  — K 线数据（pd.DataFrame pickle）
  ta_cache     — TA 分析结果（JSON dict）
  kronos_cache — Kronos 预测结果（JSON dict）

缓存语义：
  · K 线：全量永久缓存，当日数据不设置 TTL
  · TA / Kronos：缓存 key 含 config_hash + 模型版本，配置变更自动失效
"""

from __future__ import annotations

import threading

from trade_krono_cli.cache.base import KLINE_HISTORICAL_TTL, Cache, _validate_table_name
from trade_krono_cli.cache.kline import KlineCache
from trade_krono_cli.cache.kronos import KronosCache
from trade_krono_cli.cache.queries import CacheQueries
from trade_krono_cli.cache.ta import TADataCache

# 向后兼容别名
_KLINE_HISTORICAL_TTL = KLINE_HISTORICAL_TTL  # noqa: N813
_KLINE_RECENT_TTL = KLINE_HISTORICAL_TTL  # noqa: N813

__all__ = (
    "Cache",
    "KlineCache",
    "TADataCache",
    "KronosCache",
    "CacheQueries",
    "KLINE_HISTORICAL_TTL",
    "_KLINE_HISTORICAL_TTL",
    "_KLINE_RECENT_TTL",
    "_validate_table_name",
    "get_cache",
    "clear_cache_singleton",
)


# ── 单例管理 ────────────────────────────────────────────────────────────────

_cache: Cache | None = None
_cache_lock = threading.Lock()


def get_cache() -> Cache:
    """获取全局 Cache 单例，首次调用时自动初始化。"""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = Cache()
    return _cache


def clear_cache_singleton() -> None:
    """清除 Cache 全局单例（测试隔离用）。"""
    global _cache
    _cache = None
