"""fill_missing 选股 — 缺失报告。

包含 fetch_missing_report / print_missing_report，用于查询当前缺失情况并打印报告。
"""

from __future__ import annotations

from scripts._utils import get_sqlite_conn
from scripts.fill_missing.constants import CACHE_DB, FAILED_MIN_RECORDS
from scripts.fill_missing.dates import get_expected_date


def fetch_missing_report() -> dict:
    """查询当前缺失情况，返回报告字典。

    Returns:
        dict with keys:
            failed: list[str] — 记录数 < FAILED_MIN_RECORDS 的非北交所 ticker
            need_today: list[str] — 缺少今日 K 线的 ticker（含北交所）
            total: int — 总记录数
            tickers: int — 总股票数
            latest: str — 最新数据日期
            has_today: int — 已有今日数据的股票数
            has_2020: int — 已有 2020 年数据的股票数
            today: str — 期望交易日（收盘后为当日，盘中为上一交易日）
    """
    today = get_expected_date()
    conn = get_sqlite_conn(CACHE_DB)
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
