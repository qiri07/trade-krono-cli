"""每日数据缓存检查与同步脚本（CLI 入口）。

功能：
  1. 检查 K 线缓存是否已更新至预期日期（复用 scripts._utils 检查逻辑）
  2. 若未更新，自动执行增量同步（通过 trade-krono-cli sync-universe CLI）

触发方式：
  - cron 定时任务（每天 10:00 和 15:30）
  - 手动运行：python scripts/check_and_sync_cache.py

用法：
  python scripts/check_and_sync_cache.py [--dry-run] [--source mootdx]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

# 确保脚本所在目录的父目录（仓库根）在 sys.path 中，
# 使 from scripts.xxx 模式在直接执行时也能正常工作
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DB = PROJECT_ROOT / "outputs" / "cache" / "pipeline_cache.db"

from scripts._utils import get_sqlite_conn, is_bse_or_hk, is_etf_ticker  # noqa: E402

# 收盘 + 数据落库缓冲时间：此刻之后当日 K 线应已可获取
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# 按 kline_cache.end 判定滞后时的分类顺序
STALE_CATEGORIES: tuple[str, ...] = ("A股", "ETF", "北交所", "港股")


def _stale_category(ticker: str) -> str:
    """将 ticker 归入滞后统计类别（A股 / ETF / 北交所 / 港股）。"""
    if ticker.startswith("hk."):
        return "港股"
    if ticker.startswith("bj.") or is_bse_or_hk(ticker):
        return "北交所"
    if is_etf_ticker(ticker):
        return "ETF"
    return "A股"


# 公共别名（向后兼容，供 sync_stale.py 和测试使用）
# 注意：以下为本地实现（非 _utils 别名），以便测试可 patch 模块级 datetime/CACHE_DB。
def get_expected_date() -> str:
    """获取期望的缓存日期（此刻应已落库的最近交易日）。

    收盘后（>= 15:30）期望当日数据；盘中或收盘前期望上一交易日。
    周末回退到最近的周五。

    Returns:
        期望日期字符串（YYYY-MM-DD）。
    """
    today = datetime.now()
    is_after_close = (today.hour, today.minute) >= (
        MARKET_CLOSE_HOUR,
        MARKET_CLOSE_MINUTE,
    )
    expected = today.date()
    if expected.weekday() >= 5:  # 5=周六, 6=周日 → 回退到周五
        expected -= timedelta(days=expected.weekday() - 4)
    elif not is_after_close:  # 盘中/凌晨：当日数据尚未落库
        expected -= timedelta(days=1)
        while expected.weekday() >= 5:
            expected -= timedelta(days=1)
    return expected.strftime("%Y-%m-%d")


def get_stale_tickers(expected_date: str) -> dict[str, list[str]]:
    """按类别返回未更新至 ``expected_date`` 的 ticker 清单。

    以 ``kline_cache.end`` 列（写入时记录的数据区间末日期）为判断依据，
    可发现「全库 MAX(end) 已达标、但部分标的仍滞后」的情况。

    Args:
        expected_date: 期望日期（YYYY-MM-DD）。

    Returns:
        ``{类别: [ticker, ...]}``，只包含存在滞后标的的类别；查询失败返回空 dict。
    """
    if not CACHE_DB.exists():
        return {}
    try:
        conn = get_sqlite_conn(CACHE_DB)
        rows = conn.execute("SELECT ticker, MAX(end) FROM kline_cache GROUP BY ticker").fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning(f"⚠️  查询滞后标的失败: {e}")
        return {}

    stale: dict[str, list[str]] = {cat: [] for cat in STALE_CATEGORIES}
    for ticker, end in rows:
        if end and end >= expected_date:
            continue
        stale[_stale_category(ticker)].append(ticker)
    return {cat: tickers for cat, tickers in stale.items() if tickers}


def get_cache_latest_date() -> str | None:
    """获取缓存中最新的交易日。"""
    if not CACHE_DB.exists():
        return None
    try:
        conn = get_sqlite_conn(CACHE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(end) FROM kline_cache")
        result = cursor.fetchone()[0]
        conn.close()
        return result
    except Exception as e:
        logger.warning(f"⚠️  获取缓存日期失败: {e}")
        return None


def is_cache_up_to_date(expected_date: str) -> bool:
    """检查缓存是否已更新至预期日期。"""
    latest = get_cache_latest_date()
    if latest is None:
        return False
    return latest >= expected_date


def run_sync(source: str = "tonghuashun", dry_run: bool = False) -> bool:
    """执行缓存同步（调用 trade-krono-cli sync-universe CLI）。"""
    # uv 是独立二进制，不能通过 python -m uv 调用
    uv_cmd = subprocess.run(
        ["uv", "--version"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    if uv_cmd.returncode != 0:
        # 尝试完整路径
        uv_paths = [
            Path.home() / ".local" / "bin" / "uv",
            Path("/usr/local/bin/uv"),
            Path("/usr/bin/uv"),
        ]
        for p in uv_paths:
            if p.exists():
                cmd = [
                    str(p),
                    "run",
                    "trade-krono-cli",
                    "sync-universe",
                    "--source",
                    source,
                    "--no-progress",
                ]
                break
        else:
            logger.error("无法找到 uv 二进制")
            return False
    else:
        cmd = ["uv", "run", "trade-krono-cli", "sync-universe", "--source", source, "--no-progress"]

    if dry_run:
        logger.info(f"[dry-run] 将执行: {' '.join(cmd)}")
        return True
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=7200)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("同步超时（>2h）")
        return False
    except Exception as e:
        logger.error(f"同步失败: {e}")
        return False


def run_sync_with_fallback(dry_run: bool = False) -> bool:
    """尝试多个数据源进行同步，按优先级依次尝试。"""
    sources = ["tonghuashun", "akshare", "mootdx"]
    for source in sources:
        logger.info(f"尝试数据源: {source}")
        if run_sync(source=source, dry_run=dry_run):
            logger.info(f"✅ 数据源 {source} 同步成功")
            return True
        logger.warning(f"⚠️  数据源 {source} 同步失败，尝试下一个...")
    logger.error("所有数据源同步均失败")
    return False


def run_targeted_repair(tickers: list[str], dry_run: bool = False) -> bool:
    """只对滞后的标的执行增量补拉（不触发全市场重同步）。

    通过生成临时补拉计划文件，复用 ``fill_missing_and_today.py`` 的逐票
    增量合并逻辑（自动按 provider 链降级、断点续传）。

    Args:
        tickers: 需要补拉的 ticker 列表（ETF 会被脚本自动剔除）。
        dry_run: 仅打印命令，不实际执行。

    Returns:
        True 表示补拉成功（子进程返回 0）。
    """
    non_etf = [t for t in tickers if not is_etf_ticker(t)]
    if not non_etf:
        logger.info("ℹ️  滞后标的全为 ETF，请运行 scripts/etf_history_sync.py 补拉")
        return True

    plan = {"plan": {"fetch_kline": {"reasons": {"need_today": non_etf}}}}
    with tempfile.NamedTemporaryFile(
        "w", suffix="_stale_plan.json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(plan, fh, ensure_ascii=False)
        plan_path = fh.name

    cmd = [
        "uv",
        "run",
        "python",
        "scripts/fill_missing_and_today.py",
        "--plan",
        plan_path,
        "--no-backup",
    ]
    logger.info(f"🔧 定向补拉 {len(non_etf)} 只滞后标的（计划文件 {plan_path}）")
    if dry_run:
        logger.info(f"[dry-run] 将执行: {' '.join(cmd)}")
        return True
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=3600)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("定向补拉超时（>1h）")
        return False
    except OSError as e:
        logger.error(f"定向补拉启动失败: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据缓存检查与同步")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行同步")
    parser.add_argument(
        "--source",
        default="tonghuashun",
        choices=["mootdx", "akshare", "tonghuashun"],
        help="数据源（默认 tonghuashun）",
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="即使全局最大日期已达标，也对滞后的个股执行增量补拉",
    )
    args = parser.parse_args()

    # 获取期望日期
    expected_date = get_expected_date()

    # 检查缓存状态
    latest_date = get_cache_latest_date()

    # 逐票检查：全库 MAX(end) 达标不代表所有标的都更新了（ETF/北交所常滞后）
    stale = get_stale_tickers(expected_date)
    stale_count = sum(len(v) for v in stale.values())

    if latest_date and latest_date >= expected_date and not stale_count:
        logger.info(f"✅ 缓存已更新至 {latest_date}，无需同步")
        return 0

    if stale_count:
        detail = "，".join(f"{cat} {len(tks)} 只" for cat, tks in stale.items())
        logger.warning(
            f"⚠️  缓存整体最新 {latest_date or '未知'}，但 {stale_count} 只标的"
            f"未更新至 {expected_date}（{detail}）"
        )
        for cat, tks in stale.items():
            logger.warning(f"   {cat} 滞后样例: {', '.join(tks[:8])}")

    if latest_date and latest_date >= expected_date and not args.force_sync:
        logger.info("   加 --force-sync 可对上述标的执行定向补拉")
        return 0

    if args.dry_run:
        return 0

    # 全局滞后 → 全量同步；仅个别标的滞后 → 定向补拉
    if latest_date and latest_date >= expected_date:
        repairs = [t for cat, tks in stale.items() if cat != "港股" for t in tks]
        return 0 if run_targeted_repair(repairs, dry_run=False) else 1

    # 执行同步（带多源 fallback）
    success = run_sync_with_fallback(dry_run=False)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
