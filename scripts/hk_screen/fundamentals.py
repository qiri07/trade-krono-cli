"""scripts/hk_screen/fundamentals.py — 港股财务基本面采集

从 akshare 东方财富港股接口提取筛选器所需的基本面字段：

- ``stock_hk_security_profile_em``      上市日期 / 证券类型 / 沪深港通标的
- ``stock_financial_hk_analysis_indicator_em``  年报口径的 ROE / 毛利率 / 负债率 /
  每股经营现金流 / 营收增速 / 归母净利
- ``stock_hk_financial_indicator_em``   股息率 TTM / 派息比率 / 每股股息 / 每股收益
- ``stock_hk_dividend_payout_em``       分红派息历史（判断近 3 年连续派息）

设计约定：
- 所有派生字段在数据不足时返回 ``None``（未知），**不使用 0/False 表示未知**；
- 单位统一为比例（``0.15`` 表示 15%），与 ``HK_FUNDAMENTAL_RULES`` 阈值一致；
- 该模块只做「取数 + 归一化」，不做筛选判断。
"""

from __future__ import annotations

from typing import Any  # 各接口返回列类型混杂（str/float/Timestamp），Any 为合理

from loguru import logger

# 近 3 年 = 最近 3 个年报期
_YEARS = 3


def _to_float(val: Any) -> float | None:  # 输入为 DataFrame 单元格，类型混杂
    """安全转换为 float；None / NaN / 非数值返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN 自比较为 False
        return None
    return f


def _ratio(val: Any, scale: float = 100.0) -> float | None:  # 输入为 DataFrame 单元格
    """把百分数（如 21.13）转为比例（0.2113）；无效返回 None。"""
    f = _to_float(val)
    return None if f is None else f / scale


def _col(row: dict[str, Any], name: str) -> Any:
    """按列名取单元格，缺失返回 None。"""
    return row.get(name)


def _annual_rows(symbol: str) -> list[dict[str, Any]]:
    """获取港股年报口径财务指标（按报告期降序）。失败返回空列表。"""
    import akshare as ak

    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol, indicator="年度")
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.debug(f"港股 {symbol} 财务指标获取失败: {e}")
        return []
    if df is None or len(df) == 0:
        return []

    df = df.copy()
    if "REPORT_DATE" in df.columns:
        df["_rd"] = df["REPORT_DATE"].astype(str)
        df = df.sort_values("_rd", ascending=False)
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


def _dividend_years(symbol: str) -> set[str]:
    """返回有年度现金分派记录的财政年度集合。失败返回空集合。"""
    import akshare as ak

    try:
        df = ak.stock_hk_dividend_payout_em(symbol=symbol)
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.debug(f"港股 {symbol} 分红记录获取失败: {e}")
        return set()
    if df is None or len(df) == 0:
        return set()

    years: set[str] = set()
    for row in df.to_dict(orient="records"):  # type: ignore[union-attr]
        plan = str(row.get("分红方案") or "")
        kind = str(row.get("分配类型") or "")
        fiscal = str(row.get("财政年度") or "").strip()
        if not fiscal:
            continue
        if "派" not in plan and "息" not in plan:
            continue
        if kind and "年度" not in kind and "中期" not in kind and "特别" not in kind:
            continue
        years.add(fiscal)
    return years


def fetch_hk_fundamentals(symbol: str) -> dict[str, Any]:  # 派生指标值类型混杂
    """采集单只港股的财务基本面指标。

    Args:
        symbol: 5 位港股代码（如 ``"00700"``），不含 ``hk.`` 前缀。

    Returns:
        字段字典，键为 ``hk_metadata`` 列名；无法判定的字段值为 ``None``：

        - ``listing_date`` / ``listing_days`` / ``is_common_stock`` / ``is_hkd``
        - ``is_hk_connect``（沪港通或深港通标的）
        - ``debt_to_asset`` / ``gross_margin`` / ``avg_roe_3y``
        - ``operating_cashflow_3y`` / ``revenue_growth_3y`` / ``non_gaap_profit_2y``
        - ``dividend_yield`` / ``dividend_3y`` / ``payout_ratio``
        - ``fcf_to_net_profit``
    """
    from trade_krono_cli.data_providers.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    out: dict[str, Any] = {
        "listing_date": None,
        "listing_days": None,
        "is_common_stock": None,
        "is_hkd": None,
        "is_hk_connect": None,
        "pe_ttm": None,
        "pb": None,
        "market_cap": None,
        "debt_to_asset": None,
        "gross_margin": None,
        "avg_roe_3y": None,
        "operating_cashflow_3y": None,
        "revenue_growth_3y": None,
        "non_gaap_profit_2y": None,
        "dividend_yield": None,
        "dividend_3y": None,
        "payout_ratio": None,
        "fcf_to_net_profit": None,
    }

    # ── 1. 证券档案：上市日期 / 标的类型 / 沪深港通 ─────────────────────
    import akshare as ak

    limiter.acquire("akshare_hk")
    try:
        profile = ak.stock_hk_security_profile_em(symbol=symbol)
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.debug(f"港股 {symbol} 证券档案获取失败: {e}")
        profile = None

    if profile is not None and len(profile) > 0:
        row = profile.iloc[0].to_dict()
        listing = row.get("上市日期")
        listing_date = str(listing)[:10] if listing is not None else None
        if listing_date and listing_date.startswith("NaT"):
            listing_date = None
        out["listing_date"] = listing_date
        if listing_date:
            from datetime import date

            try:
                y, m, d = (int(x) for x in listing_date.split("-"))
                out["listing_days"] = (date.today() - date(y, m, d)).days
            except (ValueError, TypeError):
                out["listing_days"] = None

        sec_type = str(row.get("证券类型") or "")
        if sec_type:
            out["is_common_stock"] = "股" in sec_type and "ETF" not in sec_type

        exchange = str(row.get("交易所") or "")
        if exchange:
            out["is_hkd"] = "港" in exchange

        sh_connect = row.get("是否沪港通标的")
        sz_connect = row.get("是否深港通标的")
        if sh_connect is not None or sz_connect is not None:
            out["is_hk_connect"] = bool(sh_connect == "是" or sz_connect == "是")

    # ── 2. 财务指标（年报口径）─────────────────────────────────────────
    limiter.acquire("akshare_hk")
    rows = _annual_rows(symbol)
    if rows:
        head = rows[:_YEARS]
        latest = rows[0]

        out["debt_to_asset"] = _ratio(_col(latest, "DEBT_ASSET_RATIO"))
        out["gross_margin"] = _ratio(_col(latest, "GROSS_PROFIT_RATIO"))

        roes: list[float] = []
        for r in head:
            roe = _ratio(_col(r, "ROE_AVG"))
            if roe is not None:
                roes.append(roe)
        if roes:
            out["avg_roe_3y"] = sum(roes) / len(roes)

        # 近 3 年每股经营现金流是否均为正
        ocfs = [_to_float(_col(r, "PER_NETCASH_OPERATE")) for r in head]
        if len(ocfs) == _YEARS and all(v is not None for v in ocfs):
            out["operating_cashflow_3y"] = all(v > 0 for v in ocfs if v is not None)

        # 近 3 年营收是否「连续负增长」
        yoys = [_to_float(_col(r, "OPERATE_INCOME_YOY")) for r in head]
        if len(yoys) == _YEARS and all(v is not None for v in yoys):
            out["revenue_growth_3y"] = not all(v < 0 for v in yoys if v is not None)

        # 扣非盈利：港股接口无「扣非」口径，用归母净利润为正作为代理
        profits = [_to_float(_col(r, "HOLDER_PROFIT")) for r in rows[:2]]
        if len(profits) == 2 and all(v is not None for v in profits):
            out["non_gaap_profit_2y"] = all(v > 0 for v in profits if v is not None)

        # 经营现金流 / 净利润（用每股口径近似）
        ocf = _to_float(_col(latest, "PER_NETCASH_OPERATE"))
        eps = _to_float(_col(latest, "BASIC_EPS"))
        if ocf is not None and eps is not None and eps > 0:
            out["fcf_to_net_profit"] = ocf / eps

    # ── 3. 股息指标 ────────────────────────────────────────────────────
    limiter.acquire("akshare_hk")
    try:
        indicator = ak.stock_hk_financial_indicator_em(symbol=symbol)
    except Exception as e:  # akshare 网络层异常类型不稳定
        logger.debug(f"港股 {symbol} 股息指标获取失败: {e}")
        indicator = None

    if indicator is not None and len(indicator) > 0:
        row = indicator.iloc[0].to_dict()
        out["dividend_yield"] = _ratio(row.get("股息率TTM(%)"))
        out["payout_ratio"] = _ratio(row.get("派息比率(%)"))
        # 行情类指标（实时快照不可用时的主来源）
        out["pe_ttm"] = _to_float(row.get("市盈率"))
        out["pb"] = _to_float(row.get("市净率"))
        out["market_cap"] = _to_float(row.get("总市值(港元)")) or _to_float(
            row.get("总市值")
        )

    limiter.acquire("akshare_hk")
    years = _dividend_years(symbol)
    if years:
        out["dividend_3y"] = len(years) >= 3

    return out
