#!/usr/bin/env python3
"""批量获取港股基本面元数据并写入本地 ``hk_metadata`` 表。

数据来源与刷新策略：

==================  ==========================================  =====================
字段类别            来源                                        TTL
==================  ==========================================  =====================
行情/估值/市值      ``akshare.stock_hk_spot_em`` 全市场快照      ``--spot-ttl-hours``(12h)
上市信息/港股通      ``stock_hk_security_profile_em``            ``--profile-ttl-days``(365d)
财务/分红           ``stock_financial_hk_analysis_indicator_em`` ``--financial-ttl-days``(30d)
                    ``stock_hk_financial_indicator_em``
                    ``stock_hk_dividend_payout_em``
==================  ==========================================  =====================

技术指标（成交额、回撤、波动率、换手率等）**不在此写入**，由
``scripts/hk_screen/data_fetcher.py`` 在读取时从本地 K 线实时计算，
避免把「当日值」误存成「12 个月均值」。

用法：
    uv run python scripts/fetch_hk_metadata.py                     # 全量（按 TTL 增量）
    uv run python scripts/fetch_hk_metadata.py --codes "00700,00941"
    uv run python scripts/fetch_hk_metadata.py --force             # 忽略 TTL 全量刷新
    uv run python scripts/fetch_hk_metadata.py --skip-financial    # 只刷行情快照
    uv run python scripts/fetch_hk_metadata.py --limit 20          # 只处理前 20 只（调试）
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any  # 快照行/财务记录值类型混杂，Any 为合理

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import get_cache_db_path, get_sqlite_conn
from scripts.hk_screen.constants import HK_METADATA_FINANCIAL_TTL_DAYS
from scripts.hk_screen.data_fetcher import get_hk_connect_stocks
from scripts.hk_screen.fundamentals import fetch_hk_fundamentals
from scripts.hk_screen.metadata_store import (
    ensure_hk_metadata_table,
    has_financial_data,
    has_profile_data,
    is_stale,
    is_stale_hours,
    read_hk_metadata,
    upsert_hk_metadata,
)
from trade_krono_cli.cli_commands.core import _load_env

CACHE_DB = get_cache_db_path()


def get_hk_ticker_list_from_cache() -> list[str]:
    """从本地 K 线缓存读取所有港股 ticker（按代码升序）。

    Returns:
        港股 ticker 列表，如 ``["hk.00700", ...]``。
    """
    if not CACHE_DB.exists():
        logger.error(f"❌ 缓存数据库不存在: {CACHE_DB}")
        return []
    conn = get_sqlite_conn(CACHE_DB, readonly=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM kline_cache WHERE ticker LIKE 'hk.%' ORDER BY ticker"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def fetch_hk_spot_map(retries: int = 3) -> dict[str, Any]:  # 快照行为 Series，类型混杂
    """获取全市场港股行情快照。

    Args:
        retries: 失败重试次数。

    Returns:
        ``{5位代码: 行情行}``；全部失败时返回空字典。
    """
    import time

    import akshare as ak

    def _index(df: Any) -> dict[str, Any]:  # 快照行类型混杂
        """按 5 位代码建立索引。"""
        spot_map: dict[str, Any] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(5)
            if code:
                spot_map[code] = row
        return spot_map

    # 主源：东方财富（含 PE/PB/市值/股息率）
    for attempt in range(retries):
        try:
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                spot_map = _index(df)
                logger.info(f"✅ 获取到 {len(spot_map)} 只港股行情快照（东财）")
                return spot_map
        except Exception as e:  # akshare 网络层异常类型不稳定
            logger.warning(f"东财港股快照失败（第{attempt + 1}次）: {e}")
            time.sleep(3)

    # 备用源：新浪（仅名称/价格/成交额，不含估值字段）
    try:
        df = ak.stock_hk_spot()
        if df is not None and not df.empty:
            spot_map = _index(df)
            logger.warning(f"⚠️  东财快照不可用，已回退新浪快照（{len(spot_map)} 只，无 PE/PB/市值）")
            return spot_map
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.warning(f"新浪港股快照也失败: {e}")

    logger.error("❌ 无法获取港股行情快照（行情/估值字段将保持原值）")
    return {}


def _safe_float(val: Any) -> float | None:  # 输入为 DataFrame 单元格，类型混杂
    """安全转换为 float；None/NaN/非数值返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def build_spot_record(
    ticker: str,
    row: Any,  # 快照行为 pandas Series
    hk_connect: set[str] | None,
    now_str: str,
) -> dict[str, Any]:  # 值类型混杂
    """把一行快照转换为 ``hk_metadata`` 记录。

    Args:
        ticker: ``hk.00700`` 形式。
        row: akshare 快照行。
        hk_connect: 港股通集合；``None`` 表示未知（不写入该列）。
        now_str: 当前时间戳字符串。

    Returns:
        仅含行情/估值字段的记录（技术指标交由 data_fetcher 计算）。
    """
    record: dict[str, Any] = {"ticker": ticker, "updated_at": now_str}
    name = str(row.get("中文名称") or "").strip()
    if name:
        record["name"] = name
    # 只写入真正取到的字段：None 不落库，避免用「空」覆盖已有估值（如财务接口已填 PE）
    for column, key in (
        ("pe_ttm", "市盈率"),
        ("pb", "市净率"),
        ("dividend_yield", "股息率"),
        ("market_cap", "总市值"),
        ("price", "最新价"),
    ):
        value = _safe_float(row.get(key))
        if value is not None:
            record[column] = value
    if hk_connect is not None:
        record["is_hk_connect"] = ticker in hk_connect
    return record


