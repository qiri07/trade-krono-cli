"""trade_krono_cli.data_providers.factory_benchmark — Provider 自适应优先级 Benchmark。

职责（单一职责原则）：
  · 对所有可用 Provider 延迟 benchmark，按延迟排序
  · Provider 排名缓存（ticker 类型级，TTL 10 分钟）
  · 读写排名缓存的辅助函数
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.benchmark import (
    _bench_workers,
    _BenchResult,
    benchmark_provider,
    get_cached_ranked_chain,
    write_ranked_chain,
)


def _bench_factory_provider(factory: Any, name: str, ticker: str) -> _BenchResult:
    """Benchmark 单个 Provider（委托 benchmark 模块）。"""
    return benchmark_provider(factory, name, ticker)


def get_cached_ranked_chain_factory(
    rank_cache: dict[str, Any],
    rank_lock: Any,
    rank_cache_ttl_sec: int,
    ticker_type: str,
) -> Any:
    """读取排名缓存（委托 benchmark 模块）。"""
    return get_cached_ranked_chain(rank_cache, rank_lock, rank_cache_ttl_sec, ticker_type)


def write_ranked_chain_factory(
    rank_cache: dict[str, Any],
    rank_lock: Any,
    ticker_type: str,
    ranked_chain: list[str],
) -> None:
    """写入排名缓存（委托 benchmark 模块）。"""
    write_ranked_chain(rank_cache, rank_lock, ticker_type, ranked_chain)


def bench_all_providers(
    factory: Any,
    ticker: str = "sh.600519",
    workers: int | None = None,
) -> list[_BenchResult]:
    """对所有可用 Provider 进行延迟 benchmark，按速度排序。

    Args:
        factory: DataProviderFactory 实例（用于获取可用 provider 列表）。
        ticker: 用于测试的代表性 ticker（默认沪深主板大盘股）。
        workers: 并发 benchmark 线程数，默认 _bench_workers()。

    Returns:
        按 latency_ms 升序排列（越快越靠前），失败的排在末尾。
    """
    workers = workers or _bench_workers()
    names = factory.available_providers()
    if not names:
        logger.warning("没有可用的 Provider 进行 benchmark")
        return []

    logger.info(f"🔬 开始 Benchmark：{len(names)} 个 Provider，ticker={ticker}")
    results: list[_BenchResult] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # 通过 factory 实例方法调用，使测试可 patch factory._benchmark_provider
        bench_fn = factory._benchmark_provider
        futures = {pool.submit(bench_fn, n, ticker): n for n in names}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            status = f"{result.latency_ms:.0f}ms" if result.success else "FAIL"
            logger.info(f"  {result.name:12s}  {status}")

    results.sort(key=lambda r: (r.latency_ms, r.name))
    return results


def get_ranked_chain_for_ticker_factory(
    factory: Any,
    ticker: str,
) -> list[str]:
    """获取并缓存按速度排序的 Provider 链（ticker 类型级缓存，TTL 10 分钟）。

    同一 ticker 类型（sh/sz/bj）共享缓存。
    北交所（bj.）股票 tonghuashun 始终置顶，不受 benchmark 结果影响。

    Args:
        factory: DataProviderFactory 实例。
        ticker: 标准化 ticker，如 "sh.600519"。

    Returns:
        按速度排序的 provider 名称列表。
    """
    ticker_type = ticker.split(".", maxsplit=1)[0] if "." in ticker else ticker
    cached = get_cached_ranked_chain_factory(
        factory._rank_cache, factory._rank_lock, factory._rank_cache_ttl_sec(), ticker_type
    )
    if cached is not None:
        return cached[0]

    results = factory.bench_all(ticker=ticker)
    ranked_chain = [r.name for r in results if r.success]
    failed = [r.name for r in results if not r.success]
    ranked_chain.extend(failed)

    write_ranked_chain_factory(
        factory._rank_cache, factory._rank_lock, ticker_type, ranked_chain
    )
    logger.info(f"📊 {ticker} Provider 排序: {' → '.join(ranked_chain)}")
    return ranked_chain


def invalidate_rank_cache_factory(factory: Any, ticker_type: str) -> None:
    """清除指定 ticker 类型的 Provider 排名缓存。

    Args:
        factory: DataProviderFactory 实例。
        ticker_type: ticker 前缀，如 "sh"、"sz"、"bj"。
    """
    with factory._rank_lock:
        factory._rank_cache.pop(ticker_type, None)
