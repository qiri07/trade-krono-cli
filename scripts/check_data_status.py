#!/usr/bin/env python3
"""数据现状检查 + 补拉计划生成。

检查当前 K 线缓存 / 参考数据（metadata.json）的新鲜度，产出结构化补拉计划
JSON 供 fill_missing_and_today.py --plan 读取，决定「补拉哪些票、是否覆盖」。

用法：
  uv run python scripts/check_data_status.py
  uv run python scripts/check_data_status.py --ttl-hours 48
  uv run python scripts/check_data_status.py --no-integrity-report

输出：
  outputs/results/sync_plan_<YYYYMMDD>.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._utils import FAILED_MIN_RECORDS, _get_expected_date, get_cache_db_path
from scripts.fetch_reference_data import (
    METADATA_FILE,
    metadata_age_hours,
)

CACHE_DB = get_cache_db_path()
RESULTS_DIR = Path("outputs/results")


def _query_kline_status(today: str, expected_date: str) -> dict:
    """查询 K 线缓存现状，返回 kline 段数据。"""
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM kline_cache")
    total_records = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT ticker) FROM kline_cache")
    total_tickers = cur.fetchone()[0]

    cur.execute("SELECT MAX(end) FROM kline_cache")
    latest_date = cur.fetchone()[0] or ""

    cur.execute(
        f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT LIKE 'bj.%'
          AND (SELECT COUNT(*) FROM kline_cache k2 WHERE k2.ticker = kline_cache.ticker) < {FAILED_MIN_RECORDS}
        ORDER BY ticker
    """
    )
    failed_tickers = [r[0] for r in cur.fetchall()]

    cur.execute(f"""
        SELECT DISTINCT ticker FROM kline_cache
        WHERE ticker NOT IN (
            SELECT DISTINCT ticker FROM kline_cache
            WHERE end >= '{today}'
        )
        AND ticker NOT LIKE 'bj.%'
        ORDER BY ticker
    """)
    need_today_tickers = [r[0] for r in cur.fetchall()]

    conn.close()

    is_up_to_date = latest_date >= expected_date
    return {
        "total_records": total_records,
        "total_tickers": total_tickers,
        "latest_date": latest_date,
        "expected_date": expected_date,
        "is_up_to_date": is_up_to_date,
        "failed_tickers": failed_tickers,
        "need_today_tickers": need_today_tickers,
    }


def _query_metadata_status(ttl_hours: float) -> dict:
    """查询 metadata.json 现状，返回 metadata 段数据。"""
    entries = 0
    if METADATA_FILE.exists():
        try:
            entries = len(json.loads(METADATA_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            entries = 0
    age_hours = metadata_age_hours()
    is_stale = age_hours is None or age_hours >= ttl_hours
    needs_full_refresh = entries < 100 or is_stale
    return {
        "file": str(METADATA_FILE),
        "entries": entries,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "ttl_hours": ttl_hours,
        "is_stale": is_stale,
        "needs_full_refresh": needs_full_refresh,
    }


def build_sync_plan(ttl_hours: float = 24.0) -> dict:
    """生成补拉计划 dict，供 save_plan 序列化。"""
    today = datetime.now().strftime("%Y-%m-%d")
    expected_date = _get_expected_date()

    kline = _query_kline_status(today, expected_date)
    metadata = _query_metadata_status(ttl_hours)

    # 补拉计划：K 线侧（历史失败 + 缺今日）
    fetch_kline_tickers: list[str] = list(
        dict.fromkeys(kline["failed_tickers"] + kline["need_today_tickers"])
    )

    # 参考数据侧：全量刷新 vs 部分合并 vs 跳过
    if metadata["needs_full_refresh"]:
        metadata_mode = "full"
        metadata_tickers: list[str] = []
    elif metadata["is_stale"]:
        metadata_mode = "partial"
        metadata_tickers = []
    else:
        metadata_mode = "skip"
        metadata_tickers = []

    plan = {
        "generated_at": datetime.now().isoformat(),
        "kline": kline,
        "metadata": metadata,
        "plan": {
            "fetch_kline": {
                "tickers": fetch_kline_tickers,
                "reasons": {
                    "failed": kline["failed_tickers"],
                    "need_today": kline["need_today_tickers"],
                },
            },
            "fetch_metadata": {
                "mode": metadata_mode,
                "tickers": metadata_tickers,
            },
        },
    }
    return plan


def save_plan(plan: dict, out_dir: Path | None = None) -> Path:
    """将补拉计划写入 outputs/results/sync_plan_<YYYYMMDD>.json，返回路径。"""
    out_dir = out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d")
    path = out_dir / f"sync_plan_{date_tag}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_plan_summary(plan: dict) -> None:
    """打印人类可读的补拉计划摘要。"""
    kline = plan["kline"]
    meta = plan["metadata"]
    print("=" * 65)
    print("📊 数据现状检查报告")
    print(f"   生成时间: {plan['generated_at']}")
    print("=" * 65)
    print()
    print("【K 线缓存】")
    print(f"  总记录数:   {kline['total_records']}")
    print(f"  股票数:     {kline['total_tickers']}")
    print(f"  最新日期:   {kline['latest_date']}（期望 {kline['expected_date']}）")
    print(f"  是否最新:   {'✅' if kline['is_up_to_date'] else '❌'}")
    print(f"  历史失败:   {len(kline['failed_tickers'])} 只")
    print(f"  缺今日增量: {len(kline['need_today_tickers'])} 只")
    print()
    print("【参考数据 metadata.json】")
    print(f"  路径:       {meta['file']}")
    print(f"  条目数:     {meta['entries']}")
    age_str = f"{meta['age_hours']:.1f}h" if meta["age_hours"] is not None else "不存在"
    print(f"  距上次更新: {age_str}（TTL {meta['ttl_hours']:.0f}h）")
    print(f"  是否过期:   {'✅' if not meta['is_stale'] else '❌'}")
    print(f"  补拉计划:   K 线 {len(plan['plan']['fetch_kline']['tickers'])} 只，"
          f"参考数据 {plan['plan']['fetch_metadata']['mode']}")
    print("=" * 65)


def main(
    ttl_hours: float = 24.0,
    out_dir: Path | None = None,
    no_integrity_report: bool = False,
) -> Path:
    """检查数据现状并生成补拉计划 JSON，返回输出文件路径。"""
    # 可选：调现有 check_data_integrity 打印人类可读报告
    if not no_integrity_report:
        try:
            from scripts.check_data_integrity import main as integrity_main

            integrity_main()
        except Exception as e:
            print(f"⚠️  完整性报告生成失败（不影响补拉计划）: {e}")

    plan = build_sync_plan(ttl_hours=ttl_hours)
    out_path = save_plan(plan, out_dir=out_dir)
    print_plan_summary(plan)
    print(f"\n✅ 补拉计划已写入: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据现状检查 + 补拉计划生成")
    parser.add_argument(
        "--ttl-hours",
        type=float,
        default=24.0,
        help="metadata.json 刷新阈值（小时），默认 24",
    )
    parser.add_argument(
        "--no-integrity-report",
        action="store_true",
        help="跳过 check_data_integrity 人类可读报告，仅生成补拉计划",
    )
    args = parser.parse_args()
    main(
        ttl_hours=args.ttl_hours,
        no_integrity_report=args.no_integrity_report,
    )
