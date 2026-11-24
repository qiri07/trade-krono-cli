"""scripts/hk_screen/data_fetcher.py — 港股数据获取

数据优先级（修复后）：

1. 本地 K 线缓存（``pipeline_cache.db`` → ``kline_cache``）计算技术/流动性指标；
2. 本地元数据缓存（``pipeline_cache.db`` → ``hk_metadata``）提供 PE/PB/市值/财务指标；
3. 仅当以上仍缺关键字段且 ``live_fallback=True`` 时，才回退 akshare 实时快照。

这样每日流程不再强依赖易失败的实时接口；缺失字段以 ``None`` 表示「未知」，
由筛选器按三态语义处理（未知不计失败，但计入 ``coverage``）。
"""

from __future__ import annotations

import pickle
import sqlite3
from dataclasses import dataclass
from io import BytesIO
from typing import Any  # K 线/元数据值类型混杂（float/int/bool/str/None），Any 为合理

import pandas as pd
from loguru import logger

from scripts._utils import expected_trading_day, get_cache_db_path, get_sqlite_conn
from scripts.hk_screen.metadata_store import read_hk_metadata
from scripts.hk_screen.screener import HKScreenResult

# 12 个月 / 1 年窗口统一使用 252 个交易日
TRADING_DAYS_PER_YEAR = 252


@dataclass
class HKRawData:
    """单只港股的原始数据（缺失为 ``None``，布尔未知为 ``None``）。"""

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
    is_hk_connect: bool | None = None
    is_hkd: bool | None = None
    is_common_stock: bool | None = None

    # 停牌与监管
    suspension_days_30d: int | None = None
    has_risk_warning: bool | None = None

    # 财务指标
    debt_to_asset: float | None = None
    non_gaap_profit_2y: bool | None = None
    clean_audit_2y: bool | None = None
    operating_cashflow_3y: bool | None = None
    revenue_growth_3y: bool | None = None
    gross_margin: float | None = None
    avg_roe_3y: float | None = None

    # 异常标的指标
    top_shareholder_pct: float | None = None
    max_drawdown_12m: float | None = None
    rights_issues_12m: int | None = None
    drawdown_from_high: float | None = None
    single_day_spike_1y: float | None = None

    # 质量加分指标
    dividend_3y: bool | None = None
    payout_ratio: float | None = None
    annualized_volatility_12m: float | None = None
    fcf_to_net_profit: float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 股票列表
# ─────────────────────────────────────────────────────────────────────────────


def get_hk_stock_list_from_cache() -> list[str]:
    """从本地缓存数据库获取所有港股 ticker（按 ticker 升序）。

    Returns:
        港股 ticker 列表，如 ``["hk.00700", "hk.00941", ...]``。
    """
    db_path = get_cache_db_path()
    if not db_path.exists():
        logger.warning("⚠️  本地缓存数据库不存在，尝试从 akshare 获取港股列表")
        return get_hk_stock_list_from_akshare()

    try:
        conn = get_sqlite_conn(db_path, readonly=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM kline_cache "
                "WHERE ticker LIKE 'hk.%' ORDER BY ticker"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"⚠️  从本地缓存获取港股列表失败: {e}，尝试从 akshare 获取")
        return get_hk_stock_list_from_akshare()

    tickers = [r[0] for r in rows]
    logger.info(f"✅ 从本地缓存获取到 {len(tickers)} 只港股")
    return tickers


