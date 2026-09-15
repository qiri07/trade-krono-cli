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
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts.feishu_core import load_config, send_notification
from trade_krono_cli.cli_commands._core_helpers import _load_env

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

_WHITELIST_DEFAULT = "000001,002027,601668,000932,601061"
_STOCK_NAMES: dict[str, str] = {
    "000001": "平安银行",
    "002027": "分众传媒",
    "601668": "中国建筑",
    "000932": "华菱钢铁",
    "601061": "中信金属",
}

_CACHE_DB = Path("outputs/cache/pipeline_cache.db")

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
    base_url = os.getenv("BACKEND_URL", "") or os.getenv(
        "LLM_BASE_URL", "https://api.deepseek.com/v1"
    )
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


def get_kline(ticker: str) -> pd.DataFrame | None:
    """从 SQLite 缓存读取 K 线数据。"""
    import sqlite3

    conn = sqlite3.connect(str(_CACHE_DB))
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


def analyze_stock(ticker: str) -> dict[str, Any]:
    """对单只股票做基础技术分析。"""
    df = get_kline(ticker)
    if df is None or len(df) < 20:
        return {
            "ticker": ticker,
            "name": _STOCK_NAMES.get(ticker.lstrip("sz.").lstrip("sh.").lstrip("bj."), ""),
            "status": "no_data",
            "score": 0.0,
            "trend": "数据不足",
            "ma_signal": "无",
            "vol_signal": "无",
        }

    ts_col = "timestamps" if "timestamps" in df.columns else "date"
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)

    close = df["close"].astype(float)
    latest = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else latest
    daily_change = (latest - prev) / prev * 100 if prev > 0 else 0

    # 均线（5日、10日、20日、60日）
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]

    # 均线信号
    if latest > ma5 > ma10 > ma20:
        ma_signal = "多头排列✅"
    elif latest < ma5 < ma10 < ma20:
        ma_signal = "空头排列❌"
    elif latest > ma5 and latest > ma10:
        ma_signal = "偏多"
    elif latest < ma5 and latest < ma10:
        ma_signal = "偏空"
    else:
        ma_signal = "震荡"

    # 趋势判断（近5日涨跌）
    recent_5 = close.iloc[-5:]
    up_days = sum(1 for i in range(1, len(recent_5)) if recent_5.iloc[i] > recent_5.iloc[i - 1])
    if up_days >= 4:
        trend = "强势上涨📈"
    elif up_days >= 3:
        trend = "震荡偏多"
    elif up_days <= 1:
        trend = "弱势下跌📉"
    else:
        trend = "横盘整理"

    # 量能（近5日均量 vs 近20日均量）
    vol_col = "volume" if "volume" in df.columns else None
    vol_signal = "无"
    if vol_col:
        vol = df[vol_col].astype(float)
        avg_vol_5 = vol.iloc[-5:].mean()
        avg_vol_20 = vol.iloc[-20:].mean()
        if avg_vol_20 > 0:
            vol_ratio = avg_vol_5 / avg_vol_20
            if vol_ratio > 1.5:
                vol_signal = "放量🔥"
            elif vol_ratio < 0.6:
                vol_signal = "缩量📉"
            else:
                vol_signal = "正常"

    # 综合评分（0-100）
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
        "name": _STOCK_NAMES.get(ticker.lstrip("sz.").lstrip("sh.").lstrip("bj."), ""),
        "status": "ok",
        "score": round(score, 1),
        "close": round(latest, 2),
        "change": round(daily_change, 2),
        "trend": trend,
        "ma_signal": ma_signal,
        "vol_signal": vol_signal,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def run_analysis(date: str, tickers_str: str) -> None:
    """执行白名单技术分析 + AI核实 + 飞书推送。"""
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

    # ── Step 2: 逐一技术分析 ────────────────────────────────────────────
    results = []
    for ticker in tickers_with_prefix:
        result = analyze_stock(ticker)
        results.append(result)
        status_emoji = "✅" if result["status"] == "ok" else "⚠️"
        logger.info(
            f"  {status_emoji} {ticker} {result['name']}: "
            f"分数={result['score']} 趋势={result['trend']} 均线={result['ma_signal']}"
        )

    # ── Step 3: AI 核实 ──────────────────────────────────────────────────
    logger.info("🤖 启动 AI 核实...")
    ai_result = ai_verify(results, date)

    # ── Step 4: 构建飞书内容 ─────────────────────────────────────────────
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

    content_lines = [
        f"**📊 {date} 白名单技术分析**\n",
        f"**统计：** {len(ok_results)} 只有数据，BUY {len(buy_signals)} 只 · HOLD {len(hold_signals)} 只 · SELL {len(sell_signals)} 只"
        f"{'，' + str(len(no_data_results)) + ' 只无数据' if no_data_results else ''}\n\n",
    ]
    for r in sorted(ok_results, key=lambda x: x["score"], reverse=True):
        emoji = "🔴" if r["score"] >= 65 else ("🟡" if r["score"] >= 45 else "⚪")
        content_lines.append(
            f"• {emoji} **{r['ticker']} {r['name']}**  综合分={r['score']}  "
            f"趋势={r['trend']}  均线={r['ma_signal']}  "
            f"量能={r['vol_signal']}  今日={r['change']:+.2f}%"
        )
    if no_data_results:
        content_lines.append(f"\n⚠️ 无数据：{', '.join(r['ticker'] for r in no_data_results)}")

    content_lines.append("\n")
    content_lines.append(f"**🤖 AI 核实摘要：** {ai_result.get('analysis_summary', '')}\n")
    if ai_result.get("top_picks"):
        content_lines.append(f"**🏆 Top 推荐：** {ai_result['top_picks']}\n")
    if ai_result.get("risk_alerts") and ai_result["risk_alerts"] != "无明显异常风险":
        content_lines.append(f"**⚠️ 风险提示：** {ai_result['risk_alerts']}\n")
    content_lines.append(f"**📝 结论：** {ai_result.get('conclusion', '')}")

    content = "\n".join(content_lines)

    # ── Step 5: 推送飞书 ─────────────────────────────────────────────────
    config = load_config()
    ok = send_notification(
        mode="daily",
        config=config,
        status="success",
        date=date,
        tickers=tickers_str,
        top3=top3_str,
        run_url="",
        content=content,
    )
    if ok:
        logger.info("✅ 飞书推送成功")
    else:
        logger.error("❌ 飞书推送失败")


def main() -> None:
    """命令行入口：解析参数并执行白名单分析 + AI 核实 + 飞书推送。"""
    import argparse

    parser = argparse.ArgumentParser(description="白名单股票综合分析 + AI核实 + 飞书推送")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument("--tickers", default=_WHITELIST_DEFAULT, help="股票代码（逗号分隔）")
    args = parser.parse_args()

    run_analysis(date=args.date, tickers_str=args.tickers)


if __name__ == "__main__":
    main()
