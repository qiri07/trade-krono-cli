"""同步命令共享工具 — 信号量、股票解析、Provider 健康检查、并发拉取。"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from loguru import logger
from rich.console import Console
from rich.table import Table

console = Console()

# ── 常量 ─────────────────────────────────────────────────────────────────────

# 单只股票 K 线拉取超时（秒）
_STOCK_FETCH_TIMEOUT: int = 30

# 每个 Provider 的最大并发请求数（防止被限流/拉黑）
_PROVIDER_CONCURRENCY: dict[str, int] = {
    "mootdx": 5,
    "baostock": 3,
    "akshare": 3,
    "tushare": 3,
    "tonghuashun": 3,
}

# 全局请求间隔（秒），用于在批次之间分散请求
_BETWEEN_REQUEST_DELAY: float = 0.1

# 周期性健康检查：每处理 N 只股票或间隔 M 秒重新检测一次 Provider 健康状态
_HEALTH_CHECK_INTERVAL_TICKERS: int = 200
_HEALTH_CHECK_MIN_INTERVAL_SEC: float = 60.0

# 滞环阈值：连续失败多少次才剔除 Provider，连续成功多少次才恢复
_HEALTH_REMOVE_THRESHOLD: int = 3
_HEALTH_RESTORE_THRESHOLD: int = 2

_EXCHANGE_PREFIX: dict[str, str] = {
    "6": "sh.",  # 上交所主板 + 科创板
    "0": "sz.",  # 深交所主板
    "3": "sz.",  # 创业板
    "9": "bj.",  # 北交所
}

# ── 信号量管理 ───────────────────────────────────────────────────────────────

# 模块级：每个 Provider 的并发信号量
_provider_semaphores: dict[str, threading.Semaphore] = {}
_semaphores_lock = threading.Lock()

# 全局并发上限（所有 Provider 合计），防止整体流量过大
_global_semaphore = threading.Semaphore(20)
_global_sem_lock = threading.Lock()


def _get_provider_semaphore(provider: str) -> threading.Semaphore:
    """获取（或创建）指定 Provider 的并发信号量。"""
    with _semaphores_lock:
        if provider not in _provider_semaphores:
            limit = _PROVIDER_CONCURRENCY.get(provider, 3)
            _provider_semaphores[provider] = threading.Semaphore(limit)
        return _provider_semaphores[provider]


def _get_global_semaphore() -> threading.Semaphore:
    """获取全局并发信号量，首次调用时根据可用 Provider 数量动态调整。"""
    with _global_sem_lock:
        return _global_semaphore


# ── 股票解析 ─────────────────────────────────────────────────────────────────


def _resolve_tickers(raw: str) -> list[str]:
    """将逗号分隔的6位股票代码转为带交易所前缀的 ticker 列表。

    规则：6xxxxx→sh.，0/3xxxxx→sz.，9xxxxx→bj.。
    自动去重并保持首次出现的顺序。
    """
    seen: set[str] = set()
    result: list[str] = []
    if not raw:
        return result
    for code in (c.strip() for c in raw.split(",") if c.strip()):
        if len(code) != 6 or not code.isdigit():
            logger.warning(f"⚠️  白名单代码格式错误，已跳过: {code}")
            continue
        prefix = _EXCHANGE_PREFIX.get(code[0])
        if prefix is None:
            logger.warning(f"⚠️  未知交易所前缀，已跳过: {code}")
            continue
        ticker = f"{prefix}{code}"
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


# ── Provider 健康检查 ────────────────────────────────────────────────────────


def _check_provider_health() -> dict[str, bool]:
    """检查所有 Provider 的健康状态，返回 {name: is_healthy}。"""
    from trade_krono_cli.data_providers import get_data_factory

    factory = get_data_factory()
    result: dict[str, bool] = {}
    for name in factory.provider_chain:
        provider = factory.get_provider(name)
        if provider is None:
            result[name] = False
            continue
        try:
            result[name] = provider.health_check()
        except Exception:
            result[name] = False
    return result


def _update_usable_providers(
    usable_providers: list[str],
    locked_providers: list[str],
    failure_counts: dict[str, int],
) -> list[str]:
    """重新检测 Provider 健康状态，动态更新可用列表（带滞环防抖）。

    使用连续失败/成功次数阈值，避免间歇性抖动导致 Provider 频繁切换。
    返回新的 usable_providers 列表，若与健康状态变化则打印日志。
    """
    health = _check_provider_health()
    from trade_krono_cli.data_providers.factory import get_data_factory

    factory = get_data_factory()
    ranked = factory.get_ranked_chain_for_ticker("sh.600519")
    new_providers: set[str] = set(locked_providers)  # 基于当前可用列表修改

    for p in ranked:
        is_healthy = health.get(p, False)
        if p in locked_providers:
            # 当前可用：连续失败才剔除
            if is_healthy:
                failure_counts[p] = 0
            else:
                failure_counts[p] = failure_counts.get(p, 0) + 1
                if failure_counts[p] >= _HEALTH_REMOVE_THRESHOLD:
                    new_providers.discard(p)
                    logger.warning(f"Provider {p} 连续 {failure_counts[p]} 次健康检查失败，已剔除")
        else:
            # 当前不可用：连续成功才恢复
            if is_healthy:
                failure_counts[p] = failure_counts.get(p, 0) + 1
                if failure_counts[p] >= _HEALTH_RESTORE_THRESHOLD:
                    new_providers.add(p)
                    logger.info(
                        f"Provider {p} 连续 {_HEALTH_RESTORE_THRESHOLD} 次健康检查通过，已恢复"
                    )
            else:
                failure_counts[p] = 0

    result = sorted(new_providers)
    if result != locked_providers:
        logger.info(f"Provider 状态变化: {locked_providers} → {result}")
        console.print(
            f"[dim]⚡ Provider 更新: {[p + ('✅' if p in result else '❌') for p in ranked]}[/dim]"
        )
    return result


# ── 并发拉取 ─────────────────────────────────────────────────────────────────


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
    from trade_krono_cli.data_providers import get_data_factory

    def _single(ticker_: str, provider_: str) -> tuple[int, str | None]:
        # 获取该 Provider 的信号量，限制并发请求数
        provider_sema = _get_provider_semaphore(provider_)
        with provider_sema:
            old_primary = get_data_factory().primary
            old_fallbacks = list(get_data_factory().fallbacks)
            try:
                get_data_factory().primary = provider_
                get_data_factory().fallbacks = []
                df = fetch_kline_incremental(
                    ticker_, start_date, end_date, frequency, adjustflag, use_cache
                )
                return (len(df), provider_) if df is not None and len(df) > 0 else (0, None)
            except Exception as e:
                logger.debug(f"  {ticker_} ← {provider_} 失败: {e}")
                return (0, None)
            finally:
                get_data_factory().primary = old_primary
                get_data_factory().fallbacks = old_fallbacks

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {pool.submit(_single, ticker, p): p for p in providers}
        for future in as_completed(futures):
            rows, provider_name = future.result()
            if rows > 0:
                return rows, provider_name
    return 0, None


# ── 通用同步逻辑（sync_universe / sync_whitelist 共用）────────────────────────


def _run_sync(
    tickers: list[str],
    date: str,
    lookback: int,
    workers: int,
    show_progress: bool,
    label: str = "股票",
) -> None:
    """通用的 K 线缓存同步逻辑（供 sync_universe / sync_whitelist 调用）。"""
    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = (end_date - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

    total = len(tickers)
    workers = min(workers, total)
    success_count = 0
    fail_tickers: list[str] = []
    provider_map: dict[str, str | None] = {}

    console.print(
        f"[bold green]🔥 {label} K 线缓存同步[/bold green] 股票数={total} 日期={start_date}~{date}",
    )

    # ── 检查 Provider 健康状态 ───────────────────────────────────────────────
    console.print("[bold cyan]🔍 检查数据线路健康状态...[/bold cyan]")
    health = _check_provider_health()
    health_table = Table(title="Provider 健康状态")
    health_table.add_column("Provider", style="cyan")
    health_table.add_column("状态", style="green")
    for name, ok in health.items():
        health_table.add_row(name, "✅ 可用" if ok else "❌ 不可用")
    console.print(health_table)

    from trade_krono_cli.data_providers.factory import get_data_factory

    factory = get_data_factory()
    ranked = factory.get_ranked_chain_for_ticker("sh.600519")
    usable_providers = [p for p in ranked if health.get(p, False)]
    logger.info(f"可用 Provider: {usable_providers}")

    # ── 并发拉取 ─────────────────────────────────────────────────────────────
    total = len(tickers)
    workers = min(workers, total)
    success_count = 0

    # 动态健康检查共享状态
    _health_state: dict = {
        "usable_providers": list(usable_providers),
        "last_check_time": time.monotonic(),
        "tickers_since_check": 0,
        "failure_counts": {},
        "lock": threading.Lock(),
    }

    def _health_monitor() -> None:
        """后台线程：定期重新检测 Provider 健康状态。"""
        while True:
            time.sleep(5.0)
            with _health_state["lock"]:
                elapsed = time.monotonic() - _health_state["last_check_time"]
                tickers_done = _health_state["tickers_since_check"]
            if (
                elapsed >= _HEALTH_CHECK_MIN_INTERVAL_SEC
                or tickers_done >= _HEALTH_CHECK_INTERVAL_TICKERS
            ):
                with _health_state["lock"]:
                    _health_state["last_check_time"] = time.monotonic()
                    _health_state["tickers_since_check"] = 0
                new_providers = _update_usable_providers(
                    _health_state["usable_providers"],
                    _health_state["usable_providers"],
                    _health_state["failure_counts"],
                )
                with _health_state["lock"]:
                    _health_state["usable_providers"] = new_providers

    _monitor_thread = threading.Thread(target=_health_monitor, daemon=True)
    _monitor_thread.start()

    def _process(ticker: str) -> tuple[str, int, str | None]:
        _get_global_semaphore().acquire()
        try:
            # 随机小抖动，避免所有线程同时发起请求
            time.sleep(random.uniform(0, _BETWEEN_REQUEST_DELAY))
            with _health_state["lock"]:
                providers = list(_health_state["usable_providers"])
                _health_state["tickers_since_check"] += 1
            rows, provider_name = _fetch_ticker_parallel(
                ticker=ticker,
                start_date=start_date,
                end_date=date,
                frequency="d",
                adjustflag="1",
                use_cache=True,
                providers=providers,
            )
            return ticker, rows, provider_name
        finally:
            _get_global_semaphore().release()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, t): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                t, rows, provider_name = future.result()
                provider_map[ticker] = provider_name
                if rows > 0:
                    success_count += 1
                    if show_progress:
                        console.print(
                            f"  ✅ [{success_count}/{total}] {ticker} {rows}行 ← {provider_name or '?'}",
                            soft_wrap=True,
                        )
                else:
                    fail_tickers.append(ticker)
                    logger.warning(f"⚠️  {ticker} 无数据返回")
                    if show_progress:
                        console.print(
                            f"  ❌ [{success_count + len(fail_tickers)}/{total}] {ticker} 无数据",
                            soft_wrap=True,
                        )
            except Exception as e:
                fail_tickers.append(ticker)
                logger.debug(f"⚠️  {ticker} K 线拉取失败: {e}")
                if show_progress:
                    console.print(
                        f"  ❌ [{success_count + len(fail_tickers)}/{total}] {ticker} {str(e)[:40]}",
                        soft_wrap=True,
                    )

    console.print()

    console.print(
        f"[bold green]✅ 同步完成[/bold green] "
        f"成功={success_count}/{total}  失败={len(fail_tickers)}",
    )
    if fail_tickers:
        console.print(f"[yellow]⚠️  失败股票: {', '.join(fail_tickers)}[/yellow]")
        prov_counts: dict[str, int] = {}
        for p in provider_map.values():
            if p:
                prov_counts[p] = prov_counts.get(p, 0) + 1
        if prov_counts:
            console.print(
                f"[dim]Provider 分布: {', '.join(f'{k}:{v}' for k, v in sorted(prov_counts.items(), key=lambda x: -x[1]))}[/dim]"
            )
