"""sync_helpers.fetcher — 并发 K 线拉取。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from trade_krono_cli.cli_commands.sync_helpers.semaphore import _get_provider_semaphore


def _fetch_ticker_parallel(
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str,
    adjustflag: str,
    use_cache: bool,
    providers: list[str],
) -> tuple[int, str | None]:
    """并发尝试多个 Provider 拉取单只股票的增量 K 线。

    受全局信号量 + 每 Provider 信号量双重限流，防止触发接口限制。

    Returns
    -------
    (行数, provider名称) 或 (0, None) 表示失败
    """
    from trade_krono_cli.data import fetch_kline_incremental

    def _single(ticker_: str, provider_: str) -> tuple[int, str | None]:
        # 获取该 Provider 的信号量，限制并发请求数
        provider_sema = _get_provider_semaphore(provider_)
        with provider_sema:
            try:
                df = fetch_kline_incremental(
                    ticker_,
                    start_date,
                    end_date,
                    frequency,
                    adjustflag,
                    use_cache,
                )
                if df is not None and len(df) > 0:
                    return (len(df), provider_)
                return (0, None)
            except Exception as e:
                logger.debug(f"  {ticker_} ← {provider_} 失败: {e}")
                return (0, None)

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {pool.submit(_single, ticker, p): p for p in providers}
        for future in as_completed(futures):
            rows, provider_name = future.result()
            if rows > 0:
                return rows, provider_name
    return 0, None
