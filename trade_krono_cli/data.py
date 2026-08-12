"""
数据层 — A 股 K 线获取 + Kronos 格式转换。
使用 baostock 作为数据源（免费、无需 key）。
"""
from __future__ import annotations

import time
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional

import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.security import TokenBucket, validate_ticker, validate_date, retry
from trade_krono_cli.cache import get_cache

# baostock 惰性导入
_bs = None
_HAS_BS = False
_bs_logged_in = False
_bs_limiter: Optional[TokenBucket] = None
_bs_login_lock = threading.Lock()


def _get_limiter(settings: Optional[Settings] = None) -> TokenBucket:
    global _bs_limiter
    if _bs_limiter is None:
        s = settings or get_settings()
        _bs_limiter = TokenBucket(
            rate=1.0 / s.baostock_sleep_sec,
            capacity=5.0,
        )
    return _bs_limiter


def clear_baostock_globals() -> None:
    """
    重置 baostock 模块级状态，用于测试隔离。

    被清除的状态：
      - _bs             baostock 模块引用
      - _HAS_BS         baostock 是否已导入
      - _bs_logged_in   baostock 是否已登录
      - _bs_limiter     速率限制器
    """
    global _bs, _HAS_BS, _bs_logged_in, _bs_limiter
    _bs = None
    _HAS_BS = False
    _bs_logged_in = False
    _bs_limiter = None


def _ensure_bs_import() -> None:
    global _bs, _HAS_BS
    if _HAS_BS:
        return
    try:
        import baostock as _bs_mod  # type: ignore
        _bs = _bs_mod
        _HAS_BS = True
    except ImportError:
        raise RuntimeError(
            "baostock 未安装，无法拉取 K 线。请运行: pip install baostock"
        )


def _ensure_bs_login() -> None:
    global _bs_logged_in
    if not _HAS_BS:
        _ensure_bs_import()
    if _bs_logged_in:
        return
    with _bs_login_lock:
        # 双重检查：另一线程可能已在此等待锁期间完成登录
        if _bs_logged_in:
            return
        lg = _bs.login()  # type: ignore
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
    _bs_logged_in = True
    logger.info("✅ baostock 登录成功")


# ═══════════════════════════════════════════════════════
# 核心：拉取 K 线
# ═══════════════════════════════════════════════════════

