"""six_dim 选股 — 技术面维度（位置 / 趋势 / 量价）。

负责从 K 线缓存读取数据并计算位置、趋势、量价指标，以及相应的打分。
"""

from __future__ import annotations

import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from loguru import logger

from scripts.six_dim.constants import POSITION_WINDOW_DAYS


def _compute_technicals(df: pd.DataFrame) -> dict[str, float] | None:
    """从日线 DataFrame 计算位置 / 趋势 / 量价指标。

    Args:
        df: 含 ``timestamps/open/high/low/close/volume/amount`` 的日线数据。

    Returns:
        指标字典；数据不足 130 根时返回 ``None``。
    """
    if df is None or len(df) < 130:
        return None
    data = df.sort_values("timestamps").reset_index(drop=True)
    close = pd.to_numeric(data["close"], errors="coerce")
    amount = pd.to_numeric(data["amount"], errors="coerce")
    if close.isna().all() or amount.isna().all():
        return None

    ma = {n: close.rolling(n).mean() for n in (5, 10, 20, 60, 120)}
    last_close = float(close.iloc[-1])

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    window = min(POSITION_WINDOW_DAYS, len(close))
    win_close = close.iloc[-window:]
    low_n: float = float(win_close.min())  # type: ignore[assignment]
    high_n: float = float(win_close.max())  # type: ignore[assignment]
    pct_pos = (last_close - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50.0

    win1y = close.iloc[-250:] if len(close) >= 250 else close
    low1y: float = float(win1y.min())  # type: ignore[assignment]
    high1y: float = float(win1y.max())  # type: ignore[assignment]
    pct_pos_1y = (last_close - low1y) / (high1y - low1y) * 100 if high1y > low1y else 50.0

    ma60_slope = (
        (ma[60].iloc[-1] / ma[60].iloc[-21] - 1) * 100 if len(ma[60].dropna()) > 21 else 0.0
    )
    ma20_slope = (
        (ma[20].iloc[-1] / ma[20].iloc[-11] - 1) * 100 if len(ma[20].dropna()) > 11 else 0.0
    )

    amt5 = float(amount.iloc[-5:].mean())
    amt60 = float(amount.iloc[-60:].mean())
    amt_ratio = amt5 / amt60 if amt60 > 0 else 1.0

    last20 = data.iloc[-20:]
    up_mask = last20["close"] >= last20["open"]
    up_amt = float(last20.loc[up_mask, "amount"].sum())
    down_amt = float(last20.loc[~up_mask, "amount"].sum())
    updown_ratio = up_amt / down_amt if down_amt > 0 else 2.0

    ret5 = (last_close / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0.0
    ret20 = (last_close / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0

    return {
        "close": last_close,
        "ma5": float(ma[5].iloc[-1]),
        "ma10": float(ma[10].iloc[-1]),
        "ma20": float(ma[20].iloc[-1]),
        "ma60": float(ma[60].iloc[-1]),
        "ma120": float(ma[120].iloc[-1]),
        "ma20_slope": ma20_slope,
        "ma60_slope": ma60_slope,
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "pct_pos_3y": max(0.0, min(100.0, pct_pos)),
        "pct_pos_1y": max(0.0, min(100.0, pct_pos_1y)),
        "high_3y": high_n,
        "low_3y": low_n,
        "amt_ratio": amt_ratio,
        "updown_amt_ratio": updown_ratio,
        "ret5": ret5,
        "ret20": ret20,
        "bars": float(len(data)),
        "last_bar_ts": float(pd.to_datetime(data["timestamps"].iloc[-1]).timestamp()),
    }


def load_technicals(
    tickers: dict[str, str],
    db_path: Path,
    workers: int = 8,
    asof: str | None = None,
) -> dict[str, dict[str, float]]:
    """并发从 K 线缓存读取并计算技术指标。

    Args:
        tickers: ``code -> 带前缀 ticker`` 映射。
        db_path: ``pipeline_cache.db`` 路径。
        workers: 并发线程数。
        asof: 分析日期（YYYY-MM-DD）；给出时只用该日及之前的 K 线，保证历史回放不使用未来数据。

    Returns:
        ``code -> 技术指标字典``；读取失败或数据不足的股票不会出现在结果中。
    """

    def _one(item: tuple[str, str]) -> tuple[str, dict[str, float] | None]:
        code, ticker = item
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
            try:
                rows = conn.execute(
                    "SELECT data FROM kline_cache WHERE ticker=? AND freq='d' ORDER BY end",
                    (ticker,),
                ).fetchall()
            finally:
                conn.close()
            if not rows:
                return code, None
            frames = [pd.read_pickle(io.BytesIO(r[0])) for r in rows]
            df = pd.concat(frames, ignore_index=True)
            df["timestamps"] = pd.to_datetime(df["timestamps"])
            df = df.sort_values("timestamps").drop_duplicates("timestamps", keep="last")
            if asof:
                df = df[df["timestamps"] <= pd.Timestamp(asof)]
            if df.empty:
                return code, None
            return code, _compute_technicals(df.reset_index(drop=True))
        except (sqlite3.Error, OSError, ValueError, KeyError):
            return code, None

    out: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, res in ex.map(_one, tickers.items()):
            if res is not None:
                out[code] = res
    logger.info(f"技术面：成功计算 {len(out)}/{len(tickers)} 只的位置/趋势/量价指标")
    return out


def score_position(tech: dict[str, float]) -> tuple[float, str]:
    """位置维度打分。

    股价在 3 年区间中的分位，偏中低位（35% 左右）最优，历史高位（>85%）扣分。

    Args:
        tech: :func:`_compute_technicals` 返回的指标。

    Returns:
        ``(0~100 分, 说明文本)``。
    """
    pct_pos = tech.get("pct_pos_3y", 50.0)
    if pct_pos <= 35.0:
        score = 100 - abs(35.0 - pct_pos) * 2
    elif pct_pos <= 60.0:
        score = 100 - (pct_pos - 35.0) * 1.5
    else:
        score = max(0, 100 - (pct_pos - 60.0) * 3)
    return round(score, 1), f"3年分位 {pct_pos:.1f}%"


def trend_gate_and_score(tech: dict[str, float]) -> tuple[bool, float, str]:
    """趋势门槛 + 打分。

    明确下跌趋势（价格跌破 MA60/MA120 且 MA60 向下）时淘汰。

    Args:
        tech: :func:`_compute_technicals` 返回的指标。

    Returns:
        ``(是否通过, 0~100 分, 淘汰原因或通过说明)``。
    """
    close = tech.get("close", 0.0)
    ma60 = tech.get("ma60", 0.0)
    ma120 = tech.get("ma120", 0.0)
    ma60_slope = tech.get("ma60_slope", 0.0)

    if close < ma60 and ma60_slope < 0:
        return False, 0.0, f"下跌趋势：价格跌破 MA60 且 MA60 向下"
    if close < ma120:
        return False, 0.0, "下跌趋势：价格跌破 MA120"

    # 打分：均线排列占 40%、MA60 斜率占 30%、MACD 占 30%
    ma5 = tech.get("ma5", 0.0)
    ma10 = tech.get("ma10", 0.0)
    ma20 = tech.get("ma20", 0.0)
    ma_align = 0
    if close > ma5 > ma10 > ma20:
        ma_align = 40
    elif close > ma5 > ma10:
        ma_align = 30
    elif close > ma5:
        ma_align = 20

    dif = tech.get("dif", 0.0)
    dea = tech.get("dea", 0.0)
    macd_score = 30 if dif > 0 and dif > dea else (15 if dif > 0 else 0)
    slope_score = min(max(ma60_slope / 5.0 * 30, 0), 30)

    score = ma_align + slope_score + macd_score
    trend_txt = "多头排列" if close > ma5 > ma10 > ma20 else "弱势/震荡"
    return True, round(score, 1), f"{trend_txt}，MA60 20日斜率 {ma60_slope:+.1f}%，DIF={dif:+.2f}"


def score_vol_price(tech: dict[str, float]) -> tuple[float, str]:
    """量价关系打分。

    成交额 5 日/60 日倍数在 1.0~1.8 之间为健康，放量下跌扣分。

    Args:
        tech: :func:`_compute_technicals` 返回的指标。

    Returns:
        ``(0~100 分, 说明文本)``。
    """
    amt_ratio = tech.get("amt_ratio", 1.0)
    updown_ratio = tech.get("updown_amt_ratio", 1.0)
    ret5 = tech.get("ret5", 0.0)

    vol_score: float = 50.0
    if 1.0 <= amt_ratio <= 1.8:
        vol_score = 50.0
    elif amt_ratio < 1.0:
        vol_score = max(0.0, 50.0 - (1.0 - amt_ratio) * 50.0)
    else:
        vol_score = max(0.0, 50.0 - (amt_ratio - 1.8) * 30.0)

    price_score: float = 50.0
    if ret5 > 0 and updown_ratio > 1.0:
        price_score = 50.0
    elif ret5 <= 0 and updown_ratio < 1.0:
        price_score = 20.0
    else:
        price_score = 35.0

    score = vol_score + price_score
    return round(score, 1), f"成交额倍数 {amt_ratio:.1f}×，涨跌量能比 {updown_ratio:.1f}"
