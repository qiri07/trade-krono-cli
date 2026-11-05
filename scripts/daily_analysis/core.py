#!/usr/bin/env python3
"""daily_analysis 技术分析核心模块。

除权检测 + MA/量能/趋势/买卖点信号 + 综合评分 + 估值评估，
以及除权基准一致性守卫与 PE/PB 抓取。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from scripts._utils import check_kline_regime
from scripts.daily_analysis.kline import _fetch_and_save_kline, get_kline
from scripts.daily_analysis.names import _get_stock_name, _preload_stock_names
from scripts.valuation import _safe_float, evaluate_valuation
from trade_krono_cli.errors import DataInconsistencyError


def _check_regime_guard(ticker: str, df: pd.DataFrame, end_date: str) -> None:
    """复权基准一致性守卫：检测 K 线最近 20 日是否存在 >50% 单日跳变。

    命中时记录 warning（不阻断分析，由下游除权检测兜底处理），并提示数据需重拉对齐。
    """
    try:
        check_kline_regime(ticker, df, window=20)
    except DataInconsistencyError as e:
        logger.warning(f"⚠️ {ticker} 复权基准漂移，数据需重拉对齐（end_date={end_date}）: {e}")


def analyze_stock(ticker: str, end_date: str | None = None) -> dict[str, Any]:
    """对单只股票做基础技术分析。

    数据获取策略（三级回退）：
      1. 本地缓存（KlineCache 日期范围查询）
      2. 本地缓存（全量读取）
      3. 外部数据源拉取并写入缓存
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # 进程级名称预加载（幂等，多线程下仅首次实际拉取全表），
    # 避免 6000+ 只逐只触发 baostock 网络查询导致并发池挂起
    _preload_stock_names()

    # 第 1 步：尝试从缓存读取（带日期范围）
    df = get_kline(ticker, start="2020-01-01", end=end_date)
    if df is not None and len(df) >= 20:
        _check_regime_guard(ticker, df, end_date)
        return _run_analysis(ticker, df, end_date)

    # 第 2 步：尝试全量缓存读取（兼容旧格式）
    df = get_kline(ticker)
    if df is not None and len(df) >= 20:
        _check_regime_guard(ticker, df, end_date)
        return _run_analysis(ticker, df, end_date)

    # 第 3 步：从外部数据源拉取
    logger.info(f"{ticker} 本地无缓存，尝试从外部源拉取...")
    df = _fetch_and_save_kline(ticker, end_date)
    if df is not None and len(df) >= 20:
        _check_regime_guard(ticker, df, end_date)
        return _run_analysis(ticker, df, end_date)

    name = _get_stock_name(ticker)
    return {
        "ticker": ticker,
        "name": name,
        "status": "no_data",
        "score": 0.0,
        "trend": "数据不足",
        "ma_signal": "无",
        "vol_signal": "无",
    }


