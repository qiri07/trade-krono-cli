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
from scripts.fill_missing.dates import get_expected_date
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data_providers.base import KlineData

# 读取库内已有记录时的上界（远未来），确保所有历史行都参与并集合并
_READ_ALL_END_DATE = "9999-12-31"


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


def _merge_with_cached(cache: Any, ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    """把新拉取的 K 线与库内已有历史按 ``timestamps`` 做并集合并。

    同一交易日以新拉取的数据为准（``keep="last"``），避免多源复权口径混入时
    保留旧值；库内没有记录时直接返回新数据。合并结果是并集，因此数据源返回的
    序列比库内更短（源头滞后 / 停牌）时不会丢掉已有交易日。

    Args:
        cache: 缓存实例（需实现 ``get_kline``）。
        ticker: 股票标准化键（如 sh.600519）。
        df: 新拉取的 K 线 DataFrame（需含 ``timestamps`` 列）。

    Returns:
        按 ``timestamps`` 升序、已去重的合并结果。
    """
    fresh = df.copy()
    fresh["timestamps"] = pd.to_datetime(fresh["timestamps"], format="mixed")

    # 上界取远未来，确保库内任何已有记录都参与合并（含历史脏数据 end 超前的情况）
    existing = cache.get_kline(ticker, START_DATE, _READ_ALL_END_DATE, "d", "1")
    if existing is None or len(existing) == 0:
        return fresh.sort_values("timestamps").reset_index(drop=True)

    cached = existing.copy()
    cached["timestamps"] = pd.to_datetime(cached["timestamps"], format="mixed")
    return (
        pd.concat([cached, fresh], ignore_index=True)
        .drop_duplicates(subset=["timestamps"], keep="last")
        .sort_values("timestamps")
        .reset_index(drop=True)
    )


def fetch_full_range(
    factory: Any,
    ticker: str,
    provider_chain: list[str],
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取（provider 级超时后自动降级尝试下一个 provider）。

    终点取「应已落库的最近交易日」，避免盘中把未收盘的当日 bar 写成正式日线；
    写入时与库内已有历史做并集合并（见 :func:`_merge_with_cached`），
    因此数据源本身滞后、返回序列比库内更短时也不会丢掉已有交易日。

    Args:
        factory: DataProviderFactory 实例。
        ticker: 股票标准化键（如 sh.600519）。
        provider_chain: Provider 名称列表，按优先级排序。
        fetch_timeout: 单 provider 拉取超时（秒）。

    Returns:
        (provider_name, 合并后记录数, 合并后日期范围) 元组；全部失败时返回
        ("failed", 0, 错误信息)。
    """
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    # 终点取「应已落库的最近交易日」：盘中/开盘前运行时当日 K 线尚未完成，
    # 按原始今日拉取会把盘中未收盘的 bar 当成正式日线写入。
    end_date = get_expected_date()

    def _fetch() -> None:
        for provider_name in provider_chain:
            result, error, timed_out = _fetch_kline_provider(
                factory,
                ticker,
                provider_name,
                START_DATE,
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
            # ETF 无复权概念，缓存统一按 adjustflag='1' 存储
            cache = get_cache()
            # 与库内已有历史做并集合并，而非直接覆盖：
            # 数据源本身滞后时（港股 sina、停牌股等），其返回序列的最新日可能早于
            # 库内已有记录，直接覆盖会让该 ticker 的 end 倒退回更早日期，丢掉新数据。
            combined = _merge_with_cached(cache, ticker, df)
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
            # 读取该 ticker 从 START_DATE 起的全部 K 线（含历史行与已有今日行），
            # 与增量数据并集合并去重后写回单条记录。
            combined = _merge_with_cached(cache, ticker, df)
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