def get_hk_stock_list_from_akshare() -> list[str]:
    """从 akshare 获取全量港股代码列表（失败返回空列表）。

    Returns:
        港股 ticker 列表，如 ``["hk.00700", ...]``。
    """
    import time

    import akshare as ak

    logger.info("📊 正在从 akshare 获取全量港股列表...")
    for attempt in range(3):
        try:
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                codes = df["代码"].astype(str).str.zfill(5).tolist()
                tickers = [f"hk.{code}" for code in codes]
                logger.info(f"✅ 获取到 {len(tickers)} 只港股（东财）")
                return tickers
        except Exception as e:  # akshare 内部异常类型不稳定，网络层统一兜底
            logger.warning(f"东财港股列表失败（第{attempt + 1}次）: {e}")
            time.sleep(2)

    try:
        df = ak.stock_hk_spot()
        if df is not None and not df.empty:
            codes = df["代码"].astype(str).str.zfill(5).tolist()
            tickers = [f"hk.{code}" for code in codes]
            logger.warning(f"⚠️  已回退新浪港股列表（{len(tickers)} 只）")
            return tickers
    except Exception as e:  # 网络层异常类型不稳定
        logger.warning(f"新浪港股列表也失败: {e}")

    logger.error("❌ 无法获取港股列表")
    return []


def get_hk_connect_stocks() -> set[str] | None:
    """获取港股通成分股清单。

    Returns:
        港股通标的 ticker 集合；**获取失败或为空时返回 None（未知）**，
        避免把「接口失败」误判为「全部不在港股通」。
    """
    import akshare as ak

    logger.info("📊 正在获取港股通成分股清单...")
    try:
        df = ak.stock_hk_ggt_components_em()
        if df is None or df.empty:
            logger.warning("⚠️  港股通成分股清单为空，标记为未知（不做剔除）")
            return None

        code_col = next((c for c in df.columns if "代码" in c), None)
        if code_col is None:
            logger.warning("⚠️  无法识别港股通清单代码列，标记为未知")
            return None

        codes = df[code_col].astype(str).str.zfill(5).tolist()
        tickers = {f"hk.{code}" for code in codes}
        logger.info(f"✅ 获取到 {len(tickers)} 只港股通标的")
        return tickers
    except Exception as e:  # 网络层异常类型不稳定，统一兜底为「未知」
        logger.warning(f"⚠️  获取港股通成分股失败: {e}，标记为未知（不做剔除）")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 本地数据加载
# ─────────────────────────────────────────────────────────────────────────────