def _fetch_pe_data(ticker: str) -> dict[str, float | None]:
    """从同花顺 API 获取单只股票的 PE_TTM 和 PB。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）

    Returns
    -------
    dict[str, float | None]
        {"pe_ttm": float | None, "pb": float | None}
    """
    import subprocess

    api_key = (
        os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
    )
    if not api_key:
        return {"pe_ttm": None, "pb": None}

    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    # 港股（hk.）不使用同花顺 A 股估值 API
    if ticker.startswith("hk."):
        return {"pe_ttm": None, "pb": None}
    prefix = "sh" if ticker.startswith("sh.") else "sz" if ticker.startswith("sz.") else "bj"
    thscode = f"{code}.{prefix.upper()}"

    try:
        url = f"https://fuyao.aicubes.cn/api/a-share/valuations/snapshot?thscodes={thscode}"
        cmd = [
            "curl",
            "-s",
            "--max-time",
            "8",
            "-w",
            "\n%{http_code}",
            url,
            "-H",
            f"X-api-key: {api_key}",
            "-A",
            "DailyAnalysis/1.0",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        stdout = r.stdout.strip()
        nl = stdout.rfind("\n")
        if nl < 0:
            return {"pe_ttm": None, "pb": None}
        body, http_code = stdout[:nl], int(stdout[nl + 1 :])
        if http_code != 200:
            return {"pe_ttm": None, "pb": None}
        data = json.loads(body)
        if data.get("code") != 0:
            return {"pe_ttm": None, "pb": None}
        item = data.get("data", {}).get("item", [{}])[0]
        pe = _safe_float(item.get("pe_ttm"))
        pb = _safe_float(item.get("pb_mrq"))
        return {"pe_ttm": pe, "pb": pb}
    except Exception as e:
        logger.debug(f"{ticker} PE 获取失败: {e}")
        return {"pe_ttm": None, "pb": None}


def _run_analysis(ticker: str, df: pd.DataFrame, end_date: str) -> dict[str, Any]:
    """基于已有 DataFrame 执行技术分析。"""
    if len(df) < 20:
        return {
            "status": "no_data",
            "ticker": ticker,
            "name": _get_stock_name(ticker),
            "score": 0.0,
            "close": None,
            "change": None,
            "exrights": False,
            "trend": "数据不足",
            "ma_signal": "无",
            "vol_signal": "无",
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "buy_points": [],
            "sell_points": [],
            "high_20": None,
            "low_20": None,
            "pe_ttm": None,
            "pb": None,
            "valuation_score": None,
            "buy_price_suggested": None,
            "valuation_conclusion": "",
        }

    ts_col = "timestamps" if "timestamps" in df.columns else "date"
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)

    close = df["close"].astype(float)
    latest = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else latest

    # ── 除权检测：最近 5 个交易日内出现过 >30% 单日跳变即视为除权日 ──
    # 前复权序列中除权跳变后价格体系切换，跳变后的行与跳变前行不可比，
    # 统一取「最近一次除权日及之前」为有效技术计算基准。
    _recent5 = close.iloc[-5:] if len(close) >= 5 else close
    _recent5_chg = _recent5.pct_change().abs()
    _ex_mask = _recent5_chg > 0.30
    _is_exrights = bool(_ex_mask.any())
    if _is_exrights:
        # 除权日在序列中的全局位置：_ex_mask 是 recent5 上的布尔掩码，
        # 先定位到 recent5 内的首个跳变局部位置，再映射回 close 的全局位置。
        # 取首个跳变而非末个：跳变后价格体系已切换，后续所有"小跳变"
        # 都发生在同一新体系内，无意义；真正需要回退的是首个切换点。
        _local_ex = _ex_mask.reset_index(drop=True)
        _first_local = int(_local_ex.idxmin()) if bool(_local_ex.any()) else len(close) - 1
        _last_ex_pos = max(0, len(close) - 5) + _first_local
        daily_change = (
            (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
            if len(close) > 1 and close.iloc[-2] > 0
            else 0.0
        )
        logger.info(
            f"📌 {ticker} 检测到除权日：最近5日最大跳变 {float(_recent5_chg.max()):+.1f}%，"
            f"技术指标将基于除权前一日收盘价（{close.iloc[_last_ex_pos - 1]:.2f}）计算"
            if _last_ex_pos >= 1
            else f"📌 {ticker} 检测到除权日，数据不足无法回退"
        )
        # 有效收盘价：最近一次除权日的前一日
        _effective_close = close.iloc[_last_ex_pos - 1] if _last_ex_pos >= 1 else latest
        # 有效计算序列：除权日（含）之前的全部数据
        _ma_src = close.iloc[:_last_ex_pos] if _last_ex_pos >= 1 else close
    else:
        daily_change = (latest - prev) / prev * 100 if prev > 0 else 0
        _effective_close = latest
        _ma_src = close
    _sig_price = _effective_close
    daily_change_display = 0.0 if _is_exrights else daily_change

    # 均线（基于有效收盘价序列，避免除权日污染）
    ma5 = _ma_src.rolling(5).mean().iloc[-1] if len(_ma_src) >= 5 else latest
    ma10 = _ma_src.rolling(10).mean().iloc[-1] if len(_ma_src) >= 10 else latest
    ma20 = _ma_src.rolling(20).mean().iloc[-1] if len(_ma_src) >= 20 else latest

    # 均线信号（使用有效收盘价）
    if _sig_price > ma5 > ma10 > ma20:
        ma_signal = "多头排列✅"
    elif _sig_price < ma5 < ma10 < ma20:
        ma_signal = "空头排列❌"
    elif _sig_price > ma5 and _sig_price > ma10:
        ma_signal = "偏多"
    elif _sig_price < ma5 and _sig_price < ma10:
        ma_signal = "偏空"
    else:
        ma_signal = "震荡"

    # 趋势判断（近5日涨跌，基于有效序列，排除除权跳变）
    _trend_src = _ma_src.iloc[-5:]
    up_days = sum(
        1 for i in range(1, len(_trend_src)) if _trend_src.iloc[i] > _trend_src.iloc[i - 1]
    )
    if up_days >= 4:
        trend = "强势上涨📈"
    elif up_days >= 3:
        trend = "震荡偏多"
    elif up_days <= 1:
        trend = "弱势下跌📉"
    else:
        trend = "横盘整理"

    # 量能（近5日均量 vs 近20日均量，基于有效序列，排除除权跳变）
    vol_col = "volume" if "volume" in df.columns else None
    vol_signal = "无"
    buy_points: list[str] = []
    sell_points: list[str] = []

    if vol_col:
        vol = df[vol_col].astype(float)
        _vol_src = vol.iloc[: len(_ma_src)] if _is_exrights else vol
        avg_vol_5 = _vol_src.iloc[-5:].mean()
        avg_vol_20 = _vol_src.iloc[-20:].mean()
        if avg_vol_20 > 0:
            vol_ratio = avg_vol_5 / avg_vol_20
            if vol_ratio > 1.5:
                vol_signal = "放量🔥"
            elif vol_ratio < 0.6:
                vol_signal = "缩量📉"
            else:
                vol_signal = "正常"

    # ── 买点 / 卖点 信号分析 ─────────────────────────────────────────────
    high_col = "high" if "high" in df.columns else None
    low_col = "low" if "low" in df.columns else None
    if high_col and low_col:
        _sub = df.iloc[-30:].copy()
        # 若检测到除权日，剔除除权跳变日及之后的行（新价格体系），只保留跳变前数据
        if _is_exrights:
            _clean = _sub[_sub.index < _last_ex_pos]
            _sub = _clean if len(_clean) >= 5 else _sub
        high_20 = float(_sub[high_col].astype(float).max())
        low_20 = float(_sub[low_col].astype(float).min())
    else:
        high_20 = _sig_price
        low_20 = _sig_price

    # MA5/MA10 金叉 / 死叉（最近5日内出现）
    ma5_series = _ma_src.rolling(5).mean()
    ma10_series = _ma_src.rolling(10).mean()
    for i in range(-5, 0):
        if i - 1 < -len(ma5_series):
            continue
        if (
            ma5_series.iloc[i] > ma10_series.iloc[i]
            and ma5_series.iloc[i - 1] <= ma10_series.iloc[i - 1]
        ):
            buy_points.append("MA5金叉MA10")
            break
        if (
            ma5_series.iloc[i] < ma10_series.iloc[i]
            and ma5_series.iloc[i - 1] >= ma10_series.iloc[i - 1]
        ):
            sell_points.append("MA5死叉MA10")
            break

    # 超卖反弹：价格接近20日最低点（±3%）
    dist_to_low = (_sig_price - low_20) / low_20 * 100 if low_20 > 0 else 999
    if dist_to_low < 3 and dist_to_low > -1:
        buy_points.append(f"20日低位（距低点{dist_to_low:+.1f}%）")

    # 超买回调：价格远离MA5（>4%）
    dist_to_ma5 = (_sig_price - ma5) / ma5 * 100 if ma5 > 0 else 0
    if dist_to_ma5 > 4:
        sell_points.append(f"远离MA5（偏高{dist_to_ma5:.1f}%）")

    # 放量突破压力：量能放大 + 突破20日高点
    if vol_col and avg_vol_20 > 0:
        _vol_for_today = _vol_src.iloc[-1] if not _is_exrights else 0.0
        vol_ratio_today = _vol_for_today / avg_vol_20 if avg_vol_20 > 0 else 0
        if vol_ratio_today > 1.5 and _sig_price > high_20:
            buy_points.append("放量突破20日高点")
        elif vol_ratio_today > 1.5 and abs(daily_change_display) < 0.5:
            sell_points.append("放量滞涨")

    # 支撑/压力位
    if _sig_price < ma20 * 0.97:
        sell_points.append(f"远低于MA20（{((_sig_price / ma20 - 1) * 100):+.1f}%）")
    elif abs(_sig_price - ma20) / ma20 * 100 < 1:
        buy_points.append("MA20附近支撑")

    # ── 综合评分（0-100） ────────────────────────────────────────────────
    score = 50.0
    if ma_signal == "多头排列✅":
        score += 25
    elif ma_signal == "偏多":
        score += 10
    elif ma_signal == "空头排列❌":
        score -= 25
    elif ma_signal == "偏空":
        score -= 10

    if trend == "强势上涨📈":
        score += 10
    elif trend == "弱势下跌📉":
        score -= 10

    if vol_signal == "放量🔥":
        score += 5
    elif vol_signal == "缩量📉":
        score -= 5

    score = max(0, min(100, score))

    # ── 估值评估 ────────────────────────────────────────────────────
    pe_pb = _fetch_pe_data(ticker)
    pe_ttm = pe_pb.get("pe_ttm")
    pb = pe_pb.get("pb")
    valuation_score: float | None = None
    buy_price_suggested: float | None = None
    valuation_conclusion = ""

    if pe_ttm is not None and pe_ttm > 0 and _sig_price > 0:
        try:
            # 反推 EPS 和每股净资产
            eps_ttm = _sig_price / pe_ttm if pe_ttm > 0 else None
            bvps = _sig_price / pb if pb is not None and pb > 0 else None

            # 获取 PE 历史分位（来自 akshare）
            pe_percentile: float | None = None
            try:
                import akshare as _ak

                thscode = ticker.replace("sh.", "").replace("sz.", "")
                df_pe = _ak.stock_value_em(symbol=thscode)
                if df_pe is not None and len(df_pe) > 0:
                    pe_col = "PE(TTM)"
                    pe_values = df_pe[pe_col].dropna().astype(float)
                    pe_values = pe_values[pe_values > 0]
                    if len(pe_values) > 0:
                        current_pe = float(pe_values.iloc[-1])
                        count_below = int((pe_values < current_pe).sum())
                        pe_percentile = round((count_below / len(pe_values)) * 100.0, 1)
            except Exception as e:
                logger.warning(f"⚠️  PE 分位获取失败（{ticker}）: {e}，不影响主流程")

            val_result = evaluate_valuation(
                ticker=thscode,
                name=_get_stock_name(ticker) or "",
                current_price=_sig_price,
                pe_ttm=pe_ttm,
                pb=pb,
                roe=None,  # 需从财务数据获取，暂不传入
                pe_percentile=pe_percentile,
                eps_ttm=eps_ttm,
                book_value_per_share=bvps,
            )
            valuation_score = val_result.valuation_score
            buy_price_suggested = val_result.combined_price
            valuation_conclusion = val_result.conclusion
        except Exception as e:
            logger.debug(f"{ticker} 估值分析失败: {e}")

    return {
        "ticker": ticker,
        "name": _get_stock_name(ticker),
        "status": "ok",
        "score": round(score, 1),
        "close": round(_sig_price, 2),
        "change": round(daily_change_display, 2),
        "exrights": _is_exrights,
        "trend": trend,
        "ma_signal": ma_signal,
        "vol_signal": vol_signal,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "buy_points": buy_points,
        "sell_points": sell_points,
        "high_20": round(high_20, 2),
        "low_20": round(low_20, 2),
        # ── 估值评估 ────────────────────────────────────────────────────
        "pe_ttm": pe_ttm,
        "pb": pb,
        "valuation_score": valuation_score,
        "buy_price_suggested": buy_price_suggested,
        "valuation_conclusion": valuation_conclusion,
    }
