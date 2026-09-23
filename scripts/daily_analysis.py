#!/usr/bin/env python3
"""白名单股票综合分析 + AI 核实 + 飞书推送。

基于缓存 K 线数据进行基础技术分析（MA/量能/涨跌），
支持在 TA/Kronos 外部模块不可用时也能正常输出分析结果。

用法：
  uv run python scripts/daily_analysis.py
  uv run python scripts/daily_analysis.py --date 2026-09-15
  uv run python scripts/daily_analysis.py --tickers "000001,002027"
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts.feishu_core import load_config, send_notification
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands._core_helpers import _load_env
from trade_krono_cli.config import get_settings  # type: ignore[import-not-found]
from trade_krono_cli.data_providers import get_data_factory

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

_STOCK_NAMES: dict[str, str] = {
    "000001": "平安银行",
    "002027": "分众传媒",
    "601668": "中国建筑",
    "000932": "华菱钢铁",
    "601061": "中信金属",
}


def _get_cache_db() -> Path:
    """获取缓存数据库路径，优先从 settings 读取以支持测试隔离。"""
    # 注意：使用局部变量而非模块级全局，避免测试间污染
    try:
        settings = get_settings()
        return settings.cache_dir / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


def _get_whitelist() -> str:
    """从配置获取每日分析白名单（支持环境变量覆盖）。"""
    try:
        settings = get_settings()
        return settings.daily_analysis_whitelist.strip()
    except Exception:
        # 配置加载失败时回退到默认值
        return "000001,002027,601668,000932,601061"


_DYNAMIC_WHITELIST_PATH = Path("outputs/results/buffett_dynamic_whitelist.txt")


def _get_dynamic_whitelist() -> list[str]:
    """读取巴菲特动态白名单文件（逗号分隔的6位代码）。"""
    if not _DYNAMIC_WHITELIST_PATH.exists():
        return []
    try:
        content = _DYNAMIC_WHITELIST_PATH.read_text(encoding="utf-8").strip()
        if not content:
            return []
        return [c.strip() for c in content.split(",") if c.strip()]
    except Exception:
        return []


def _get_merged_whitelist() -> str:
    """合并静态白名单 + 动态巴菲特白名单（去重）。"""
    static = _get_whitelist()
    static_codes = [c.strip() for c in static.split(",") if c.strip()]
    dynamic = _get_dynamic_whitelist()
    seen: set[str] = set()
    merged: list[str] = []
    for code in static_codes + dynamic:
        code = code.strip()
        if code and code not in seen:
            seen.add(code)
            merged.append(code)
    return ",".join(merged)


# ─────────────────────────────────────────────────────────────────────────────
# AI 核实
# ─────────────────────────────────────────────────────────────────────────────

_AI_MODEL = os.getenv("AI_VERIFICATION_MODEL", "agnes-2.5-flash")

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc,assignment]


def _ai_available() -> bool:
    """检查 LLM 是否可用（必须在 _load_env() 之后调用）。"""
    if OpenAI is None:
        return False
    return bool(
        os.getenv("AGNES_API_KEY", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )


def _get_llm_client() -> Any | None:
    if OpenAI is None or not _ai_available():
        return None
    key = (
        os.getenv("AGNES_API_KEY", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )
    base_url = os.getenv("BACKEND_URL", "") or os.getenv("LLM_BASE_URL", "")
    if not base_url:
        logger.warning(
            "BACKEND_URL 未配置，LLM 将回退到 deepseek。"
            "建议在 .env 中设置 BACKEND_URL=https://apihub.agnes-ai.cn/v1"
        )
        base_url = "https://api.deepseek.com/v1"
    return OpenAI(api_key=key, base_url=base_url)


def _extract_json(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON。"""
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def ai_verify(stock_results: list[dict], date_str: str) -> dict[str, str]:
    """调用 LLM 对分析结果进行核实和摘要。"""
    if not _ai_available():
        logger.warning("AI 核实跳过：LLM 未配置")
        return {
            "analysis_summary": "LLM 未配置，跳过 AI 核实",
            "top_picks": "无",
            "risk_alerts": "无",
            "conclusion": "无",
        }

    stock_lines = []
    for r in stock_results:
        ticker = r.get("ticker", "?")
        name = r.get("name", "")
        trend = r.get("trend", "?")
        ma_signal = r.get("ma_signal", "?")
        vol_signal = r.get("vol_signal", "?")
        score = r.get("score", 0)
        stock_lines.append(
            f"• {ticker} {name}：趋势={trend}  "
            f"均线信号={ma_signal}  量能信号={vol_signal}  综合分={score:.1f}"
        )
    stock_text = "\n".join(stock_lines) or "（无数据）"

    prompt = (
        f"以下是 {date_str} 白名单股票的技术分析结果，请给出综合评估：\n\n"
        f"{stock_text}\n\n"
        "请按以下格式输出严格合法的 JSON（不要有任何额外文字）：\n"
        "{\n"
        '  "analysis_summary": "整体市场概况一句话总结",\n'
        '  "top_picks": "推荐Top 3股票及简短理由",\n'
        '  "risk_alerts": "需关注的主要风险，如无写无明显异常风险",\n'
        '  "conclusion": "简要评价1-2句话"\n'
        "}\n"
    )

    try:
        client = _get_llm_client()
        if client is None:
            return {
                "analysis_summary": "LLM 不可用",
                "top_picks": "无",
                "risk_alerts": "无",
                "conclusion": "无",
            }
        response = client.chat.completions.create(
            model=_AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=800,
        )
        raw = (response.choices[0].message.content or "").strip()
        result = _extract_json(raw)
        if result:
            return {
                "analysis_summary": result.get("analysis_summary", ""),
                "top_picks": result.get("top_picks", ""),
                "risk_alerts": result.get("risk_alerts", ""),
                "conclusion": result.get("conclusion", ""),
            }
    except Exception as e:
        logger.warning(f"AI 核实调用失败: {e}")
    return {
        "analysis_summary": "AI 核实失败",
        "top_picks": "无",
        "risk_alerts": "无",
        "conclusion": "无",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 技术分析（基于缓存 K 线）
# ─────────────────────────────────────────────────────────────────────────────


def get_kline(ticker: str, start: str | None = None, end: str | None = None) -> pd.DataFrame | None:
    """从缓存读取 K 线数据（支持日期范围过滤）。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）
    start : str | None
        起始日期 YYYY-MM-DD，为空时不限定下限
    end : str | None
        结束日期 YYYY-MM-DD，为空时不限定上限

    Returns
    -------
    pd.DataFrame | None
        K 线 DataFrame（含 timestamps/open/high/low/close/volume 列），无数据时返回 None
    """
    cache = get_cache()
    kline_cache = cache  # Cache 实例本身有 get_kline/set_kline 方法
    if start is not None and end is not None:
        return kline_cache.get_kline(ticker, start, end, freq="d", adjustflag="1")
    # 无日期限制：直接读原始行
    import sqlite3

    conn = sqlite3.connect(str(_get_cache_db()))
    row = conn.execute("SELECT data FROM kline_cache WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        from io import BytesIO

        df = pd.read_pickle(BytesIO(row[0]))
    except Exception as e:
        logger.warning(f"{ticker} 数据读取失败: {e}")
        return None
    return df


def _fetch_and_save_kline(ticker: str, end_date: str) -> pd.DataFrame | None:
    """拉取 K 线并写入本地缓存，失败时静默返回 None。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀）
    end_date : str
        查询结束日期 YYYY-MM-DD

    Returns
    -------
    pd.DataFrame | None
        拉取到的 K 线 DataFrame，失败时返回 None
    """
    try:
        factory = get_data_factory()
        # 拉取近一年历史数据（足够计算 MA60）
        start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365)).strftime(
            "%Y-%m-%d"
        )
        kline_data = factory.fetch_kline(
            ticker, start_date, end_date, frequency="d", adjustflag="1"
        )
        if kline_data is None or kline_data.is_empty:
            logger.warning(f"{ticker} 外部数据源亦无数据")
            return None
        df = kline_data.to_dataframe()
        if df is None or len(df) == 0:
            return None
        cache = get_cache()
        ts_col = "timestamps" if "timestamps" in df.columns else "date"
        df[ts_col] = pd.to_datetime(df[ts_col])
        df = df.sort_values(ts_col).reset_index(drop=True)
        cache.set_kline(
            ticker,
            df[ts_col].iloc[0].strftime("%Y-%m-%d"),
            df[ts_col].iloc[-1].strftime("%Y-%m-%d"),
            freq="d",
            df=df,
            ttl=0.0,
            adjustflag="1",
        )
        logger.info(f"{ticker} 已从外部源拉取并缓存 {len(df)} 条记录")
        return df
    except Exception as e:
        logger.warning(f"{ticker} 拉取失败: {e}")
        return None


