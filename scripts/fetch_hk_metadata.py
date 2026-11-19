#!/usr/bin/env python3
"""批量获取港股基本面元数据并存入本地数据库。

从 akshare 获取港股实时行情（包含 PE/PB/市值/换手率等），
并写入 pipeline_cache.db 的 hk_metadata 表，供筛选脚本本地读取。

用法：
    uv run python scripts/fetch_hk_metadata.py              # 全量获取所有港股元数据
    uv run python scripts/fetch_hk_metadata.py --codes "00700,00941"  # 指定股票
    uv run python scripts/fetch_hk_metadata.py --force      # 强制重新获取已有数据
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any

from loguru import logger

from scripts._utils import get_cache_db_path

CACHE_DB = get_cache_db_path()


def get_hk_ticker_list_from_cache() -> list[str]:
    """从本地缓存获取所有港股 ticker。"""
    conn = sqlite3.connect(str(CACHE_DB))
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM kline_cache WHERE ticker LIKE 'hk.%' ORDER BY ticker"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def create_metadata_table(conn: sqlite3.Connection) -> None:
    """创建 hk_metadata 表（如果不存在）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hk_metadata (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            pe_ttm REAL,
            pb REAL,
            dividend_yield REAL,
            market_cap REAL,
            turnover_20d_avg REAL,
            turnover_rate_12m_avg REAL,
            listing_date TEXT,
            listing_days INTEGER,
            is_hk_connect INTEGER DEFAULT 0,
            is_hkd INTEGER DEFAULT 1,
            is_common_stock INTEGER DEFAULT 1,
            suspension_days_30d INTEGER DEFAULT 0,
            has_risk_warning INTEGER DEFAULT 0,
            debt_to_asset REAL,
            non_gaap_profit_2y INTEGER DEFAULT 0,
            clean_audit_2y INTEGER DEFAULT 0,
            operating_cashflow_3y INTEGER DEFAULT 0,
            revenue_growth_3y INTEGER DEFAULT 0,
            gross_margin REAL,
            avg_roe_3y REAL,
            top_shareholder_pct REAL,
            max_drawdown_12m REAL,
            rights_issues_12m INTEGER DEFAULT 0,
            price REAL,
            drawdown_from_high REAL,
            single_day_spike_1y REAL,
            dividend_3y INTEGER DEFAULT 0,
            payout_ratio REAL,
            annualized_volatility_12m REAL,
            fcf_to_net_profit REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """
    )
    conn.commit()


def fetch_hk_spot_data() -> dict:
    """从 akshare 获取全市场港股实时行情。

    Returns
    -------
    dict
        代码 -> 行情数据的映射
    """
    import akshare as ak

    logger = __import__("loguru").logger

    for attempt in range(5):
        try:
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                logger.info(f"✅ 获取到 {len(df)} 只港股实时行情")
                result: dict = {}
                for _, r in df.iterrows():
                    code = str(r.get("代码", "")).zfill(5)
                    if code:
                        result[code] = r
                return result
        except Exception as e:
            logger.warning(f"获取全市场行情失败（第{attempt + 1}次）: {e}")
            import time as _time

            _time.sleep(3)

    logger.error("❌ 无法获取港股实时行情")
    return {}


def store_metadata(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
    industry: str,
    pe_ttm: float | None,
    pb: float | None,
    dividend_yield: float | None,
    market_cap: float | None,
    turnover_20d_avg: float | None,
    turnover_rate_12m_avg: float | None,
    listing_date: str | None,
    listing_days: int | None,
    is_hk_connect: bool,
    is_hkd: bool,
    is_common_stock: bool,
    suspension_days_30d: int,
    has_risk_warning: bool,
    debt_to_asset: float | None,
    non_gaap_profit_2y: bool,
    clean_audit_2y: bool,
    operating_cashflow_3y: bool,
    revenue_growth_3y: bool,
    gross_margin: float | None,
    avg_roe_3y: float | None,
    top_shareholder_pct: float | None,
    max_drawdown_12m: float | None,
    rights_issues_12m: int,
    price: float | None,
    drawdown_from_high: float | None,
    single_day_spike_1y: float | None,
    dividend_3y: bool,
    payout_ratio: float | None,
    annualized_volatility_12m: float | None,
    fcf_to_net_profit: float | None,
) -> None:
    """将单只港股元数据写入数据库。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO hk_metadata (
            ticker, name, industry, pe_ttm, pb, dividend_yield, market_cap,
            turnover_20d_avg, turnover_rate_12m_avg, listing_date, listing_days,
            is_hk_connect, is_hkd, is_common_stock, suspension_days_30d,
            has_risk_warning, debt_to_asset, non_gaap_profit_2y, clean_audit_2y,
            operating_cashflow_3y, revenue_growth_3y, gross_margin, avg_roe_3y,
            top_shareholder_pct, max_drawdown_12m, rights_issues_12m,
            price, drawdown_from_high, single_day_spike_1y,
            dividend_3y, payout_ratio, annualized_volatility_12m,
            fcf_to_net_profit, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            ticker, name, industry, pe_ttm, pb, dividend_yield, market_cap,
            turnover_20d_avg, turnover_rate_12m_avg, listing_date, listing_days,
            int(is_hk_connect), int(is_hkd), int(is_common_stock),
            suspension_days_30d, int(has_risk_warning), debt_to_asset,
            int(non_gaap_profit_2y), int(clean_audit_2y),
            int(operating_cashflow_3y), int(revenue_growth_3y),
            gross_margin, avg_roe_3y, top_shareholder_pct, max_drawdown_12m,
            rights_issues_12m, price, drawdown_from_high, single_day_spike_1y,
            int(dividend_3y), payout_ratio, annualized_volatility_12m,
            fcf_to_net_profit,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="批量获取港股基本面元数据并存入本地数据库")
    parser.add_argument(
        "--codes",
        default=None,
        help="指定港股代码，逗号分隔，如 00700,00941（不含 hk. 前缀）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新获取已有数据",
    )
    args = parser.parse_args()

    # 获取股票列表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        tickers = [f"hk.{c.zfill(5)}" for c in codes]
    else:
        tickers = get_hk_ticker_list_from_cache()

    if not tickers:
        logger.error("❌ 没有找到任何港股 ticker")
        sys.exit(1)

    logger.info(f"📊 待获取元数据的港股数量: {len(tickers)} 只")

    # 创建元数据表
    conn = sqlite3.connect(str(CACHE_DB))
    create_metadata_table(conn)

    # 获取全市场实时行情
    spot_map = fetch_hk_spot_data()

    success_count = 0
    fail_count = 0

    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace("hk.", "")

        # 检查是否已有数据且不强制更新
        if not args.force:
            existing = conn.execute(
                "SELECT ticker FROM hk_metadata WHERE ticker = ?", (ticker,)
            ).fetchone()
            if existing:
                continue

        row = spot_map.get(symbol)
        if row is None:
            fail_count += 1
            continue

        try:
            store_metadata(
                conn=conn,
                ticker=ticker,
                name=str(r.get("中文名称", "")) if (r := row) is not None else "",
                industry="",  # 本地暂无行业数据
                pe_ttm=_safe_float(r.get("市盈率")),
                pb=_safe_float(r.get("市净率")),
                dividend_yield=_safe_float(r.get("股息率")),
                market_cap=_safe_float(r.get("总市值")),
                turnover_20d_avg=_safe_float(r.get("成交额")),
                turnover_rate_12m_avg=_safe_float(r.get("换手率")),
                listing_date=None,
                listing_days=None,
                is_hk_connect=False,  # 需要另行获取港股通名单
                is_hkd=True,
                is_common_stock=True,
                suspension_days_30d=0,
                has_risk_warning=False,
                debt_to_asset=None,
                non_gaap_profit_2y=False,
                clean_audit_2y=False,
                operating_cashflow_3y=False,
                revenue_growth_3y=False,
                gross_margin=None,
                avg_roe_3y=None,
                top_shareholder_pct=None,
                max_drawdown_12m=None,
                rights_issues_12m=0,
                price=_safe_float(r.get("最新价")),
                drawdown_from_high=None,
                single_day_spike_1y=None,
                dividend_3y=False,
                payout_ratio=None,
                annualized_volatility_12m=None,
                fcf_to_net_profit=None,
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            logger.debug(f"存储 {ticker} 元数据失败: {e}")

        if i % 100 == 0:
            conn.commit()
            logger.info(f"  已处理 {i}/{len(tickers)} 只（成功: {success_count}, 失败: {fail_count}）")

    conn.commit()
    conn.close()

    logger.info(
        f"✅ 港股元数据同步完成！成功 {success_count} 只，失败 {fail_count} 只，总计 {len(tickers)} 只"
    )


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        import pandas as pd

        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
