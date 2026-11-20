"""fill_missing 选股 — K 线拉取。

包含 _fetch_kline_provider / fetch_full_range / fetch_today_incremental，
实现 provider 级超时后自动降级尝试下一个 provider。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from scripts.fill_missing.constants import FETCH_TIMEOUT as _DEFAULT_FETCH_TIMEOUT
from scripts.fill_missing.constants import INCREMENTAL_LOOKBACK_DAYS, START_DATE
from scripts.fill_missing.dates import get_expected_date, get_today_date
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data_providers.base import KlineData


def _fetch_kline_provider(
    factory: Any,
    ticker: str,
    provider_name: str,
    start_date: str,
    end_date: str,
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> tuple[KlineData | None, Exception | None, bool]:
    """单个 Provider 拉取 K 线，内部启线程 + join 实现超时控制。

    用于 provider 级超时后自动降级到下一个 provider 的降级链路。

    Args:
        factory: DataProviderFactory 实例。
        ticker: 股票标准化键（如 sh.600519）。
        provider_name: Provider 名称（如 baostock / akshare / tushare）。
        start_date: 起始日期（YYYY-MM-DD）。
        end_date: 结束日期（YYYY-MM-DD）。
        fetch_timeout: 单 provider 拉取超时（秒）。

    Returns:
        (KlineData | None, 异常 | None, 是否超时)：
            成功且有数据时返回 (KlineData, None, False)；拉取失败时返回
            (None, 异常, False)；Provider 拉取超时时返回 (None, None, True)。
    """
    error: Exception | None = None
    result: KlineData | None = None

    def _attempt(provider_name: str) -> None:
        nonlocal error, result
        try:
            provider = factory.get_provider(provider_name)
            if provider is None:
                return
            result = provider.fetch_kline(
                ticker, start_date, end_date, frequency="d", adjustflag="1"
            )
        except Exception as e:
            error = e
            result = None

    attempt = threading.Thread(target=_attempt, args=(provider_name,), daemon=True)
    attempt.start()
    attempt.join(timeout=fetch_timeout)
    if attempt.is_alive():
        # 单 provider 拉取超时
        return (None, None, True)
    return (result, error, False)


def fetch_full_range(
    factory: Any,
    ticker: str,
    provider_chain: list[str],
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取（provider 级超时后自动降级尝试下一个 provider）。

    Args:
        factory: DataProviderFactory 实例。
        ticker: 股票标准化键（如 sh.600519）。
        provider_chain: Provider 名称列表，按优先级排序。
        fetch_timeout: 单 provider 拉取超时（秒）。

    Returns:
        (provider_name, 记录数, 日期范围) 元组；全部失败时返回
        ("failed", 0, 错误信息)。
    """
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch() -> None:
        for provider_name in provider_chain:
            result, error, timed_out = _fetch_kline_provider(
                factory,
                ticker,
                provider_name,
                START_DATE,
                get_today_date(),
                fetch_timeout=fetch_timeout,
            )
            if timed_out:
                # provider 级超时 → 转失败并降级尝试下一个 provider
                error_container.append(f"{provider_name}: 超时{fetch_timeout:.0f}s")
                continue
            if error is not None:
                error_container.append(f"{provider_name}:{error}")
                continue
            if result is None or result.is_empty:
                continue
            df = result.to_dataframe()
            if len(df) == 0:
                continue
            # ETF 无复权概念，缓存统一按 adjustflag='1' 存储
            cache = get_cache()
            ts = pd.to_datetime(df["timestamps"])
            cache.set_kline(
                ticker,
                ts.min().strftime("%Y-%m-%d"),
                ts.max().strftime("%Y-%m-%d"),
                "d",
                df,
                ttl=0.0,
            )
            result_container.append(
                (
                    provider_name,
                    len(df),
                    f"{ts.min().strftime('%Y-%m-%d')}~{ts.max().strftime('%Y-%m-%d')}",
                )
            )
            return

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join()

    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def fetch_today_incremental(
    factory: Any,
    ticker: str,
    provider_chain: list[str],
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> tuple[str, int, str]:
    """拉取增量数据（provider 级超时后自动降级尝试下一个 provider）。

    以「应已落库的最近交易日」(:func:`get_expected_date`) 为终点，向前回看
    ``INCREMENTAL_LOOKBACK_DAYS`` 天拉取窗口，用于补齐最新交易日以及中间可能
    缺失的交易日（数据源滞后时单日窗口会留下空洞）。

    新拉取的 K 线会与现有历史行合并去重后写回单条记录（按 timestamps 去重，
    保留最新值），重复拉取已有交易日是幂等的。

    Args:
        factory: DataProviderFactory 实例。
        ticker: 股票标准化键（如 sh.600519）。
        provider_chain: Provider 名称列表，按优先级排序。
        fetch_timeout: 单 provider 拉取超时（秒）。

    Returns:
        (provider_name, 记录数, 日期范围) 元组；全部失败时返回
        ("failed", 0, 错误信息)。
    """
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    # 终点取「应已落库的最近交易日」而非原始今日：开盘前/盘中运行时当日 K 线尚不存在，
    # 若按今日拉取会全部返回空、触发无谓重试。回看窗口可顺带补齐中间缺失交易日。
    end_date = get_expected_date()
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    def _fetch() -> None:
        for provider_name in provider_chain:
            result, error, timed_out = _fetch_kline_provider(
                factory,
                ticker,
                provider_name,
                start_date,
                end_date,
                fetch_timeout=fetch_timeout,
            )
            if timed_out:
                # provider 级超时 → 转失败并降级尝试下一个 provider
                error_container.append(f"{provider_name}: 超时{fetch_timeout:.0f}s")
                continue
            if error is not None:
                error_container.append(f"{provider_name}:{error}")
                continue
            if result is None or result.is_empty:
                continue
            df = result.to_dataframe()
            if len(df) == 0:
                continue
            cache = get_cache()
            # 把新拉取的增量 K 线追加到现有历史行，而非新建独立记录：
            # 读取该 ticker 从 START_DATE 起至预期交易日的全部 K 线（含历史行与已有今日行），
            # 与增量数据合并去重后写回单条记录。
            combined = df
            existing = cache.get_kline(ticker, START_DATE, end_date, "d", "1")
            if existing is not None and len(existing) > 0:
                # 统一 timestamps 为 datetime，避免 str 与 datetime64 混型导致排序崩溃
                existing = existing.copy()
                existing["timestamps"] = pd.to_datetime(existing["timestamps"])
                df = df.copy()
                df["timestamps"] = pd.to_datetime(df["timestamps"])
                combined = (
                    pd.concat([existing, df], ignore_index=True)
                    .drop_duplicates(subset=["timestamps"], keep="last")
                    .sort_values("timestamps")
                    .reset_index(drop=True)
                )
            cache.set_kline(
                ticker,
                combined["timestamps"].min().strftime("%Y-%m-%d"),
                combined["timestamps"].max().strftime("%Y-%m-%d"),
                "d",
                combined,
                ttl=0.0,
            )
            result_container.append(
                (
                    provider_name,
                    len(combined),
                    f"{combined['timestamps'].min().strftime('%Y-%m-%d')}"
                    f"~{combined['timestamps'].max().strftime('%Y-%m-%d')}",
                )
            )
            return

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join()

    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")
