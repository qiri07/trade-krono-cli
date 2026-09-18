"""data_providers.benchmark — Provider 延迟 benchmark 逻辑。

提取自 data_providers.factory，职责单一：
  · _BenchResult 数据类
  · DataProviderFactory.bench_all / _benchmark_provider / _bench_date / _bench_workers
  · 排名缓存的读写辅助方法（_get_cached_ranked_chain / _write_ranked_chain）

factory.py 保留核心的实例管理、降级路由、fetch_* 接口。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════
#  Benchmark 数据类
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class _BenchResult:
    """单次 Provider benchmark 结果。"""

    name: str
    latency_ms: float
    success: bool


# ═══════════════════════════════════════════════════════
#  Benchmark 核心方法（挂在 DataProviderFactory 上使用）
# ═══════════════════════════════════════════════════════


def _bench_date() -> str:
    """返回用于 benchmark 的采样日期（最近 1 天）。"""
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _bench_workers() -> int:
    """Benchmark 并发线程数，从 Settings 读取。"""
    import trade_krono_cli.config as _cfg

    return _cfg.get_settings().provider_bench_workers


def _rank_cache_ttl_sec() -> int:
    """Provider 排名缓存 TTL（秒），从 Settings 读取。"""
    import trade_krono_cli.config as _cfg

    return _cfg.get_settings().provider_rank_cache_ttl_sec


def benchmark_provider(
    factory: object,
    name: str,
    ticker: str,
) -> _BenchResult:
    """Benchmark 单个 Provider：用一个小查询测量延迟。"""
    provider = getattr(factory, "get_provider")(name)
    if provider is None or not getattr(provider, "supports_kline", False):
        return _BenchResult(name=name, latency_ms=float("inf"), success=False)
    try:
        t0 = time.perf_counter()
        data = getattr(provider, "fetch_kline")(ticker, _bench_date(), _bench_date(), "d", "1")
        latency_ms = (time.perf_counter() - t0) * 1000
        success = data is not None and getattr(data, "is_empty", False) is False
        return _BenchResult(name=name, latency_ms=latency_ms, success=success)
    except Exception as e:
        from loguru import logger

        logger.debug(f"benchmark {name} 失败: {e}")
        return _BenchResult(name=name, latency_ms=float("inf"), success=False)


def get_cached_ranked_chain(
    rank_cache: dict[str, tuple[list[str], float]],
    rank_lock: threading.Lock,
    ttl_sec: int,
    ticker_type: str,
) -> tuple[list[str], float] | None:
    """读取缓存中指定 ticker_type 的已排序 Provider 链（不含 bj. 特殊处理）。"""
    with rank_lock:
        cached = rank_cache.get(ticker_type)
    if cached is None:
        return None
    ranked_chain, ts = cached
    if time.time() - ts >= ttl_sec:
        return None
    return ranked_chain, ts


def write_ranked_chain(
    rank_cache: dict[str, tuple[list[str], float]],
    rank_lock: threading.Lock,
    ticker_type: str,
    ranked_chain: list[str],
) -> None:
    """将排序结果写入缓存，带当前时间戳。"""
    with rank_lock:
        rank_cache[ticker_type] = (ranked_chain, time.time())
