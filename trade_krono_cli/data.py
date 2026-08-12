"""
数据层 — A 股 K 线获取 + Kronos 格式转换。
使用 baostock 作为数据源（免费、无需 key）。
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional

import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings
from trade_krono_cli.security import TokenBucket, validate_ticker, validate_date, retry
from trade_krono_cli.cache import get_cache

# baostock 惰性导入
_bs = None
_HAS_BS = False
_bs_logged_in = False
_bs_limiter: Optional[TokenBucket] = None
_bs_login_lock = threading.Lock()


def _get_limiter() -> TokenBucket:
    global _bs_limiter
    if _bs_limiter is None:
        s = get_settings()
        _bs_limiter = TokenBucket(
            rate=1.0 / s.baostock_sleep_sec,
            capacity=5.0,
        )
    return _bs_limiter


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
    return df


def next_business_days(last_date: str, n: int) -> list[pd.Timestamp]:
    """生成后续 n 个工作日（不含周末近似）。"""
    from pandas.tseries.offsets import BDay
    start = pd.Timestamp(last_date) + BDay(1)
    return [start + BDay(i) for i in range(n)]


def fetch_realtime_quote(ticker: str) -> dict:
    """
    腾讯财经实时估值（免费、无需 key）。
    返回 {price, pe, pb, market_cap, turnover} 或 {}。
    """
    ticker = validate_ticker(ticker)
    code = ticker.split(".")[-1]
    prefix = ticker.split(".")[0]

    import urllib.request
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        fields = raw.split("~")
        if len(fields) < 45:
            return {}
        return {
            "price":     float(fields[3])  if fields[3] else None,
            "pe":        float(fields[39]) if len(fields) > 39 and fields[39] else None,
            "pb":        float(fields[46]) if len(fields) > 46 and fields[46] else None,
            "market_cap": float(fields[44]) if len(fields) > 44 and fields[44] else None,
            "turnover":  float(fields[38]) if len(fields) > 38 and fields[38] else None,
        }
    except Exception as e:
        logger.warning(f"腾讯行情获取失败 {ticker}: {e}")
        return {}