@retry(max_attempts=3, base_delay=2.0, exceptions=(RuntimeError, ConnectionError))
def fetch_kline(
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str = "d",
    adjustflag: str = "1",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    拉取 A 股 K 线并转为 Kronos 标准格式。

    Returns
    -------
    DataFrame with columns: open, high, low, close, volume, amount, timestamps
    """
    ticker = validate_ticker(ticker)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    cache = get_cache()
    if use_cache:
        cached = cache.get_kline(ticker, start_date, end_date, frequency)
        if cached is not None:
            logger.debug(f"📦 K线缓存命中: {ticker} {start_date}~{end_date}")
            return cached

    _ensure_bs_import()
    _get_limiter().acquire()
    _ensure_bs_login()

    # baostock 格式: sh.600519 / sz.000858
    bs_code = ticker  # 已经是 sh.600519 或 sz.000858 格式

    logger.debug(f"📥 拉取 K 线: {bs_code} {start_date}~{end_date} freq={frequency}")

    rs = _bs.query_history_k_data_plus(  # type: ignore
        bs_code,
        "date,open,high,low,close,volume,amount",
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        adjustflag=adjustflag,
    )

    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败 [{bs_code}]: {rs.error_msg}")

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        raise RuntimeError(f"空数据: {bs_code} {start_date}~{end_date}")

    df = pd.DataFrame(rows, columns=rs.fields)
    df = df.dropna(subset=["close"])

    out = pd.DataFrame({
        "timestamps": pd.to_datetime(df["date"]),
        "open":   df["open"].astype(float),
        "high":   df["high"].astype(float),
        "low":    df["low"].astype(float),
        "close":  df["close"].astype(float),
        "volume": df["volume"].astype(float),
        "amount": df["amount"].astype(float),
    }).reset_index(drop=True)

    # 写缓存
    ttl = 3600 if frequency in ("5min", "15min", "30min", "60min") else 86400
    cache.set_kline(ticker, start_date, end_date, frequency, out, ttl=ttl)
    logger.debug(f"✅ K 线就绪: {ticker} 共 {len(out)} 行")

    return out


def fetch_lookback(
    ticker: str,
    end_date: str,
    lookback: int = 400,
    frequency: str = "d",
    buffer_days: int = 60,
    use_cache: bool = True,
    adjustflag: str = "1",
) -> pd.DataFrame:
    """
    自动计算 start_date，拉取足够历史数据。
    """
    ticker = validate_ticker(ticker)
    end_date = validate_date(end_date)
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if frequency == "d":
        start = end - timedelta(days=lookback * 2 + buffer_days)
    else:
        start = end - timedelta(days=lookback // 48 + 10)
    start_s = start.strftime("%Y-%m-%d")

    df = fetch_kline(ticker, start_s, end_date, frequency=frequency, adjustflag=adjustflag, use_cache=use_cache)
    if len(df) < lookback:
        raise RuntimeError(
            f"数据不足: {ticker} 仅 {len(df)} 行 < lookback {lookback}（检查停牌/新上市）"
        )
    # 校验数据末尾与评估日期的间隔，防止停牌期间数据过时
    validate_data_freshness(df, end_date, ticker)
    return df


def next_business_days(last_date: str, n: int) -> list[pd.Timestamp]:
    """生成后续 n 个工作日（不含周末近似）。"""
    from pandas.tseries.offsets import BDay
    start = pd.Timestamp(last_date) + BDay(1)
    return [start + BDay(i) for i in range(n)]


def validate_data_freshness(
    df: pd.DataFrame,
    eval_date: str,
    ticker: str,
    max_gap_trading_days: int = 10,
) -> None:
    """
    校验 K 线数据的最后交易日与评估日期的间隔。

    如果数据末尾距离评估日期超过 max_gap_trading_days 个交易日，
    说明股票在评估日前长时间停牌，不应参与预测。

    Raises
    ------
    RuntimeError : 数据过旧或不存在
    """
    if "timestamps" not in df.columns:
        raise RuntimeError(f"数据格式异常，缺少 timestamps 列: {ticker}")

    last_ts = pd.to_datetime(df["timestamps"].iloc[-1])
    eval_ts = pd.to_datetime(eval_date)

    if last_ts > eval_ts:
        raise RuntimeError(
            f"数据未来化: {ticker} 数据截止 {last_ts.date()} 晚于评估日期 {eval_ts.date()}"
        )

    # 计算两个日期之间的实际交易日数
    trading_days_gap = len(pd.bdate_range(start=last_ts, end=eval_ts)) - 1

    if trading_days_gap > max_gap_trading_days:
        raise RuntimeError(
            f"数据过旧: {ticker} 最后交易日 {last_ts.date()} 与评估日 {eval_ts.date()} "
            f"相差 {trading_days_gap} 个交易日（阈值 {max_gap_trading_days}），"
            f"疑似停牌或退市"
        )

    logger.debug(
        f"✅ 数据新鲜度校验通过: {ticker} 最后交易日={last_ts.date()}, "
        f"与评估日间隔 {trading_days_gap} 个交易日"
    )


def _safe_float(value: str, default: Optional[float] = None) -> Optional[float]:
    """
    安全地将字符串解析为 float，失败时返回 default。
    腾讯行情接口可能返回空串、'--' 等占位符，不应让整个函数失败。
    """
    if not value:
        return default
    try:
        f = float(value)
        return f if not (f != f or f == float('inf') or f == float('-inf')) else default
    except (ValueError, TypeError):
        return default


# 腾讯财经接口字段索引（qt.gtimg.cn 协议）
# https://stock.finance.sina.com.cn/
# 索引对应字段如下（部分字段可能因股票类型不同而缺失）：
_TQ_PRICE     = 3    # 当前价（元）
_TQ_PE        = 39   # 市盈率（动态）
_TQ_PB        = 46   # 市净率
_TQ_MKT_CAP   = 44   # 总市值（亿元）
_TQ_TURNOVER  = 38   # 换手率（%）
# 最小字段数：需至少包含 _TQ_PRICE（索引 3）+ 分隔符，取保守阈值 45
_TQ_MIN_FIELDS = 45


def fetch_realtime_quote(ticker: str) -> dict:
    """
    腾讯财经实时估值（免费、无需 key）。
    返回 {price, pe, pb, market_cap, turnover} 或 {}。

    字段均安全解析：字段缺失或非数字时该项为 None，不抛异常。
    """
    ticker = validate_ticker(ticker)
    code = ticker.split(".")[-1]
    prefix = ticker.split(".")[0]

    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except (urllib.error.URLError, OSError):
        return {}

    fields = raw.split("~")
    if len(fields) < _TQ_MIN_FIELDS:
        return {}

    return {
        "price":      _safe_float(fields[_TQ_PRICE]),
        "pe":         _safe_float(fields[_TQ_PE])         if len(fields) > _TQ_PE else None,
        "pb":         _safe_float(fields[_TQ_PB])         if len(fields) > _TQ_PB else None,
        "market_cap": _safe_float(fields[_TQ_MKT_CAP])    if len(fields) > _TQ_MKT_CAP else None,
        "turnover":   _safe_float(fields[_TQ_TURNOVER])   if len(fields) > _TQ_TURNOVER else None,
    }