def main() -> int:
    """同步港股元数据。

    Returns:
        进程退出码（0 成功，1 部分/全部失败）。
    """
    parser = argparse.ArgumentParser(description="批量获取港股基本面元数据并写入本地数据库")
    parser.add_argument(
        "--codes",
        default=None,
        help="指定港股代码，逗号分隔，如 00700,00941（不含 hk. 前缀）",
    )
    parser.add_argument("--force", action="store_true", help="忽略 TTL，强制刷新全部字段")
    parser.add_argument("--skip-financial", action="store_true", help="只刷新行情快照")
    parser.add_argument(
        "--financial-ttl-days",
        type=int,
        default=HK_METADATA_FINANCIAL_TTL_DAYS,
        help=f"财务字段刷新周期（天，默认 {HK_METADATA_FINANCIAL_TTL_DAYS}）",
    )
    parser.add_argument(
        "--profile-ttl-days",
        type=int,
        default=365,
        help="上市信息/港股通刷新周期（天，默认 365）",
    )
    parser.add_argument(
        "--spot-ttl-hours",
        type=int,
        default=12,
        help="行情/估值快照刷新周期（小时，默认 12）",
    )
    parser.add_argument("--workers", type=int, default=2, help="财务拉取并发数（默认 2）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（0=全部）")
    args = parser.parse_args()

    _load_env()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        tickers = [f"hk.{c.zfill(5)}" for c in codes]
    else:
        tickers = get_hk_ticker_list_from_cache()

    if args.limit > 0:
        tickers = tickers[: args.limit]
    if not tickers:
        logger.error("❌ 没有找到任何港股 ticker")
        return 1

    logger.info(f"📊 待处理港股: {len(tickers)} 只")

    conn = get_sqlite_conn(CACHE_DB)
    ok = fail = 0
    try:
        ensure_hk_metadata_table(conn)
        existing = read_hk_metadata(conn, tickers)
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ── 1. 行情/估值快照（单次全市场请求）──────────────────────────
        need_spot = args.force or any(
            is_stale_hours(existing.get(t, {}).get("updated_at"), args.spot_ttl_hours)
            for t in tickers
        )
        if need_spot:
            hk_connect = get_hk_connect_stocks()
            if hk_connect is None:
                logger.warning("⚠️  港股通名单不可用，is_hk_connect 保持原值/未知")
            spot_map = fetch_hk_spot_map()
            written = 0
            name_rows: list[tuple[str, str]] = []
            for ticker in tickers:
                row = spot_map.get(ticker.replace("hk.", ""))
                if row is None:
                    continue
                upsert_hk_metadata(conn, build_spot_record(ticker, row, hk_connect, now_str))
                name = str(row.get("中文名称") or "").strip()
                if name:
                    name_rows.append((ticker, name))
                written += 1
            # 名称同步到本地缓存，筛选链路零延迟、不联网
            try:
                from scripts.stock_name_cache import (
                    batch_write_stock_name_cache,
                    ensure_stock_name_table,
                )

                ensure_stock_name_table(conn)
                batch_write_stock_name_cache(conn, name_rows)
                logger.info(f"✅ 股票名称缓存写入 {len(name_rows)} 只港股")
            except Exception as e:  # 名称缓存非关键路径
                logger.warning(f"⚠️  港股名称缓存写入失败: {e}")
            conn.commit()
            logger.info(f"✅ 行情/估值写入 {written} 只")
        else:
            logger.info("✅ 行情快照仍在 TTL 内，跳过")

        # ── 2. 档案 + 财务（逐只）─────────────────────────────────────
        if args.skip_financial:
            logger.info("⏭️  --skip-financial：跳过档案/财务刷新")
            return 0

        targets: list[str] = []
        for ticker in tickers:
            meta = existing.get(ticker)
            if args.force:
                targets.append(ticker)
                continue
            need_profile = not has_profile_data(meta) or is_stale(
                (meta or {}).get("profile_updated_at"), args.profile_ttl_days
            )
            need_financial = not has_financial_data(meta) or is_stale(
                (meta or {}).get("financial_updated_at"), args.financial_ttl_days
            )
            if need_profile or need_financial:
                targets.append(ticker)

        logger.info(f"📊 需刷新档案/财务: {len(targets)} 只")
        if not targets:
            return 0

        def _work(ticker: str) -> tuple[str, dict[str, Any] | None]:  # 财务记录值混杂
            """拉取单只港股档案 + 财务（线程内只取数，不写库）。"""
            try:
                data = fetch_hk_fundamentals(ticker.replace("hk.", ""))
            except Exception as e:  # 单只失败不阻断整体
                logger.warning(f"  ❌ {ticker} 基本面获取失败: {e}")
                return ticker, None
            return ticker, data

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(_work, t): t for t in targets}
            for i, future in enumerate(as_completed(futures), 1):
                ticker, data = future.result()
                if data is None:
                    fail += 1
                    continue
                record: dict[str, Any] = {"ticker": ticker, **data}
                record["profile_updated_at"] = now_str
                record["financial_updated_at"] = now_str
                upsert_hk_metadata(conn, record)
                ok += 1
                if i % 50 == 0:
                    conn.commit()
                    logger.info(f"  已处理 {i}/{len(targets)}（成功 {ok}，失败 {fail}）")

        conn.commit()
    finally:
        conn.close()

    logger.info(f"✅ 港股元数据同步完成：成功 {ok} 只，失败 {fail} 只，候选 {len(tickers)} 只")
    return 0 if fail == 0 or ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