def _load_hk_kline_from_cache(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """从本地缓存加载港股 K 线（按时间升序、重置索引）。

    Args:
        tickers: 港股 ticker 列表。

    Returns:
        ``{ticker: K线 DataFrame}``。
    """
    db_path = get_cache_db_path()
    if not db_path.exists():
        logger.warning("⚠️  本地缓存数据库不存在")
        return {}

    result: dict[str, pd.DataFrame] = {}
    conn = get_sqlite_conn(db_path, readonly=True)
    try:
        for i in range(0, len(tickers), 500):
            batch = tickers[i : i + 500]
            placeholders = ",".join(["?"] * len(batch))
            rows = conn.execute(
                f"SELECT ticker, data FROM kline_cache WHERE ticker IN ({placeholders})",
                batch,
            ).fetchall()
            for ticker, data_blob in rows:
                try:
                    df = pd.read_pickle(BytesIO(data_blob))
                except (OSError, ValueError, pickle.UnpicklingError) as e:
                    logger.debug(f"读取 {ticker} K 线缓存失败: {e}")
                    continue
                if df is None or df.empty or "timestamps" not in df.columns:
                    continue
                df = df.copy()
                df["timestamps"] = pd.to_datetime(df["timestamps"])
                result[ticker] = df.sort_values("timestamps").reset_index(drop=True)
    finally:
        conn.close()

    logger.info(f"✅ 从本地缓存加载了 {len(result)} 只港股 K 线数据")
    return result


def load_hk_metadata(tickers: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """读取本地港股元数据（``hk_metadata`` 表）。

    Args:
        tickers: 指定 ticker 列表；``None`` 表示全表。

    Returns:
        ``{ticker: 元数据字典}``；表不存在或为空时返回空字典。
    """
    db_path = get_cache_db_path()
    if not db_path.exists():
        return {}
    conn = get_sqlite_conn(db_path, readonly=True)
    try:
        return read_hk_metadata(conn, tickers)
    except sqlite3.Error as e:
        logger.warning(f"⚠️  读取 hk_metadata 失败: {e}")
        return {}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 技术指标
# ─────────────────────────────────────────────────────────────────────────────


def _compute_technical_indicators(
    df: pd.DataFrame, end_date: str | None = None
) -> dict[str, Any]:  # 指标值类型混杂（float/int/None）
    """从 K 线计算技术/流动性指标（严格按时间窗口）。

    计算项：
    - 最新价格（``end_date`` 当日或之前最后一个收盘价）
    - 近 20 个交易日日均成交额（港元）
    - 近 12 个月（252 交易日）最大回撤
    - 从历史高点跌幅（全历史，规则本身要求全历史高点）
    - 近 1 年（252 交易日）单日最大涨幅
    - 近 12 个月年化波动率（日收益标准差 × √252）
    - 近 30 个交易日停牌天数（成交量为 0）

    Args:
        df: K 线数据，需含 ``timestamps`` / ``close``，可选 ``volume`` / ``amount``。
        end_date: 分析日期 ``YYYY-MM-DD``；``None`` 使用数据最后一条。

    Returns:
        指标字典；数据不足返回空字典。
    """
    if df is None or df.empty or len(df) < 2:
        return {}

    work = df.copy()
    work["timestamps"] = pd.to_datetime(work["timestamps"])
    if end_date is not None:
        work = work.loc[work["timestamps"] <= pd.Timestamp(end_date)]
    if work.empty or len(work) < 2:
        return {}

    closes = work["close"].astype(float)
    volumes = (
        work["volume"].astype(float) if "volume" in work.columns else pd.Series(dtype=float)
    )
    amounts = (
        work["amount"].astype(float)
        if "amount" in work.columns
        else closes * volumes if len(volumes) == len(closes) else pd.Series(dtype=float)
    )

    latest_price = float(closes.iloc[-1])
    avg_turnover_20d = float(amounts.tail(20).mean()) if len(amounts) > 0 else None

    win = closes.tail(TRADING_DAYS_PER_YEAR)

    max_drawdown_12m = None
    if len(win) >= 2:
        rolling_max = win.cummax()
        drawdown = (win - rolling_max) / rolling_max
        max_drawdown_12m = float(abs(drawdown.min()))

    all_time_max = float(closes.max())
    drawdown_from_high = (
        float((all_time_max - latest_price) / all_time_max) if all_time_max > 0 else None
    )

    single_day_spike_1y = None
    annualized_volatility_12m = None
    if len(win) >= 3:
        rets = win.pct_change().dropna()
        if len(rets) > 0:
            single_day_spike_1y = float(rets.max())
        if len(rets) >= 2:
            annualized_volatility_12m = float(rets.std() * (TRADING_DAYS_PER_YEAR**0.5))

    suspension_days = int((volumes.tail(30) <= 0).sum()) if len(volumes) > 0 else None
    avg_amount_12m = float(amounts.tail(TRADING_DAYS_PER_YEAR).mean()) if len(amounts) > 0 else None

    return {
        "price": latest_price,
        "turnover_20d_avg": avg_turnover_20d,
        "avg_amount_12m": avg_amount_12m,
        "max_drawdown_12m": max_drawdown_12m,
        "drawdown_from_high": drawdown_from_high,
        "single_day_spike_1y": single_day_spike_1y,
        "annualized_volatility_12m": annualized_volatility_12m,
        "suspension_days_30d": suspension_days,
    }


def _listing_days_from(df: pd.DataFrame | None, end_date: str) -> int | None:
    """根据 K 线首日推算上市自然日数（无数据返回 None）。"""
    if df is None or df.empty:
        return None
    first = pd.to_datetime(df["timestamps"].iloc[0])
    try:
        return int((pd.Timestamp(end_date) - first).days)
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:  # 输入为 DataFrame 单元格，类型混杂
    """安全转换为 float；失败或 NaN 返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


# ─────────────────────────────────────────────────────────────────────────────
# 实时快照兜底
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_hk_spot_map() -> dict[str, Any]:  # 快照行为 Series，类型混杂
    """获取全市场港股实时快照（本地元数据缺失时兜底）。

    Returns:
        ``{5位代码: 行情行}``；失败返回空字典。
    """
    import time

    import akshare as ak

    logger.info("📊 本地元数据不完整，尝试获取全市场港股实时快照作为兜底...")
    for attempt in range(2):
        try:
            spot_df = ak.stock_hk_spot_em()
            if spot_df is not None and not spot_df.empty:
                spot_map: dict[str, Any] = {}
                for _, row in spot_df.iterrows():
                    code = str(row.get("代码", "")).zfill(5)
                    if code:
                        spot_map[code] = row
                logger.info(f"✅ 获取到 {len(spot_map)} 只港股实时快照")
                return spot_map
        except Exception as e:  # akshare 网络层异常类型不稳定
            logger.warning(f"获取港股实时快照失败（第{attempt + 1}次）: {e}")
            time.sleep(2)
    logger.warning("⚠️  港股实时快照不可用，将仅使用本地数据")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────


def fetch_hk_raw_data(
    tickers: list[str],
    hk_connect_stocks: set[str] | None = None,
    date: str | None = None,
    live_fallback: bool = True,
) -> list[HKRawData]:
    """批量获取港股原始筛选数据。

    Args:
        tickers: 港股 ticker 列表。
        hk_connect_stocks: 港股通标的集合；``None`` 表示未知。缺省时自动获取。
        date: 分析日期 ``YYYY-MM-DD``；``None`` 使用最近交易日。
        live_fallback: 本地仍缺名称/PE/市值时是否回退实时快照。

    Returns:
        与 ``tickers`` 同序的 :class:`HKRawData` 列表。
    """
    end_date = date or expected_trading_day()

    if hk_connect_stocks is None:
        hk_connect_stocks = get_hk_connect_stocks()

    logger.info(f"📊 加载港股 K 线缓存（分析日期 {end_date}）...")
    kline_map = _load_hk_kline_from_cache(tickers)
    logger.info(f"✅ 本地缓存命中 {len(kline_map)} 只港股 K 线数据")

    logger.info("📊 加载本地港股元数据（hk_metadata）...")
    meta_map = load_hk_metadata(tickers)
    logger.info(f"✅ 本地元数据命中 {len(meta_map)} 只港股")

    name_map: dict[str, str] = {}
    try:
        from scripts.stock_name_cache import open_name_cache, resolve_stock_names

        conn = open_name_cache()
        try:
            name_map = resolve_stock_names(conn, tickers)
        finally:
            conn.close()
    except Exception as e:  # 名称缓存非关键路径，失败不阻断
        logger.debug(f"读取股票名称缓存失败: {e}")

    spot_map: dict[str, Any] = {}
    if live_fallback:
        need_live = any(
            not (meta_map.get(t, {}).get("name") or name_map.get(t))
            or (
                meta_map.get(t, {}).get("pe_ttm") is None
                and meta_map.get(t, {}).get("market_cap") is None
            )
            for t in tickers
        )
        if need_live:
            spot_map = _fetch_hk_spot_map()

    results: list[HKRawData] = []
    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace("hk.", "")
        meta = meta_map.get(ticker, {})
        kline_df = kline_map.get(ticker)
        tech = _compute_technical_indicators(kline_df, end_date) if kline_df is not None else {}

        # 上市天数：元数据 listing_date 优先，其次 K 线首日
        listing_days = meta.get("listing_days")
        if listing_days is None and meta.get("listing_date"):
            try:
                listing_days = int(
                    (pd.Timestamp(end_date) - pd.Timestamp(meta["listing_date"])).days
                )
            except (ValueError, TypeError):
                listing_days = None
        if listing_days is None:
            listing_days = _listing_days_from(kline_df, end_date)

        # 港股通：元数据优先，其次传入集合，最后未知
        if meta.get("is_hk_connect") is not None:
            is_hk_connect: bool | None = bool(meta["is_hk_connect"])
        elif hk_connect_stocks is not None:
            is_hk_connect = ticker in hk_connect_stocks
        else:
            is_hk_connect = None

        market_cap = meta.get("market_cap")
        turnover_rate_12m = meta.get("turnover_rate_12m_avg")
        if turnover_rate_12m is None and market_cap and tech.get("avg_amount_12m"):
            # 换手率近似：近 12 个月日均成交额 / 总市值（%）
            turnover_rate_12m = tech["avg_amount_12m"] / market_cap * 100.0

        def _pick(key: str, _meta: dict[str, Any] = meta, _tech: dict[str, Any] = tech) -> Any:
            """元数据优先，缺失时回退技术指标（绑定当前循环变量）。"""
            value = _meta.get(key)
            return value if value is not None else _tech.get(key)

        raw = HKRawData(
            ticker=ticker,
            name=str(meta.get("name") or name_map.get(ticker) or ""),
            sector=str(meta.get("industry") or ""),
            industry=str(meta.get("industry") or ""),
            price=_pick("price"),
            market_cap=market_cap,
            pe_ttm=meta.get("pe_ttm"),
            pb=meta.get("pb"),
            dividend_yield=meta.get("dividend_yield"),
            turnover_20d_avg=_pick("turnover_20d_avg"),
            turnover_rate_12m_avg=turnover_rate_12m,
            listing_date=meta.get("listing_date"),
            listing_days=listing_days,
            is_hk_connect=is_hk_connect,
            is_hkd=meta.get("is_hkd"),
            is_common_stock=meta.get("is_common_stock"),
            suspension_days_30d=_pick("suspension_days_30d"),
            has_risk_warning=meta.get("has_risk_warning"),
            debt_to_asset=meta.get("debt_to_asset"),
            non_gaap_profit_2y=meta.get("non_gaap_profit_2y"),
            clean_audit_2y=meta.get("clean_audit_2y"),
            operating_cashflow_3y=meta.get("operating_cashflow_3y"),
            revenue_growth_3y=meta.get("revenue_growth_3y"),
            gross_margin=meta.get("gross_margin"),
            avg_roe_3y=meta.get("avg_roe_3y"),
            top_shareholder_pct=meta.get("top_shareholder_pct"),
            max_drawdown_12m=_pick("max_drawdown_12m"),
            rights_issues_12m=meta.get("rights_issues_12m"),
            drawdown_from_high=_pick("drawdown_from_high"),
            single_day_spike_1y=_pick("single_day_spike_1y"),
            dividend_3y=meta.get("dividend_3y"),
            payout_ratio=meta.get("payout_ratio"),
            annualized_volatility_12m=_pick("annualized_volatility_12m"),
            fcf_to_net_profit=meta.get("fcf_to_net_profit"),
        )

        # 实时快照兜底（只补仍为空的字段）
        row_data = spot_map.get(symbol)
        if row_data is not None:
            if not raw.name:
                raw.name = str(row_data.get("中文名称") or "")
            if raw.price is None:
                raw.price = _safe_float(row_data.get("最新价"))
            if raw.market_cap is None:
                raw.market_cap = _safe_float(row_data.get("总市值"))
            if raw.pe_ttm is None:
                raw.pe_ttm = _safe_float(row_data.get("市盈率"))
            if raw.pb is None:
                raw.pb = _safe_float(row_data.get("市净率"))
            if raw.dividend_yield is None:
                raw.dividend_yield = _safe_float(row_data.get("股息率"))
            if raw.turnover_20d_avg is None:
                raw.turnover_20d_avg = _safe_float(row_data.get("成交额"))

        results.append(raw)
        if i % 500 == 0:
            logger.info(f"  已处理 {i}/{len(tickers)} 只港股...")

    logger.info(f"✅ 完成 {len(results)} 只港股原始数据获取")
    return results


def raw_to_screen_result(raw: HKRawData) -> HKScreenResult:
    """把 :class:`HKRawData` 转为 :class:`HKScreenResult`（同名字段直传）。"""
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
        payout_ratio=raw.payout_ratio,
        annualized_volatility_12m=raw.annualized_volatility_12m,
        fcf_to_net_profit=raw.fcf_to_net_profit,
    )