def _get_stock_name(ticker: str) -> str:
    """获取股票名称，优先查本地映射，其次通过 baostock 动态查询。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）

    Returns
    -------
    str
        股票名称，无法获取时返回空字符串
    """
    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    name = _STOCK_NAMES.get(code, "")
    if name:
        return name
    try:
        from trade_krono_cli.data_providers import get_data_factory

        factory = get_data_factory()
        provider = factory.get_provider("baostock")
        if provider is None:
            return ""
        # _query_stock_basic 内部处理登录，字段顺序：code, code_name, ipoDate, outDate, ...
        rows = provider._query_stock_basic(ticker)  # type: ignore[attr-defined]
        if rows and len(rows[0]) > 1:
            fetched_name = rows[0][1].strip()
            if fetched_name:
                _STOCK_NAMES[code] = fetched_name
                return fetched_name
    except Exception as e:
        logger.debug(f"{ticker} 名称查询失败: {e}")
    return ""


def analyze_stock(ticker: str, end_date: str | None = None) -> dict[str, Any]:
    """对单只股票做基础技术分析。

    数据获取策略（三级回退）：
      1. 本地缓存（KlineCache 日期范围查询）
      2. 本地缓存（全量读取）
      3. 外部数据源拉取并写入缓存
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # 第 1 步：尝试从缓存读取（带日期范围）
    df = get_kline(ticker, start="2020-01-01", end=end_date)
    if df is not None and len(df) >= 20:
        return _run_analysis(ticker, df, end_date)

    # 第 2 步：尝试全量缓存读取（兼容旧格式）
    df = get_kline(ticker)
    if df is not None and len(df) >= 20:
        return _run_analysis(ticker, df, end_date)

    # 第 3 步：从外部数据源拉取
    logger.info(f"{ticker} 本地无缓存，尝试从外部源拉取...")
    df = _fetch_and_save_kline(ticker, end_date)
    if df is not None and len(df) >= 20:
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
        }

    ts_col = "timestamps" if "timestamps" in df.columns else "date"
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)

    close = df["close"].astype(float)
    latest = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else latest

    # ── 除权检测：当日跌幅超过 30% 视为除权日，以最后一个非除权交易日为准 ──
    daily_change = (latest - prev) / prev * 100 if prev > 0 else 0
    _is_exrights = bool(daily_change < -30)
    if _is_exrights:
        logger.info(
            f"📌 {ticker} 检测到除权日：当日跌幅 {daily_change:+.1f}%，"
            f"技术指标将基于前一日收盘价（{prev:.2f}）计算"
        )
    # 用于技术指标计算的有效收盘价（排除除权跳空当天）
    _effective_close = close.iloc[-2] if _is_exrights and len(close) > 2 else latest
    # 用于买卖信号计算的有效收盘价
    _sig_price = _effective_close
    daily_change_display = 0.0 if _is_exrights else daily_change

    # 均线（基于有效收盘价序列，避免除权日污染）
    _ma_src = close.iloc[:-1] if _is_exrights else close
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

    # 趋势判断（近5日涨跌，排除除权日）
    _trend_src = close.iloc[-6:-1] if _is_exrights else close.iloc[-5:]
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

    # 量能（近5日均量 vs 近20日均量，排除除权日）
    vol_col = "volume" if "volume" in df.columns else None
    vol_signal = "无"
    buy_points: list[str] = []
    sell_points: list[str] = []

    if vol_col:
        vol = df[vol_col].astype(float)
        _vol_src = vol.iloc[:-1] if _is_exrights else vol
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
        _close_sub = _sub["close"].astype(float)
        _high_30 = float(_sub[high_col].astype(float).max())
        # 若检测到除权日，剔除收盘异常低的行（除权后价格），只保留同一价格体系的数据
        if _is_exrights:
            _prev_close = close.iloc[-2]
            # 标记：收盘价远低于前一日（除权后低价），这些行需要排除
            _ex_mask = _close_sub < _prev_close * 0.5
            _clean = _sub.loc[~_ex_mask]
            if len(_clean) >= 5:
                _sub = _clean
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
    }


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def _build_stock_card(r: dict[str, Any], date_str: str) -> str:
    """构建单只股票的飞书消息文本。"""
    is_exrights = r.get("exrights", False)
    emoji = "🟢" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "🔴")
    action = "BUY" if r["score"] >= 65 else ("HOLD" if r["score"] >= 45 else "SELL")
    buy = "、".join(r.get("buy_points", [])) or "暂无"
    sell = "、".join(r.get("sell_points", [])) or "暂无"
    exrights_tag = " 📌除权日" if is_exrights else ""
    change_str = f"{r.get('change', 0):+.2f}%" if not is_exrights else "除权调整"
    return (
        f"**📊 {date_str} · {r['ticker']} {r['name']}{exrights_tag}**\n"
        f"{emoji} **综合分：{r['score']}**（{action}）\n"
        f"收盘价：{r.get('close', '?')}  今日涨跌：{change_str}\n"
        f"趋势：{r['trend']}　均线信号：{r['ma_signal']}　量能信号：{r['vol_signal']}\n"
        f"MA5={r.get('ma5', '?')}　MA10={r.get('ma10', '?')}　MA20={r.get('ma20', '?')}\n"
        f"20日区间：{r.get('low_20', '?')} ~ {r.get('high_20', '?')}\n"
        f"🟢 买点信号：{buy}\n"
        f"🔴 卖点信号：{sell}"
    )


def run_analysis(date: str, tickers_str: str) -> None:
    """执行白名单技术分析 + 逐只飞书推送 + AI核实 + 汇总推送。"""
    _load_env()

    # ── Step 1: 解析股票代码 ─────────────────────────────────────────────
    raw_codes = [c.strip() for c in tickers_str.split(",") if c.strip()]
    tickers_with_prefix = []
    for code in raw_codes:
        if "." in code:
            tickers_with_prefix.append(code)
        elif code.startswith("6"):
            tickers_with_prefix.append(f"sh.{code}")
        else:
            tickers_with_prefix.append(f"sz.{code}")

    logger.info(f"📊 白名单分析：{tickers_with_prefix}  日期={date}")

    # ── Step 2: 逐一技术分析 + 实时推送 ────────────────────────────────
    results: list[dict[str, Any]] = []
    config = load_config()
    for ticker in tickers_with_prefix:
        result = analyze_stock(ticker, end_date=date)
        results.append(result)
        status_emoji = "✅" if result["status"] == "ok" else "⚠️"
        logger.info(
            f"  {status_emoji} {ticker} {result['name']}: "
            f"分数={result['score']} 趋势={result['trend']} 均线={result['ma_signal']}"
        )
        # 立即推送单只股票结果
        if result["status"] == "ok":
            ok = send_notification(
                mode="text",
                config=config,
                content=_build_stock_card(result, date),
                title=f"{result['ticker']} {result['name']}",
            )
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {result['ticker']} {result['name']}")
        else:
            logger.warning(f"  ⚠️ {ticker} 无数据，跳过推送")

    # ── Step 3: AI 核实 ──────────────────────────────────────────────────
    logger.info("🤖 启动 AI 核实...")
    ai_result = ai_verify(results, date)

    # ── Step 4: 汇总推送 ────────────────────────────────────────────────
    ok_results = [r for r in results if r["status"] == "ok"]
    no_data_results = [r for r in results if r["status"] != "ok"]
    buy_signals = [r for r in ok_results if r["score"] >= 65]
    hold_signals = [r for r in ok_results if 45 <= r["score"] < 65]
    sell_signals = [r for r in ok_results if r["score"] < 45]

    top3_sorted = sorted(ok_results, key=lambda x: x["score"], reverse=True)[:3]
    top3_str = (
        "  ".join(
            f"{r['ticker']} {r['name']}:{'BUY' if r['score'] >= 65 else 'HOLD'} {r['score']}分"
            for r in top3_sorted
        )
        or "无"
    )

    summary_lines = [
        f"**📊 {date} 白名单分析汇总**\n",
        f"共 {len(ok_results)} 只有数据 · BUY {len(buy_signals)} 只 · HOLD {len(hold_signals)} 只 · SELL {len(sell_signals)} 只"
        f"{'，' + str(len(no_data_results)) + ' 只无数据' if no_data_results else ''}\n\n",
    ]
    for r in sorted(ok_results, key=lambda x: x["score"], reverse=True):
        emoji = "🔴" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "⚪")
        er_tag = " 📌除权日" if r.get("exrights", False) else ""
        change_str = f"{r['change']:+.2f}%" if not r.get("exrights") else "除权日"
        summary_lines.append(
            f"• {emoji} {r['ticker']} {r['name']}{er_tag}  分={r['score']}  趋势={r['trend']}  "
            f"均线={r['ma_signal']}  量能={r['vol_signal']}  今日={change_str}"
        )
    if no_data_results:
        summary_lines.append(f"\n⚠️ 无数据：{', '.join(r['ticker'] for r in no_data_results)}")

    summary_lines.append("\n")
    summary_lines.append(f"**🤖 AI 核实摘要：** {ai_result.get('analysis_summary', '')}\n")
    if ai_result.get("top_picks"):
        summary_lines.append(f"**🏆 Top 推荐：** {ai_result['top_picks']}\n")
    if ai_result.get("risk_alerts") and ai_result["risk_alerts"] != "无明显异常风险":
        summary_lines.append(f"**⚠️ 风险提示：** {ai_result['risk_alerts']}\n")
    summary_lines.append(f"**📝 结论：** {ai_result.get('conclusion', '')}")

    summary_content = "\n".join(summary_lines)

    ok = send_notification(
        mode="daily",
        config=config,
        status="success",
        date=date,
        tickers=tickers_str,
        top3=top3_str,
        run_url="",
        content=summary_content,
    )
    if ok:
        logger.info("✅ 汇总飞书推送成功")
    else:
        logger.error("❌ 汇总飞书推送失败")


def main() -> None:
    """命令行入口：解析参数并执行白名单分析 + AI 核实 + 飞书推送。"""
    import argparse

    parser = argparse.ArgumentParser(description="白名单股票综合分析 + AI核实 + 飞书推送")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument("--tickers", default=_get_merged_whitelist(), help="股票代码（逗号分隔，静态+动态白名单合并）")
    args = parser.parse_args()

    run_analysis(date=args.date, tickers_str=args.tickers)


if __name__ == "__main__":
    main()
