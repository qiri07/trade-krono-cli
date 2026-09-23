"""sync_helpers — 同步命令共享工具包。

子模块：
  semaphore — 信号量管理（Provider 并发控制）
  ticker    — 股票代码解析
  health    — Provider 健康检查与动态可用列表
  fetcher   — 并发 K 线拉取
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from loguru import logger
from rich.console import Console
from rich.table import Table

from trade_krono_cli.cli_commands.sync_helpers.fetcher import _fetch_ticker_parallel
from trade_krono_cli.cli_commands.sync_helpers.health import (
    _check_provider_health,
    _update_usable_providers,
)
from trade_krono_cli.cli_commands.sync_helpers.semaphore import (
    _BETWEEN_REQUEST_DELAY,
    _HEALTH_CHECK_INTERVAL_TICKERS,
    _HEALTH_CHECK_MIN_INTERVAL_SEC,
    _get_global_semaphore,
    _get_provider_semaphore,
)
from trade_krono_cli.cli_commands.sync_helpers.ticker import _resolve_tickers

__all__ = [
    "_check_provider_health",
    "_fetch_ticker_parallel",
    "_get_global_semaphore",
    "_get_provider_semaphore",
    "_HEALTH_CHECK_INTERVAL_TICKERS",
    "_HEALTH_CHECK_MIN_INTERVAL_SEC",
    "_HEALTH_REMOVE_THRESHOLD",
    "_HEALTH_RESTORE_THRESHOLD",
    "_resolve_tickers",
    "_run_sync",
    "_update_usable_providers",
    "console",
]

# Re-export thresholds from semaphore for backward compat
from trade_krono_cli.cli_commands.sync_helpers.semaphore import (  # noqa: F401
    _HEALTH_REMOVE_THRESHOLD,
    _HEALTH_RESTORE_THRESHOLD,
)

console = Console()


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

    if not usable_providers:
        logger.error("❌ 所有 Provider 均不可用，同步终止。请检查网络连接或数据源配置。")
        console.print("\n[red]❌ 同步失败：无可用数据源[/red]")
        return

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
        "stop_event": threading.Event(),
    }

    def _health_monitor() -> None:
        """后台线程：定期重新检测 Provider 健康状态。"""
        while not _health_state["stop_event"].wait(timeout=5.0):
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

    try:
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
    finally:
        _health_state["stop_event"].set()

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
