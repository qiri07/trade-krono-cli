"""fill_missing 选股 — CLI 入口。

主流程：补齐失败股票历史数据 + 今日增量同步。
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from scripts._utils import (
    get_provider_chain,
    get_sqlite_conn,
    is_etf_ticker,
)
from scripts.fill_missing.constants import CACHE_DB, FETCH_TIMEOUT, WORKERS
from scripts.fill_missing.dates import get_expected_date
from scripts.fill_missing.db_queries import (
    _has_sufficient_history,
    _has_today_data,
    get_failed_tickers,
)
from scripts.fill_missing.fetch import (
    fetch_full_range,
    fetch_today_incremental,
)
from scripts.fill_missing.plan import load_sync_plan
from scripts.fill_missing.progress import (
    _flush_completed,
    _load_completed_tickers,
    _load_failed_tickers,
    _mark_completed,
    _mark_failed,
)
from scripts.fill_missing.report import fetch_missing_report, print_missing_report
from trade_krono_cli.data_providers.factory import get_data_factory


def main(
    plan_path: str | None = None,
    dry_run: bool = False,
    no_incremental: bool = False,
    backup: bool = True,
) -> None:
    """主流程：补齐失败股票历史数据 + 今日增量同步。

    Args:
        plan_path: 补拉计划 JSON 路径（由 scripts/check_data_status.py 生成）；缺省时全库扫描。
        dry_run: 仅打印补拉清单，不写缓存。
        no_incremental: 禁用增量合并，改为全量覆盖（重拉完整历史段）。
        backup: 写入前是否自动备份核心数据文件（默认 True）。
    """
    # ── 前置步骤：数据安全备份（在任何写入操作前自动备份）─────────────────
    if backup and not dry_run:
        from scripts.backup_cache import backup_all

        backup_all(keep=10)

    # ── 第一步：检查当前缺失情况 ─────────────────────────────
    print_missing_report(fetch_missing_report())

    # 补拉来源：优先读计划 JSON，缺省回退到全库扫描
    if plan_path:
        plan_failed, plan_need_today = load_sync_plan(plan_path)
    else:
        plan_failed, plan_need_today = [], []

    # ETF 从本脚本剔除，统一由第 3 步 etf_history_sync.py 拉取
    # （tonghuashun 对多数 ETF 报 Unknown thscode、mootdx 不可达，在此逐只降级极慢）
    n_plan = len(plan_failed) + len(plan_need_today)
    plan_failed = [t for t in plan_failed if not is_etf_ticker(t)]
    plan_need_today = [t for t in plan_need_today if not is_etf_ticker(t)]
    if skipped := n_plan - len(plan_failed) - len(plan_need_today):
        print(f"ℹ️  剔除 {skipped} 只 ETF（由 etf_history_sync.py 统一拉取）")

    if dry_run:
        print("\n🧪 DRY-RUN 模式：仅打印补拉清单，不写缓存、不拉取数据")
        print(f"   将补齐历史: {len(plan_failed) if plan_path else '待全库扫描'} 只")
        print(f"   将补今日增量: {len(plan_need_today) if plan_path else '待全库扫描'} 只")
        return

    factory = get_data_factory()

    # 加载已完成和失败进度（断点续传）
    completed_tickers: set[str] = _load_completed_tickers()
    failed_tickers_cache: set[str] = _load_failed_tickers()
    if completed_tickers:
        print(f"📍 检测到已完成进度：{len(completed_tickers)} 只股票已成功拉取，将跳过")
    if failed_tickers_cache:
        print(f"📍 检测到失败进度：{len(failed_tickers_cache)} 只股票曾拉取失败，将重试")

    # ── 第二步：补齐失败股票的历史数据 ─────────────────────
    print("=" * 60)
    print("📌 第二步：补齐失败股票的历史数据")
    print("=" * 60)
    failed_tickers = plan_failed if plan_path else get_failed_tickers()
    failed_before = len(failed_tickers)
    # 增量跳过：跳过已有足够历史数据的 ticker（避免中断重启后重复拉取）

    failed_tickers = [t for t in failed_tickers if not _has_sufficient_history(t)]
    # 断点续传：跳过已在进度文件中标记完成的 ticker
    failed_tickers = [t for t in failed_tickers if t not in completed_tickers]
    # 将之前标记失败的 ticker 加入重试列表（去重）
    retry_failed = [
        t for t in failed_tickers_cache if t not in completed_tickers and t not in failed_tickers
    ]
    if retry_failed:
        failed_tickers.extend(retry_failed)
        print(f"ℹ️  加入 {len(retry_failed)} 只之前失败的股票进行重试")
    if failed_before > len(failed_tickers) - len(retry_failed):
        print(f"ℹ️  跳过 {failed_before - len(failed_tickers) + len(retry_failed)} 只已完成的股票")
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
                    _mark_failed(ticker, completed_tickers, failed_tickers_cache)
                    print(f"  ⏱️  {i}/{len(failed_tickers)} 超时: {ticker}")
                elif provider == "failed":
                    failed_13.append((ticker, detail))
                    _mark_failed(ticker, completed_tickers, failed_tickers_cache)
                    print(f"  ❌ {i}/{len(failed_tickers)} 失败: {ticker} ({detail[:60]})")
                else:
                    success_13 += 1
                    _mark_completed(ticker, completed_tickers, failed_tickers_cache)
                    print(
                        f"  ✅ {i}/{len(failed_tickers)} {ticker}: {provider} 拉取 {n_rows} 条 {detail}"
                    )
            except Exception as e:
                failed_13.append((ticker, str(e)))
                _mark_failed(ticker, completed_tickers, failed_tickers_cache)
                print(f"  ❌ {i}/{len(failed_tickers)} 异常: {ticker} ({e})")
    print(
        f"\n第二步结果: 成功 {success_13}/{len(failed_tickers)}, 失败 {len(failed_13)}/{len(failed_tickers)}"
    )
    if failed_13:
        print(f"   仍失败: {[t for t, _ in failed_13]}")
    print()
    _flush_completed(completed_tickers, failed_tickers_cache)

    # ── 第三步：检查哪些股票缺少今日数据 ───────────────────────
    print("=" * 60)
    print(f"📌 第三步：同步今日（{get_expected_date()}）增量数据")
    print("=" * 60)

    today = get_expected_date()
    # 动态查询数据库：找出所有缺少今日数据的股票（含北交所，ETF 单独脚本处理）
    conn = get_sqlite_conn(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT IN (
            SELECT DISTINCT ticker FROM kline_cache
            WHERE end >= '{today}'
        )
        ORDER BY ticker
    """
    )
    need_today_from_db = [r[0] for r in cur.fetchall() if not is_etf_ticker(r[0])]
    conn.close()

    # 合并计划中的 need_today 与数据库动态查询结果（取并集，避免计划缺失导致漏补）
    need_today_set = set(need_today_from_db)
    if plan_path:
        need_today_set.update(plan_need_today)
    need_today = sorted(need_today_set)

    # 增量跳过：跳过已有今日数据的 ticker（双重保险，SQL 查询已排除但保留兜底）
    need_today_before = len(need_today)

    need_today = [t for t in need_today if not _has_today_data(t, today)]
    # 断点续传：仅在非 plan 模式下跳过已在进度文件中标记完成的 ticker；
    # plan 模式下 need_today 已是当前缺少今日数据的股票，不应用历史完成记录过滤
    if not plan_path:
        need_today = [t for t in need_today if t not in completed_tickers]
    if need_today_before > len(need_today):
        print(f"ℹ️  跳过 {need_today_before - len(need_today)} 只已完成的股票")
    plan_src = (
        f"（来自补拉计划 {len(plan_need_today)} 只 + 数据库查询 {len(need_today_from_db)} 只，合并去重 {len(need_today)} 只）"
        if plan_path
        else "（含北交所，数据库查询）"
    )
    print(f"需要补今日数据的股票: {len(need_today)} 只 {plan_src}")
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
                        _mark_failed(ticker, completed_tickers, failed_tickers_cache)
                    else:
                        success_today += 1
                        _mark_completed(ticker, completed_tickers, failed_tickers_cache)
                except Exception as e:
                    failed_today.append((ticker, str(e)))
                    _mark_failed(ticker, completed_tickers, failed_tickers_cache)
            time.sleep(1)  # 短暂休息避免API限流

    print(
        f"\n第三步结果: 成功 {success_today}/{len(need_today)}, 失败 {len(failed_today)}/{len(need_today)}"
    )
    if failed_today:
        print(f"   仍失败: {[t for t, _ in failed_today]}")
    print()
    _flush_completed(completed_tickers, failed_tickers_cache)

    # ── 第四步：最终统计 ───────────────────────────────────────
    print("=" * 60)
    print("📌 第四步：最终数据统计")
    print("=" * 60)
    conn = get_sqlite_conn(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kline_cache")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    tickers = cur.fetchone()[0]
    cur.execute("SELECT MAX(end) FROM kline_cache")
    latest = cur.fetchone()[0]
    today = get_expected_date()
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache WHERE end >= ?", (today,))
    has_today = cur.fetchone()[0]
    cur.execute(
        """
        SELECT COUNT(DISTINCT ticker) FROM kline_cache
        WHERE start <= '2020-12-31' AND end >= '2020-01-01'
    """
    )
    has_2020 = cur.fetchone()[0]
    conn.close()

    print(f"  总记录数:   {total}")
    print(f"  股票数:     {tickers}")
    print(f"  最新日期:   {latest}")
    print(f"  今日数据:   {has_today}/{tickers} ({has_today * 100 / max(tickers, 1):.1f}%)")
    print(f"  2020年数据: {has_2020}/{tickers} ({has_2020 * 100 / max(tickers, 1):.1f}%)")
    print("=" * 60)
    print("✅ 全部完成")

    # ── 写入今日增量结果文件（供 flow_runner 判断是否启动后续选股步骤）─────
    sync_result_file = Path("outputs/results/daily_sync_result.json")
    sync_result_file.parent.mkdir(parents=True, exist_ok=True)
    sync_result = {
        "date": today,
        "today_increment_count": success_today,
        "total_processed": len(need_today),
        "success_count": success_today,
        "failed_count": len(failed_today),
        "total_db_records": total,
        "total_db_tickers": tickers,
        "has_today_data": has_today,
        "latest_date": latest,
    }
    sync_result_file.write_text(
        json.dumps(sync_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"💾 今日增量同步结果已写入: {sync_result_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补齐失败股票历史数据 + 今日增量同步")
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
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="跳过写入前的自动备份（默认会备份 pipeline_cache.db / metadata.json）",
    )
    args = parser.parse_args()
    main(
        plan_path=args.plan,
        dry_run=args.dry_run,
        no_incremental=args.no_incremental,
        backup=not args.no_backup,
    )
