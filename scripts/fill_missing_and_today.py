#!/usr/bin/env python3
"""补齐失败股票历史数据 + 今日增量同步"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from scripts._utils import FAILED_MIN_RECORDS, get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

START_DATE = "2020-01-01"
WORKERS = 8
FETCH_TIMEOUT = 45
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

_load_env()

# 首次运行时缓存今日日期，避免跨批次日期变化
_today: str | None = None


def get_today_date() -> str:
    """动态获取今日日期（YYYY-MM-DD 格式），并缓存供后续调用复用。"""
    global _today
    if _today is None:
        _today = time.strftime("%Y-%m-%d")
    return _today


def load_sync_plan(plan_path: str | Path) -> tuple[list[str], list[str]]:
    """读取 check_data_status.py 产出的补拉计划 JSON，返回 (failed, need_today)。

    计划文件结构：
        {"plan": {"fetch_kline": {"tickers": [...],
                                   "reasons": {"failed": [...], "need_today": [...]}}}}

    Args:
        plan_path: 计划 JSON 文件路径。

    Returns:
        (failed_tickers, need_today_tickers) 两个列表；文件缺失或字段缺省对应列表为空。
    """
    import json

    path = Path(plan_path)
    if not path.exists():
        print(f"⚠️  补拉计划文件不存在: {path}，回退到全库扫描")
        return [], []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"⚠️  补拉计划 JSON 解析失败: {e}，回退到全库扫描")
        return [], []

    fetch = (data.get("plan") or {}).get("fetch_kline") or {}
    reasons = fetch.get("reasons") or {}
    failed = [t for t in (reasons.get("failed") or []) if isinstance(t, str)]
    need_today = [t for t in (reasons.get("need_today") or []) if isinstance(t, str)]
    if not failed and not need_today:
        # reasons 缺省时直接用顶层 tickers 列表（全部视为需补今日）
        fallback = [t for t in (fetch.get("tickers") or []) if isinstance(t, str)]
        need_today = fallback
    print(
        f"📋 补拉计划: 历史失败 {len(failed)} 只, 需补今日 {len(need_today)} 只"
        f"（来源: {path.name}）"
    )
    return failed, need_today


def get_failed_tickers() -> list[str]:
    """动态查询 DB 中记录数过少的股票（全量同步失败或未补齐的历史数据）。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    # 记录条数 < FAILED_MIN_RECORDS 说明历史数据严重不足，视为同步失败
    cur.execute(f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT LIKE 'bj.%'
          AND (SELECT COUNT(*) FROM kline_cache k2 WHERE k2.ticker = kline_cache.ticker) < {FAILED_MIN_RECORDS}
        ORDER BY ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    if tickers:
        return tickers
    # 兼容：若无不足记录，返回空列表（后续步骤只跑今日增量）
    return []


def get_today_only_tickers() -> list[str]:
    """获取只有今日数据的非北交所股票（需要补全历史）。"""
    import sqlite3

    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    today = get_today_date()
    cur.execute(f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE start = '{today}' AND end = '{today}'
          AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def fetch_full_range(factory, ticker: str, provider_chain: list[str]) -> tuple[str, int, str]:
    """带超时的全量历史数据拉取。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch():
        for provider_name in provider_chain:
            try:
                provider = factory.get_provider(provider_name)
                if provider is None:
                    continue
                result = provider.fetch_kline(
                    ticker, START_DATE, get_today_date(), frequency="d", adjustflag="1"
                )
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
            except Exception as e:
                error_container.append(f"{provider_name}:{e}")
                continue

    thread = Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ("timeout", 0, f"超时{FETCH_TIMEOUT}s")
    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def fetch_today_incremental(
    factory, ticker: str, provider_chain: list[str]
) -> tuple[str, int, str]:
    """拉取今日增量数据。"""
    result_container: list[tuple[str, int, str]] = []
    error_container: list[str] = []

    def _fetch():
        for provider_name in provider_chain:
            try:
                provider = factory.get_provider(provider_name)
                if provider is None:
                    continue
                result = provider.fetch_kline(
                    ticker, get_today_date(), get_today_date(), frequency="d", adjustflag="1"
                )
                if result is None or result.is_empty:
                    continue
                df = result.to_dataframe()
                if len(df) == 0:
                    continue
                cache = get_cache()
                ts = pd.to_datetime(df["timestamps"])
                # 把今日 K 线追加到现有历史行，而非新建独立的今日行：
                # 读取该 ticker 从 START_DATE 起至今的全部 K 线（含历史行与已有今日行），
                # 与今日数据合并去重后写回单条记录。
                combined = df
                existing = cache.get_kline(ticker, START_DATE, get_today_date(), "d", "1")
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
                        len(df),
                        f"{ts.min().strftime('%Y-%m-%d')}~{ts.max().strftime('%Y-%m-%d')}",
                    )
                )
                return
            except Exception as e:
                error_container.append(f"{provider_name}:{e}")
                continue

    thread = Thread(target=_fetch, daemon=True)
    thread.start()
    thread.join(timeout=FETCH_TIMEOUT)

    if thread.is_alive():
        return ("timeout", 0, f"超时{FETCH_TIMEOUT}s")
    if result_container:
        return result_container[0]
    return ("failed", 0, "; ".join(error_container) if error_container else "无结果")


def fetch_missing_report() -> dict:
    """查询当前缺失情况，返回报告字典。

    Returns:
        dict with keys:
            failed: list[str] — 记录数 < FAILED_MIN_RECORDS 的非北交所 ticker
            need_today: list[str] — 缺少今日 K 线的非北交所 ticker
            total: int — 总记录数
            tickers: int — 总股票数
            latest: str — 最新数据日期
            has_today: int — 已有今日数据的股票数
            has_2020: int — 已有 2020 年数据的股票数
            today: str — 今日日期
    """
    import sqlite3

    today = get_today_date()
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT LIKE 'bj.%'
          AND (SELECT COUNT(*) FROM kline_cache k2 WHERE k2.ticker = kline_cache.ticker) < {FAILED_MIN_RECORDS}
        ORDER BY ticker
    """
    )
    failed = [r[0] for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT IN (
            SELECT DISTINCT ticker FROM kline_cache
            WHERE end >= '{today}'
        )
        AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """
    )
    need_today = [r[0] for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM kline_cache")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    tickers = cur.fetchone()[0]
    cur.execute("SELECT MAX(end) FROM kline_cache")
    latest = cur.fetchone()[0] or ""
    cur.execute(f"SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE end >= '{today}'")
    has_today = cur.fetchone()[0]
    cur.execute(
        """
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """
    )
    has_2020 = cur.fetchone()[0]
    conn.close()

    return {
        "failed": failed,
        "need_today": need_today,
        "total": total,
        "tickers": tickers,
        "latest": latest,
        "has_today": has_today,
        "has_2020": has_2020,
        "today": today,
    }


def print_missing_report(report: dict) -> None:
    """打印缺失情况报告。"""
    print("=" * 60)
    print(f"📊 缺失检查报告（{report['today']}）")
    print("=" * 60)
    print(f"  总记录数:   {report['total']}")
    print(f"  股票数:     {report['tickers']}")
    print(f"  最新日期:   {report['latest']}")
    print(
        f"  今日数据:   {report['has_today']}/{report['tickers']}"
        f" ({report['has_today'] * 100 / max(report['tickers'], 1):.1f}%)"
    )
    print(
        f"  2020年数据: {report['has_2020']}/{report['tickers']}"
        f" ({report['has_2020'] * 100 / max(report['tickers'], 1):.1f}%)"
    )

    print()
    print(f"  需补全历史: {len(report['failed'])} 只")
    if report["failed"]:
        shown = report["failed"][:10]
        suffix = f" ... 等 {len(report['failed'])} 只" if len(report["failed"]) > 10 else ""
        print(f"    {', '.join(shown)}{suffix}")
    else:
        print("    无")

    print(f"  需补今日:   {len(report['need_today'])} 只")
    if report["need_today"]:
        shown = report["need_today"][:10]
        suffix = f" ... 等 {len(report['need_today'])} 只" if len(report["need_today"]) > 10 else ""
        print(f"    {', '.join(shown)}{suffix}")
    else:
        print("    无")
    print("=" * 60)


def main(
    plan_path: str | None = None,
    dry_run: bool = False,
    no_incremental: bool = False,
) -> None:
    # ── 第零步：检查当前缺失情况 ─────────────────────────────
    print_missing_report(fetch_missing_report())

    # 补拉来源：优先读计划 JSON，缺省回退到全库扫描
    if plan_path:
        plan_failed, plan_need_today = load_sync_plan(plan_path)
    else:
        plan_failed, plan_need_today = [], []

    if dry_run:
        print("\n🧪 DRY-RUN 模式：仅打印补拉清单，不写缓存、不拉取数据")
        print(f"   将补齐历史: {len(plan_failed) if plan_path else '待全库扫描'} 只")
        print(f"   将补今日增量: {len(plan_need_today) if plan_path else '待全库扫描'} 只")
        return

    factory = get_data_factory()

    # ── 第一步：补齐失败股票的历史数据 ─────────────────────
    print("=" * 60)
    print("📌 第一步：补齐失败股票的历史数据")
    print("=" * 60)
    failed_tickers = plan_failed if plan_path else get_failed_tickers()
    success_13 = 0
    failed_13 = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(fetch_full_range, factory, t, get_provider_chain(t)): t
            for t in failed_tickers
        }
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                if provider == "timeout":
                    failed_13.append((ticker, f"超时 {FETCH_TIMEOUT}s"))
                    print(f"  ⏱️  {i}/{len(failed_tickers)} 超时: {ticker}")
                elif provider == "failed":
                    failed_13.append((ticker, detail))
                    print(f"  ❌ {i}/{len(failed_tickers)} 失败: {ticker} ({detail[:60]})")
                else:
                    success_13 += 1
                    print(
                        f"  ✅ {i}/{len(failed_tickers)} {ticker}: {provider} 拉取 {n_rows} 条 {detail}"
                    )
            except Exception as e:
                failed_13.append((ticker, str(e)))
                print(f"  ❌ {i}/{len(failed_tickers)} 异常: {ticker} ({e})")
    print(
        f"\n第一步结果: 成功 {success_13}/{len(failed_tickers)}, 失败 {len(failed_13)}/{len(failed_tickers)}"
    )
    if failed_13:
        print(f"   仍失败: {[t for t, _ in failed_13]}")
    print()

    # ── 第二步：检查哪些股票缺少今日数据 ───────────────────────
    print("=" * 60)
    print(f"📌 第二步：同步今日（{get_today_date()}）增量数据")
    print("=" * 60)
    import sqlite3

    if plan_path:
        need_today = plan_need_today
    else:
        today = get_today_date()
        conn = sqlite3.connect(str(CACHE_DB))
        cur = conn.cursor()
        cur.execute(f"""
            SELECT DISTINCT ticker FROM kline_cache
            WHERE ticker NOT IN (
                SELECT DISTINCT ticker FROM kline_cache
                WHERE end >= '{today}'
            )
            AND ticker NOT LIKE 'bj.%'
            ORDER BY ticker
        """)
        need_today = [r[0] for r in cur.fetchall()]
        conn.close()
    print(
        f"需要补今日数据的股票: {len(need_today)} 只"
        + ("（来自补拉计划）" if plan_path else "（非北交所）")
    )
    print()

    # 分批同步今日增量
    success_today = 0
    failed_today = []
    batch_size = 50
    # 增量合并（默认）：仅追加最新 K 线到现有记录，不覆盖历史
    # no_incremental（全量覆盖）：重拉完整历史段，覆盖该 ticker 的 K 线记录
    for batch_start in range(0, len(need_today), batch_size):
        batch = need_today[batch_start : batch_start + batch_size]
        mode_label = "全量覆盖" if no_incremental else "增量合并"
        print(
            f"  批次 [{batch_start + 1}~{batch_start + len(batch)}/{len(need_today)}]（{mode_label}）"
        )
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            if no_incremental:
                futures = {
                    executor.submit(fetch_full_range, factory, t, get_provider_chain(t)): t
                    for t in batch
                }
            else:
                futures = {
                    executor.submit(fetch_today_incremental, factory, t, get_provider_chain(t)): t
                    for t in batch
                }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    provider, n_rows, detail = future.result(timeout=FETCH_TIMEOUT + 5)
                    if provider in ("timeout", "failed"):
                        failed_today.append((ticker, detail))
                    else:
                        success_today += 1
                except Exception as e:
                    failed_today.append((ticker, str(e)))
        time.sleep(1)  # 短暂休息避免API限流

    print(
        f"\n第二步结果: 成功 {success_today}/{len(need_today)}, 失败 {len(failed_today)}/{len(need_today)}"
    )
    if failed_today:
        print(f"   仍失败: {[t for t, _ in failed_today]}")
    print()

    # ── 第三步：最终统计 ───────────────────────────────────────
    print("=" * 60)
    print("📌 第三步：最终数据统计")
    print("=" * 60)
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kline_cache")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    tickers = cur.fetchone()[0]
    cur.execute("SELECT MAX(end) FROM kline_cache")
    latest = cur.fetchone()[0]
    today = get_today_date()
    cur.execute(f"SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE end >= '{today}'")
    has_today = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """)
    has_2020 = cur.fetchone()[0]
    conn.close()

    print(f"  总记录数:   {total}")
    print(f"  股票数:     {tickers}")
    print(f"  最新日期:   {latest}")
    print(f"  今日数据:   {has_today}/{tickers} ({has_today * 100 / tickers:.1f}%)")
    print(f"  2020年数据: {has_2020}/{tickers} ({has_2020 * 100 / tickers:.1f}%)")
    print("=" * 60)
    print("✅ 全部完成")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="补齐失败股票历史数据 + 今日增量同步"
    )
    parser.add_argument(
        "--plan",
        default=None,
        help="补拉计划 JSON 路径（由 scripts/check_data_status.py 生成）；缺省时全库扫描",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印补拉清单，不写缓存",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="禁用增量合并，改为全量覆盖（重拉完整历史段）",
    )
    args = parser.parse_args()
    main(
        plan_path=args.plan,
        dry_run=args.dry_run,
        no_incremental=args.no_incremental,
    )
