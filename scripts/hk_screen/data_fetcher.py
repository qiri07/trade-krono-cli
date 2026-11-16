"""scripts/hk_screen/data_fetcher.py — 港股数据获取

优先从本地缓存数据库 (pipeline_cache.db) 读取港股 K 线数据，
仅缺失的基本面字段再尝试从 akshare API 获取。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from io import BytesIO
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

    # 基础行情（从本地 K 线缓存优先获取）
    price: float | None = None
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None

    # 流动性（从本地 K 线缓存计算）
    turnover_20d_avg: float | None = None
    turnover_rate_12m_avg: float | None = None

    # 上市信息
    listing_date: str | None = None
    listing_days: int | None = None
    is_hk_connect: bool = False
    is_hkd: bool = True  # 港股默认港币
    is_common_stock: bool = True  # 默认普通股，需进一步验证

    # 停牌与监管（从本地 K 线计算）
    suspension_days_30d: int = 0
    has_risk_warning: bool = False

    # 财务指标（优先从 API 获取，缺失时保留 None）
    debt_to_asset: float | None = None
    non_gaap_profit_2y: bool | None = None
    clean_audit_2y: bool | None = None
    operating_cashflow_3y: bool | None = None
    revenue_growth_3y: bool | None = None
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 异常标的指标（从本地 K 线计算）
    top_shareholder_pct: float | None = None
    max_drawdown_12m: float | None = None
    rights_issues_12m: int = 0
    drawdown_from_high: float | None = None
    single_day_spike_1y: float | None = None

    # 质量加分指标
    dividend_3y: bool | None = None
    payout_ratio: float | None = None
    annualized_volatility_12m: float | None = None
    fcf_to_net_profit: float | None = None


def get_hk_stock_list_from_cache() -> list[str]:
    """从本地缓存数据库获取所有港股 ticker 列表。

    优先使用本地 pipeline_cache.db 中已有的港股数据，避免依赖网络 API。

    Returns
    -------
    list[str]
        港股 ticker 列表，格式如 ["hk.00700", "hk.00941", ...]
    """
    from pathlib import Path

    db_path = Path("outputs/cache/pipeline_cache.db")
    if not db_path.exists():
        logger.warning("⚠️  本地缓存数据库不存在，将尝试从 akshare 获取港股列表")
        return get_hk_stock_list_from_akshare()

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM kline_cache WHERE ticker LIKE 'hk.%' ORDER BY ticker"
        ).fetchall()
        conn.close()
        tickers = [r[0] for r in rows]
        logger.info(f"✅ 从本地缓存获取到 {len(tickers)} 只港股")
        return tickers
    except Exception as e:
        logger.warning(f"⚠️  从本地缓存获取港股列表失败: {e}，将尝试从 akshare 获取")
        return get_hk_stock_list_from_akshare()


def get_hk_stock_list_from_akshare() -> list[str]:
    """从 akshare 获取全量港股代码列表。

    Returns
    -------
    list[str]
        港股 ticker 列表，格式如 ["hk.00700", "hk.00941", ...]
    """
    import akshare as ak

    logger.info("📊 正在从 akshare 获取全量港股列表...")
    for attempt in range(3):
        try:
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                codes = df["代码"].astype(str).str.zfill(5).tolist()
                tickers = [f"hk.{code}" for code in codes]
                logger.info(f"✅ 获取到 {len(tickers)} 只港股")
                return tickers
        except Exception as e:
            logger.warning(f"获取港股列表失败（第{attempt + 1}次）: {e}")
            import time

            time.sleep(2)
    logger.error("❌ 无法获取港股列表")
    return []


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


def _load_hk_kline_from_cache(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """从本地缓存数据库加载港股 K 线数据。

    Parameters
    ----------
    tickers : list[str]
        港股 ticker 列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        ticker -> K 线 DataFrame 的映射。
    """
    from pathlib import Path

    db_path = Path("outputs/cache/pipeline_cache.db")
    if not db_path.exists():
        logger.warning("⚠️  本地缓存数据库不存在")
        return {}

    try:
        conn = sqlite3.connect(str(db_path))
        placeholders = ",".join(["?"] * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, data FROM kline_cache WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        conn.close()

        result: dict[str, pd.DataFrame] = {}
        for ticker, data_blob in rows:
            try:
                df = pd.read_pickle(BytesIO(data_blob))
                if df is not None and not df.empty and "timestamps" in df.columns:
                    df = df.sort_values("timestamps").reset_index(drop=True)
                    result[ticker] = df
            except Exception as e:
                logger.debug(f"读取 {ticker} K 线缓存失败: {e}")

        logger.info(f"✅ 从本地缓存加载了 {len(result)} 只港股 K 线数据")
        return result
    except Exception as e:
        logger.warning(f"⚠️  从本地缓存加载港股 K 线失败: {e}")
        return {}


def _compute_technical_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """从 K 线数据计算技术指标。

    计算：
    - 最新价格
    - 近20个交易日日均成交额
    - 近12个月最大回撤
    - 从历史高点跌幅
    - 近1年无基本面支撑单日最大涨幅
    - 近30个交易日停牌天数（通过成交量为0判断）

    Parameters
    ----------
    df : pd.DataFrame
        K 线数据，含 timestamps, close, volume, amount 列。

    Returns
    -------
    dict[str, Any]
        计算出的技术指标字典。
    """
    if df is None or df.empty or len(df) < 2:
        return {}

    closes = df["close"].astype(float)
    volumes = df["volume"].astype(float)
    amounts = df["amount"].astype(float) if "amount" in df.columns else closes * volumes

    # 最新价格
    latest_price = float(closes.iloc[-1])

    # 近20个交易日日均成交额 (港元)
    recent_20d = amounts.tail(20)
    avg_turnover_20d = float(recent_20d.mean()) if len(recent_20d) > 0 else None

    # 近12个月最大回撤
    max_drawdown_12m = None
    if len(closes) >= 2:
        rolling_max = closes.cummax()
        drawdown = (closes - rolling_max) / rolling_max
        max_drawdown_12m = float(abs(drawdown.min())) if len(drawdown) > 0 else None

    # 从历史高点跌幅
    all_time_max = closes.max()
    drawdown_from_high = None
    if all_time_max > 0:
        drawdown_from_high = float((all_time_max - closes.iloc[-1]) / all_time_max)

    # 近1年无基本面支撑单日最大涨幅
    single_day_spike_1y = None
    if len(closes) >= 2:
        daily_returns = closes.pct_change().dropna()
        if len(daily_returns) > 0:
            single_day_spike_1y = float(daily_returns.max())

    # 近30个交易日停牌天数（成交量为0或极小视为停牌）
    recent_30d = volumes.tail(30)
    suspension_days = int((recent_30d <= 0).sum()) if len(recent_30d) > 0 else 0

    return {
        "price": latest_price,
        "turnover_20d_avg": avg_turnover_20d,
        "max_drawdown_12m": max_drawdown_12m,
        "drawdown_from_high": drawdown_from_high,
        "single_day_spike_1y": single_day_spike_1y,
        "suspension_days_30d": suspension_days,
    }


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


def _fetch_hk_spot_data() -> dict[str, Any]:
    """从 akshare 获取全市场港股实时行情。

    Returns
    -------
    dict[str, Any]
        代码 -> 行情数据的映射，键为 6 位数字代码（如 "00700"）。
    """
    import akshare as ak

    logger.info("📊 正在尝试获取全市场港股实时行情...")
    for attempt in range(3):
        try:
            spot_df = ak.stock_hk_spot_em()
            if spot_df is not None and not spot_df.empty:
                logger.info(f"✅ 获取到 {len(spot_df)} 只港股实时行情")
                spot_map: dict[str, Any] = {}
                for _, row in spot_df.iterrows():
                    code = str(r.get("代码", "")).zfill(5)
                    if code:
                        spot_map[code] = r
                return spot_map
        except Exception as e:
            logger.warning(f"获取全市场行情失败（第{attempt + 1}次）: {e}")
            import time

            time.sleep(2)
    logger.error("❌ 无法获取港股实时行情")
    return {}


def _fetch_hk_metadata_from_akshare(ticker: str) -> dict[str, Any] | None:
    """从 akshare 获取单只港股的基本面元数据。

    Parameters
    ----------
    ticker : str
        港股 ticker，如 "hk.00700"。

    Returns
    -------
    dict[str, Any] | None
        基本面数据字典，包含 name, industry, pe_ttm, pb, dividend_yield,
        market_cap, turnover_20d_avg, turnover_rate_12m_avg 等字段。
    """
    import akshare as ak

    symbol = ticker.replace("hk.", "")

    for attempt in range(3):
        try:
            # 获取实时行情（包含 PE/PB/市值/换手率等）
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                row = df[df["代码"].astype(str).str.zfill(5) == symbol]
                if not row.empty:
                    row_data = row.iloc[0]
                    return {
                        "name": str(row_data.get("中文名称", "")),
                        "price": _safe_float(row_data.get("最新价")),
                        "market_cap": _safe_float(r.get("总市值")),
                        "pe_ttm": _safe_float(r.get("市盈率")),
                        "pb": _safe_float(r.get("市净率")),
                        "dividend_yield": _safe_float(r.get("股息率")),
                        "turnover_20d_avg": _safe_float(r.get("成交额")),
                        "turnover_rate_12m_avg": _safe_float(r.get("换手率")),
                    }
        except Exception as e:
            logger.debug(f"获取 {ticker} 行情失败（第{attempt + 1}次）: {e}")
            import time

            time.sleep(1)

    return None


def fetch_hk_raw_data(
    tickers: list[str],
    hk_connect_stocks: set[str] | None = None,
) -> list[HKRawData]:
    """批量获取港股原始筛选数据。

    优先从本地缓存数据库 (pipeline_cache.db) 读取 K 线数据，
    仅对缺失的基本面字段尝试从 akshare API 获取。

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
    import time

    import akshare as ak

    if hk_connect_stocks is None:
        hk_connect_stocks = get_hk_connect_stocks()

    # 步骤1：从本地缓存加载所有港股 K 线数据
    logger.info("📊 正在从本地缓存加载港股 K 线数据...")
    kline_map = _load_hk_kline_from_cache(tickers)
    logger.info(f"✅ 本地缓存命中 {len(kline_map)} 只港股 K 线数据")

    # 步骤2：尝试获取全市场实时行情（用于获取 PE/PB/市值等基本面）
    logger.info("📊 正在尝试获取全市场港股实时行情（用于补充基本面）...")
    spot_map: dict[str, Any] = {}
    for attempt in range(3):
        try:
            spot_df = ak.stock_hk_spot_em()
            if spot_df is not None and not spot_df.empty:
                logger.info(f"✅ 获取到 {len(spot_df)} 只港股实时行情")
                for _, row in spot_df.iterrows():
                    code = str(r.get("代码", "")).zfill(5)
                    if code:
                        spot_map[code] = r
                break
        except Exception as e:
            logger.warning(f"获取全市场行情失败（第{attempt + 1}次）: {e}")
            time.sleep(2)

    results: list[HKRawData] = []

    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace("hk.", "")

        # 从本地 K 线缓存获取技术指标
        kline_df = kline_map.get(ticker)
        tech_indicators = _compute_technical_indicators(kline_df) if kline_df is not None else {}

        # 构建基础数据
        raw = HKRawData(
            ticker=ticker,
            name="",  # 从本地缓存无法获取名称，后续尝试从 API 获取
            price=tech_indicators.get("price"),
            turnover_20d_avg=tech_indicators.get("turnover_20d_avg"),
            max_drawdown_12m=tech_indicators.get("max_drawdown_12m"),
            drawdown_from_high=tech_indicators.get("drawdown_from_high"),
            single_day_spike_1y=tech_indicators.get("single_day_spike_1y"),
            suspension_days_30d=tech_indicators.get("suspension_days_30d", 0),
            is_hkd=True,
            is_common_stock=True,
        )

        # 从全市场行情中查找对应股票，补充基本面数据
        row = spot_map.get(symbol)
        if row is not None:
            raw.name = str(r.get("中文名称", "")) if (r := row) is not None else ""
            raw.market_cap = _safe_float(r.get("总市值"))
            raw.pe_ttm = _safe_float(r.get("市盈率"))
            raw.pb = _safe_float(r.get("市净率"))
            raw.dividend_yield = _safe_float(r.get("股息率"))
            # 如果本地没有成交额数据，尝试从 API 补充
            if raw.turnover_20d_avg is None:
                raw.turnover_20d_avg = _safe_float(r.get("成交额"))
            if raw.turnover_rate_12m_avg is None:
                raw.turnover_rate_12m_avg = _safe_float(r.get("换手率"))

        # 设置港股通标志
        raw.is_hk_connect = ticker in hk_connect_stocks

        results.append(raw)

        if i % 50 == 0:
            logger.info(f"  已处理 {i}/{len(tickers)} 只港股...")

    logger.info(f"✅ 完成 {len(results)} 只港股原始数据获取")
    return results