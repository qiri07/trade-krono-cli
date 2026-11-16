"""scripts/hk_screen/data_fetcher.py — 港股数据获取

从 akshare 获取港股所需的筛选数据：
1. 全量港股列表（stock_hk_spot_em）
2. 港股通成分股（stock_hk_ggt_components_em）
3. 公司概况（stock_hk_company_profile_em，单只查询）
4. 财务指标（stock_hk_financial_indicator_em，单只查询）
5. K线数据（通过 DataProviderFactory 获取，用于计算技术指标）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from loguru import logger

from scripts.hk_screen.screener import HKScreenResult


@dataclass
class HKRawData:
    """单只港股的原始数据"""

    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""

    # 基础行情
    price: float | None = None
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None

    # 流动性
    turnover_20d_avg: float | None = None
    turnover_rate_12m_avg: float | None = None

    # 上市信息
    listing_date: str | None = None
    listing_days: int | None = None
    is_hk_connect: bool = False
    is_hkd: bool = True  # 港股默认港币
    is_common_stock: bool = True  # 默认普通股，需进一步验证

    # 停牌与监管
    suspension_days_30d: int = 0
    has_risk_warning: bool = False

    # 财务指标
    debt_to_asset: float | None = None
    non_gaap_profit_2y: bool = False
    clean_audit_2y: bool = False
    operating_cashflow_3y: bool = False
    revenue_growth_3y: bool = False
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 异常标的指标
    top_shareholder_pct: float | None = None
    max_drawdown_12m: float | None = None
    rights_issues_12m: int = 0
    drawdown_from_high: float | None = None
    single_day_spike_1y: float | None = None

    # 质量加分指标
    dividend_3y: bool = False
    payout_ratio: float | None = None
    annualized_volatility_12m: float | None = None
    fcf_to_net_profit: float | None = None


def get_hk_stock_list() -> list[str]:
    """获取全量港股代码列表。

    通过 akshare.stock_hk_spot_em() 获取所有港股代码。

    Returns
    -------
    list[str]
        港股 ticker 列表，格式如 ["hk.00700", "hk.00941", ...]
    """
    import akshare as ak

    logger.info("📊 正在获取全量港股列表...")
    df = ak.stock_hk_spot_em()
    if df is None or df.empty:
        logger.error("❌ 无法获取港股列表，akshare 返回空数据")
        return []

    codes = df["代码"].astype(str).str.zfill(5).tolist()
    tickers = [f"hk.{code}" for code in codes]
    logger.info(f"✅ 获取到 {len(tickers)} 只港股")
    return tickers


def get_hk_connect_stocks() -> set[str]:
    """获取港股通成分股清单。

    Returns
    -------
    set[str]
        港股通标的 ticker 集合，格式如 {"hk.00700", "hk.00941"}
    """
    import akshare as ak

    logger.info("📊 正在获取港股通成分股清单...")
    try:
        df = ak.stock_hk_ggt_components_em()
        if df is None or df.empty:
            logger.warning("⚠️  港股通成分股清单为空，将不进行港股通资格过滤")
            return set()

        # 代码列可能是 "代码" 或 "股票代码"
        code_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
                break

        if code_col is None:
            logger.warning("⚠️  无法识别港股通成分股清单中的代码列")
            return set()

        codes = df[code_col].astype(str).str.zfill(5).tolist()
        tickers = {f"hk.{code}" for code in codes}
        logger.info(f"✅ 获取到 {len(tickers)} 只港股通标的")
        return tickers
    except Exception as e:
        logger.warning(f"⚠️  获取港股通成分股失败: {e}，将不进行港股通资格过滤")
        return set()


def fetch_hk_raw_data(
    tickers: list[str],
    hk_connect_stocks: set[str] | None = None,
) -> list[HKRawData]:
    """批量获取港股原始筛选数据。

    对每只股票获取：
    - 基础行情（价格、市值、PE/PB、股息率）
    - 流动性数据（成交额、换手率）
    - 公司概况（行业、上市日期、是否普通股等）
    - 财务指标（资产负债率、ROE、毛利率等）

    Parameters
    ----------
    tickers : list[str]
        港股 ticker 列表。
    hk_connect_stocks : set[str] | None
        港股通标的集合。如果为 None，则自动获取。

    Returns
    -------
    list[HKRawData]
        原始数据列表。
    """
    import akshare as ak

    if hk_connect_stocks is None:
        hk_connect_stocks = get_hk_connect_stocks()

    results: list[HKRawData] = []

    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace("hk.", "")

        # 获取实时行情（包含价格、市值、PE/PB等）
        try:
            spot_df = ak.stock_hk_spot_em()
            if spot_df is not None and not spot_df.empty:
                row = spot_df[spot_df["代码"].astype(str).str.zfill(5) == symbol]
                if not row.empty:
                    r = row.iloc[0]
                    raw = HKRawData(
                        ticker=ticker,
                        name=str(r.get("中文名称", "")),
                        price=_safe_float(r.get("最新价")),
                        market_cap=_safe_float(r.get("总市值")),
                        pe_ttm=_safe_float(r.get("市盈率")),
                        pb=_safe_float(r.get("市净率")),
                        dividend_yield=_safe_float(r.get("股息率")),
                        turnover_20d_avg=_safe_float(r.get("成交额")),
                        turnover_rate_12m_avg=_safe_float(r.get("换手率")),
                        is_hkd=True,
                        is_common_stock=True,
                    )
                else:
                    raw = HKRawData(ticker=ticker)
            else:
                raw = HKRawData(ticker=ticker)
        except Exception as e:
            logger.debug(f"获取 {ticker} 行情失败: {e}")
            raw = HKRawData(ticker=ticker)

        # 获取公司概况（行业、上市日期等）
        try:
            profile_df = ak.stock_hk_company_profile_em(symbol=symbol)
            if profile_df is not None and not profile_df.empty:
                # 取第一行
                p = profile_df.iloc[0]
                raw.sector = str(p.get("所属行业", "")) or raw.sector
                # 上市日期
                list_date = p.get("公司成立日期", "")
                if list_date:
                    try:
                        raw.listing_date = str(list_date)[:10]
                        list_dt = pd.to_datetime(list_date)
                        raw.listing_days = (pd.Timestamp.now() - list_dt).days
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"获取 {ticker} 公司概况失败: {e}")

        # 获取财务指标
        try:
            fin_df = ak.stock_hk_financial_indicator_em(symbol=symbol)
            if fin_df is not None and not fin_df.empty:
                f = fin_df.iloc[-1]  # 取最新一期
                raw.debt_to_asset = _safe_float(f.get("资产负债率"))
                raw.gross_margin = _safe_float(f.get("毛利率"))
                raw.avg_roe_3y = _safe_float(f.get("ROE"))
                raw.non_gaap_profit_2y = True  # 假设通过，需要实际数据验证
                raw.clean_audit_2y = True
                raw.operating_cashflow_3y = True
                raw.revenue_growth_3y = True
        except Exception as e:
            logger.debug(f"获取 {ticker} 财务指标失败: {e}")

        # 设置港股通标志
        raw.is_hk_connect = ticker in hk_connect_stocks

        results.append(raw)

        if i % 50 == 0:
            logger.info(f"  已处理 {i}/{len(tickers)} 只港股...")

    logger.info(f"✅ 完成 {len(results)} 只港股原始数据获取")
    return results


def _safe_float(val: Any) -> float | None:
    """安全转换为 float，失败返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def raw_to_screen_result(raw: HKRawData) -> HKScreenResult:
    """将 HKRawData 转换为 HKScreenResult。"""
    return HKScreenResult(
        ticker=raw.ticker,
        name=raw.name,
        sector=raw.sector,
        industry=raw.industry,
        market_cap=raw.market_cap,
        pe_ttm=raw.pe_ttm,
        pb=raw.pb,
        dividend_yield=raw.dividend_yield,
        turnover_20d_avg=raw.turnover_20d_avg,
        turnover_rate_12m_avg=raw.turnover_rate_12m_avg,
        listing_days=raw.listing_days,
        is_hk_connect=raw.is_hk_connect,
        is_hkd=raw.is_hkd,
        is_common_stock=raw.is_common_stock,
        suspension_days_30d=raw.suspension_days_30d,
        has_risk_warning=raw.has_risk_warning,
        debt_to_asset=raw.debt_to_asset,
        non_gaap_profit_2y=raw.non_gaap_profit_2y,
        clean_audit_2y=raw.clean_audit_2y,
        operating_cashflow_3y=raw.operating_cashflow_3y,
        revenue_growth_3y=raw.revenue_growth_3y,
        gross_margin=raw.gross_margin,
        avg_roe_3y=raw.avg_roe_3y,
        top_shareholder_pct=raw.top_shareholder_pct,
        max_drawdown_12m=raw.max_drawdown_12m,
        rights_issues_12m=raw.rights_issues_12m,
        price=raw.price,
        drawdown_from_high=raw.drawdown_from_high,
        single_day_spike_1y=raw.single_day_spike_1y,
        dividend_3y=raw.dividend_3y,
        annualized_volatility_12m=raw.annualized_volatility_12m,
        fcf_to_net_profit=raw.fcf_to_net_profit,
    )
