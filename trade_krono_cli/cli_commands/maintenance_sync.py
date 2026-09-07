"""同步命令 — sync-universe / sync-whitelist。"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from trade_krono_cli.cli_commands.core import _load_env

console = Console()

# 单只股票 K 线拉取超时（秒）
_STOCK_FETCH_TIMEOUT: int = 30

# 每个 Provider 的最大并发请求数（防止被限流/拉黑）
# mootdx 实测限制：单 IP 约 10 并发；baostock 已被拉黑，仅作备用
_PROVIDER_CONCURRENCY: dict[str, int] = {
    "mootdx": 5,
    "baostock": 3,
    "akshare": 3,
    "tushare": 3,
    "tonghuashun": 3,
}

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
                    logger.info(f"Provider {p} 连续 {_HEALTH_RESTORE_THRESHOLD} 次健康检查通过，已恢复")
            else:
                failure_counts[p] = 0

    result = sorted(new_providers)
    if result != locked_providers:
        logger.info(f"Provider 状态变化: {locked_providers} → {result}")
        console.print(
            f"[dim]⚡ Provider 更新: {[p + ('✅' if p in result else '❌') for p in ranked]}[/dim]"
        )
    return result


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


def sync_universe(
    source: str = typer.Option(
        "tonghuashun",
        "--source",
        "-s",
        help="股票池来源：tonghuashun / mootdx / akshare（默认 tonghuashun）",
    ),
    date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--date",
        "-d",
        help="基准日期 YYYY-MM-DD（默认今天）",
    ),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（约 2 年）"),
    workers: int = typer.Option(
        16,
        "--workers",
        "-w",
        help="并发拉取线程数（默认 16）",
    ),
    show_progress: bool = typer.Option(
        True,
        "--no-progress",
        "-p",
        help="不显示进度条（静默模式）",
    ),
) -> None:
    """全量 A 股 K 线缓存同步。

    从指定 UniverseProvider 获取全市场 A 股列表，逐只拉取历史 K 线并写入缓存。
    首次运行拉取全量历史（全部永久缓存）；
    后续运行自动增量更新，仅拉取新增交易日数据。

    若配置了 SYNC_WHITELIST，白名单股票将优先拉取，随后再处理全量列表（白名单股票已从全量中剔除，避免重复）。

    示例：
      trade-krono-cli sync-universe                  # 用同花顺同步全部 A 股
      trade-krono-cli sync-universe --source mootdx  # 用 mootdx 同步
      trade-krono-cli sync-universe --lookback 1095  # 同步 3 年历史
    """
    _load_env()

    from trade_krono_cli.config import get_settings

    settings = get_settings()
    whitelist_tickers: list[str] = _resolve_tickers(settings.sync_whitelist)

    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = (end_date - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

    # ── 获取 A 股列表 ──────────────────────────────────────────────────────────
    if source == "tonghuashun":
        from trade_krono_cli.universe.provider import TongHuaShunUniverseProvider

        provider = TongHuaShunUniverseProvider()
        tickets = provider.get_universe()
        tickers = [t.ticker for t in tickets if t.ticker]
        logger.info(f"📋 同花顺 A 股列表: {len(tickers)} 只")
    else:
        from trade_krono_cli.universe.provider import get_universe_provider

        provider_other = get_universe_provider(source)
        if provider_other is None:
            console.print(f"[red]❌ 数据源 '{source}' 不可用，请检查配置[/red]")
            raise typer.Exit(1)
        tickets = provider_other.get_universe()
        tickers = [t.ticker for t in tickets if t.ticker]
        logger.info(f"📋 {source} A 股列表: {len(tickers)} 只")

    if not tickers:
        console.print("[red]❌ 无法获取 A 股列表，请检查数据源配置[/red]")
        raise typer.Exit(1)

    # ── 白名单优先：从全量中剔除白名单股票，避免重复拉取 ──────────────────────
    whitelist_set = set(whitelist_tickers)
    remaining_tickers = [t for t in tickers if t not in whitelist_set]
    ordered_tickers = whitelist_tickers + remaining_tickers
    if whitelist_tickers:
        logger.info(f"📌 白名单 {len(whitelist_tickers)} 只优先拉取")

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
    total = len(ordered_tickers)
    workers = min(workers, total)
    success_count = 0
    fail_tickers: list[str] = []
    row_counts: dict[str, int] = {}
    provider_map: dict[str, str | None] = {}

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
            if elapsed >= _HEALTH_CHECK_MIN_INTERVAL_SEC or tickers_done >= _HEALTH_CHECK_INTERVAL_TICKERS:
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
        # 全局限流：等待许可后再开始拉取
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
        futures = {pool.submit(_process, t): t for t in ordered_tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                t, rows, provider_name = future.result()
                row_counts[ticker] = rows
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
                    logger.warning(f"⚠️  {ticker} 无数据返回（所有 Provider 均失败）")
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
        console.print(f"[yellow]⚠️  失败股票（可稍后重试）: {', '.join(fail_tickers[:20])}[/yellow]")
        if len(fail_tickers) > 20:
            console.print(f"[dim]   … 还有 {len(fail_tickers) - 20} 只[/dim]")
        # 打印各 Provider 使用分布
        prov_counts: dict[str, int] = {}
        for p in provider_map.values():
            if p:
                prov_counts[p] = prov_counts.get(p, 0) + 1
        if prov_counts:
            console.print(f"[dim]Provider 分布: {', '.join(f'{k}:{v}' for k, v in sorted(prov_counts.items(), key=lambda x: -x[1]))}[/dim]")

    # ── 自动导出 daily_pv 供 RD-Agent 使用 ─────────────────────────────
    try:
        from pathlib import Path as _P

        from trade_krono_cli.cache import get_cache

        cache = get_cache()
        rdagent_data = _P(__file__).resolve().parents[3] / "RD-Agent-Work" / "git_ignore_folder"
        main_dir = rdagent_data / "factor_implementation_source_data"
        parquet_main = main_dir / "daily_pv.parquet"
        h5_main = main_dir / "daily_pv.h5"

        result = cache.export_daily_pv(
            parquet_path=str(parquet_main),
            h5_path=str(h5_main),
            debug_insts=0,  # 完整导出，不过滤股票数量
        )
        console.print(
            f"[bold green]✅ 已自动导出 daily_pv: {result['stocks']:,} 只, "
            f"{result['rows']:,} 行 ({result['date_min']} ~ {result['date_max']})[/bold green]"
        )
    except Exception as ex:
        logger.warning(f"自动导出 daily_pv 失败（不影响同步结果）: {ex}")


def sync_whitelist(
    date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--date",
        "-d",
        help="基准日期 YYYY-MM-DD（默认今天）",
    ),
    lookback: int = typer.Option(730, "--lookback", "-l", help="回溯天数，默认 730（约 2 年）"),
    workers: int = typer.Option(
        16,
        "--workers",
        "-w",
        help="并发拉取线程数（默认 16）",
    ),
    show_progress: bool = typer.Option(
        True,
        "--no-progress",
        "-p",
        help="不显示进度条（静默模式）",
    ),
) -> None:
    """仅同步白名单股票的 K 线缓存。

    白名单来自 .env 中的 SYNC_WHITELIST 配置（逗号分隔的6位股票代码）。
    若未配置 SYNC_WHITELIST，命令将报错退出。

    示例：
      trade-krono-cli sync-whitelist                    # 同步 .env 中配置的白名单
      trade-krono-cli sync-whitelist --lookback 1095    # 同步 3 年历史
      trade-krono-cli sync-whitelist --date 2026-08-30  # 指定基准日期
    """
    _load_env()

    from trade_krono_cli.config import get_settings

    settings = get_settings()
    whitelist_raw = settings.sync_whitelist.strip()
    if not whitelist_raw:
        console.print("[red]❌ 未配置 SYNC_WHITELIST，请在 .env 中设置白名单股票代码[/red]")
        raise typer.Exit(1)

    whitelist_tickers = _resolve_tickers(whitelist_raw)
    if not whitelist_tickers:
        console.print("[red]❌ 白名单解析后无有效股票，请检查 SYNC_WHITELIST 格式[/red]")
        raise typer.Exit(1)

    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = (end_date - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

    total = len(whitelist_tickers)
    workers = min(workers, total)
    success_count = 0
    fail_tickers: list[str] = []
    provider_map: dict[str, str | None] = {}

    console.print(
        f"[bold green]🔥 白名单 K 线缓存同步[/bold green] 股票数={total} 日期={start_date}~{date}",
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
    logger.info(f"白名单可用 Provider: {usable_providers}")

    # ── 并发拉取 ─────────────────────────────────────────────────────────────
    total = len(whitelist_tickers)
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
            if elapsed >= _HEALTH_CHECK_MIN_INTERVAL_SEC or tickers_done >= _HEALTH_CHECK_INTERVAL_TICKERS:
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
        futures = {pool.submit(_process, t): t for t in whitelist_tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                _, rows, provider_name = future.result()
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
            console.print(f"[dim]Provider 分布: {', '.join(f'{k}:{v}' for k, v in sorted(prov_counts.items(), key=lambda x: -x[1]))}[/dim]")


def rank_providers(
    ticker: str = typer.Option(
        "sh.600519",
        "--ticker",
        "-t",
        help="用于 benchmark 的代表性 ticker（默认 sh.600519 贵州茅台）",
    ),
    workers: int = typer.Option(3, "--workers", "-w", help="并发 benchmark 线程数（默认 3）"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新 benchmark，忽略缓存"),
) -> None:
    """Benchmark 所有数据源延迟，按速度排序输出。

    结果会缓存 10 分钟，后续 K 线拉取自动按此顺序尝试。
    北交所（bj.）ticker 会自动将 tonghuashun 置顶。

    示例：
      trade-krono-cli rank-providers                    # 用默认 ticker benchmark
      trade-krono-cli rank-providers -t bj.920001       # 测试北交所
      trade-krono-cli rank-providers --force            # 强制重新 benchmark
    """
    _load_env()

    from trade_krono_cli.data_providers.factory import get_data_factory

    factory = get_data_factory()
    ticker_type = ticker.split(".", maxsplit=1)[0] if "." in ticker else ticker

    console.print(f"[bold cyan]🔬 Provider Benchmark[/bold cyan] ticker={ticker} workers={workers}")
    console.print()

    if force:
        # 清除该 ticker 类型的缓存，强制重新 benchmark
        factory.invalidate_rank_cache(ticker_type)

    results = factory.bench_all(ticker=ticker, workers=workers)

    if not results:
        console.print("[red]❌ 没有可用的 Provider[/red]")
        raise typer.Exit(1)

    console.print()
    console.print("[bold]结果排名（越快越靠前）:[/bold]")
    console.print(f"  {'排名':<4s} {'Provider':<14s} {'延迟':<10s} {'状态':<6s}")
    console.print("  " + "-" * 38)

    for i, r in enumerate(results, 1):
        if r.success:
            latency_str = f"{r.latency_ms:.0f}ms"
            status_str = "✅"
        else:
            latency_str = "FAIL"
            status_str = "❌"
        console.print(f"  {i:<4d} {r.name:<14s} {latency_str:<10s} {status_str:<6s}")

    # 写入缓存，避免重复 benchmark
    ranked = factory.get_ranked_chain_for_ticker(ticker)
    console.print()
    console.print(f"[bold green]✅ 已缓存 Provider 排序[/bold green] {' → '.join(ranked)}")
    console.print("  缓存 TTL: 10 分钟，下次 benchmark 将在 10 分钟后生效")
