#!/usr/bin/env python3
"""全市场三周期 MACD 共振筛选 + 分析 + 飞书推送。

筛选逻辑：
  - 日线 MACD > 0（短期动能向上）
  - 周线 MACD > 0（中期趋势向上）
  - 月线 MACD > 0（长期趋势向上）
  三线共振 = 股性强、趋势明确的标的

用法：
  uv run python scripts/multi_line_resonance.py
  uv run python scripts/multi_line_resonance.py --date 2026-10-09
  uv run python scripts/multi_line_resonance.py --top 30
  uv run python scripts/multi_line_resonance.py --workers 10
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import check_data_freshness, get_cache_db_path, ticker_to_thscode
from scripts.daily_analysis import ai_verify
from scripts.feishu_core import load_config, send_notification

# ── 路径常量 ─────────────────────────────────────────────────────────────────

_RESULTS_DIR = Path("outputs/results")
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 同花顺 Fuyao API（估值快照）─────────────────────────────────────────────
_FUYAO_BASE = "https://fuyao.aicubes.cn"
_FUYAO_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)

_ST_KEYWORDS = ("ST", "*ST", "退市", "N", "C")


def _get_all_tickers(db_path: Path) -> list[str]:
    """从缓存数据库获取所有股票代码（排除北交所）。"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    all_tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    # 排除北交所（bj.开头）
    astock_tickers = [t for t in all_tickers if not t.startswith("bj.")]
    logger.info(f"📊 全市场扫描：共 {len(all_tickers)} 只，排除北交所后 {len(astock_tickers)} 只")
    return astock_tickers


def _is_st(name: str) -> bool:
    """判断股票名称是否含 ST 标识。"""
    return any(kw in name for kw in _ST_KEYWORDS)


def _fetch_hk_pe_snapshot(tickers: list[str], batch_size: int = 50) -> dict[str, dict[str, Any]]:
    """批量获取港股的 PE_TTM 和名称（通过腾讯行情接口）。

    Parameters
    ----------
    tickers : list[str]
        hk. 前缀的股票代码列表，如 ["hk.00700", "hk.09988"]
    batch_size : int
        每批数量，默认 50

    Returns
    -------
    dict[str, dict]
        {6位代码: {"pe": pe_ttm, "name": stock_name}}
    """
    result: dict[str, dict[str, Any]] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        codes = ",".join(t.replace("hk.", "") for t in batch)
        url = f"https://qt.gtimg.cn/q=hk{codes}"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            for line in r.text.strip().split(";"):
                line = line.strip()
                if not line:
                    continue
                try:
                    prefix, data = line.split("=", 1)
                    raw_code = prefix.replace("v_", "").lower()
                    parts = data.strip('"').split("~")
                    if len(parts) < 40:
                        continue
                    name = parts[1].strip() if len(parts) > 1 else ""
                    pe_raw = parts[39]
                    if not pe_raw or pe_raw == "--":
                        continue
                    try:
                        pe_f = float(pe_raw)
                    except (TypeError, ValueError):
                        continue
                    result[raw_code] = {"pe": pe_f, "name": name}
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"港股 PE 获取失败（批次 {i // batch_size + 1}）: {str(e)[:80]}")
    return result


def _fetch_pe_snapshot(tickers: list[str], batch_size: int = 50) -> dict[str, dict]:
    """批量获取 A 股的 PE_TTM 和名称（通过同花顺 Fuyao API）。

    Parameters
    ----------
    tickers : list[str]
        sh./sz. 前缀的股票代码列表
    batch_size : int
        每批数量，默认 50

    Returns
    -------
    dict[str, dict]
        {6位代码: {"pe": pe_ttm, "name": stock_name}}，
        获取失败或 PE 缺失的 code 不出现在结果中
    """
    if not _FUYAO_API_KEY:
        logger.warning("HITHINK_FINANCE_API_KEY 未配置，跳过 PE 过滤")
        return {}

    result: dict[str, dict] = {}
    for i in range(0, len(tickers), batch_size):
        batch_tickers = tickers[i : i + batch_size]
        ths_param = ",".join(ticker_to_thscode(t) for t in batch_tickers)
        url = f"{_FUYAO_BASE}/api/a-share/valuations/snapshot?thscodes={ths_param}"
        cmd = [
            "curl",
            "-s",
            "--max-time",
            "10",
            "-w",
            "\n%{http_code}",
            url,
            "-H",
            f"X-api-key: {_FUYAO_API_KEY}",
            "-A",
            "MultiLineResonance/1.0",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            stdout = r.stdout.strip()
            nl = stdout.rfind("\n")
            if nl < 0:
                continue
            body, http_code = stdout[:nl], int(stdout[nl + 1 :])
            if http_code != 200:
                continue
            data = json.loads(body)
            if data.get("code") != 0:
                continue
            for item in data.get("data", {}).get("item", []):
                thscode = item.get("thscode", "")
                code_key = thscode.split(".")[0] if "." in thscode else thscode
                pe_raw = item.get("pe_ttm")
                name = item.get("name", "").strip()
                if pe_raw is None:
                    continue
                try:
                    pe_f = float(pe_raw)
                    if pe_f > 0:
                        result[code_key] = {"pe": pe_f, "name": name}
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug(f"PE 获取失败（批次 {i // batch_size + 1}）: {str(e)[:80]}")
    return result


def _macd(values: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """计算给定价格序列的最新 MACD 值（柱状图 = DIF - DEA，返回 DIF 值）。

    Parameters
    ----------
    values : pd.Series
        价格序列（收盘/重采样后收盘价）
    fast, slow, signal : int
        MACD 参数，默认 (12, 26, 9)

    Returns
    -------
    float
        最新 DIF 值（DIF = EMA(fast) - EMA(slow)，MACD 柱 = 2*(DIF - EMA(DIF, signal))）
    """
    if len(values) < slow + 1:
        return float("nan")
    ema_fast = values.ewm(span=fast, adjust=False).mean()
    ema_slow = values.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    return float(dif.iloc[-1])


def _resample_close(df: pd.DataFrame, freq: str) -> pd.Series:
    """按指定频率重采样收盘价（使用每周/每月最后一个交易日的收盘价）。"""
    df = df.copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.set_index("timestamps").sort_index()
    close = df["close"].resample(freq).last().dropna()
    # 确保返回普通 Series（非 period 索引）
    return close.reset_index(drop=True)


def _compute_macd_for_ticker(ticker: str, db_path: Path) -> dict[str, Any] | None:
    """计算单只股票的日/周/月 MACD，返回共振结果或 None。

    Returns
    -------
    dict or None
        {ticker, d_macd, w_macd, m_macd, d_ok, w_ok, m_ok, ...}
        若三线均共振则 d_ok/w_ok/m_ok 均为 True
    """
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker=? AND freq='d' AND adjustflag='1'",
        (ticker,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    try:
        df = pd.read_pickle(io.BytesIO(row[0]))
    except Exception:
        return None

    if not isinstance(df, pd.DataFrame):
        return None
    if df.empty or len(df) < 50:
        return None

    # timestamps 列已经是 datetime64，直接设置索引用于 resample
    df = df.copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.set_index("timestamps").sort_index()
    close = df["close"].astype(float)

    # 数据质量检查：收盘价应在合理范围内（排除极端异常值）
    close_clean = close[(close > 0) & (close < close.quantile(0.99) * 1.5)]
    if len(close_clean) < 50:
        return None

    # ── 日线 MACD ──
    d_macd = _macd(close_clean)
    if pd.isna(d_macd):
        return None

    # ── 周线 MACD ──
    try:
        w_close = close.resample("W").last().dropna()
        w_macd = _macd(w_close)
    except Exception:
        w_macd = float("nan")

    # ── 月线 MACD ──
    try:
        m_close = close.resample("ME").last().dropna()
        m_macd = _macd(m_close)
    except Exception:
        m_macd = float("nan")

    d_ok = not pd.isna(d_macd) and d_macd > 0
    w_ok = not pd.isna(w_macd) and w_macd > 0
    m_ok = not pd.isna(m_macd) and m_macd > 0

    if not (d_ok and w_ok and m_ok):
        return None

    # ── 附加信息（使用清洗后的数据）──
    latest_close = float(close_clean.iloc[-1])
    prev_close = float(close_clean.iloc[-2]) if len(close_clean) > 1 else latest_close
    change_pct = (latest_close - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

    ma5 = float(close_clean.iloc[-5:].mean()) if len(close_clean) >= 5 else latest_close
    ma10 = float(close_clean.iloc[-10:].mean()) if len(close_clean) >= 10 else latest_close
    ma20 = float(close_clean.iloc[-20:].mean()) if len(close_clean) >= 20 else latest_close

    # 均线信号
    if latest_close > ma5 > ma10 > ma20:
        ma_signal = "多头排列✅"
    elif latest_close < ma5 < ma10 < ma20:
        ma_signal = "空头排列❌"
    elif latest_close > ma5 and latest_close > ma10:
        ma_signal = "偏多"
    elif latest_close < ma5 and latest_close < ma10:
        ma_signal = "偏空"
    else:
        ma_signal = "震荡"

    # 综合评分（MACD 强度 + 均线 + 涨跌幅）
    score = 0.0
    # MACD 归一化评分（假设 MACD 在 -5 到 +10 之间，取对数缩放）
    macd_score = min(max(d_macd * 10, 0), 100)
    score += macd_score * 0.4
    if ma_signal == "多头排列✅":
        score += 25
    elif ma_signal == "偏多":
        score += 15
    # 涨跌幅评分
    change_score = min(max(change_pct * 5 + 50, 0), 100)
    score += change_score * 0.2
    # 周线/月线 MACD 强度
    w_score = min(max(w_macd * 10, 0), 100) if not pd.isna(w_macd) else 50
    m_score = min(max(m_macd * 10, 0), 100) if not pd.isna(m_macd) else 50
    score += (w_score + m_score) * 0.1

    return {
        "ticker": ticker,
        "d_macd": round(d_macd, 4),
        "w_macd": round(w_macd, 4) if not pd.isna(w_macd) else None,
        "m_macd": round(m_macd, 4) if not pd.isna(m_macd) else None,
        "close": round(latest_close, 2),
        "change_pct": round(change_pct, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma_signal": ma_signal,
        "score": round(score, 1),
        "trend": "强势上涨" if change_pct > 3 else ("震荡偏多" if change_pct > 0 else "弱势"),
    }


def _build_summary_card(results: list[dict[str, Any]], date: str, ai_result: dict[str, str]) -> str:
    """构建飞书推送摘要卡片。"""
    total = len(results)
    lines = [f"📊 **三周期 MACD 共振筛选 {date}**", f"共振股票：**{total} 只**", ""]

    # 按评分排序
    sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)

    # Top 10
    if sorted_results:
        lines.append("### 🏆 TOP 10 推荐")
        lines.append("| 排名 | 代码 | 名称 | 收盘 | 涨跌% | 日MACD | 周MACD | 月MACD | PE | 评分 |")
        lines.append(
            "|:----:|------|------|-----:|------:|-------:|-------:|-------:|-----:|-----:|"
        )
        for i, r in enumerate(sorted_results[:10], 1):
            name = r.get("name", "?")
            pe = r.get("pe", None)
            pe_str = f"{pe:.1f}" if pe is not None else "-"
            lines.append(
                f"| {i} | {r['ticker']} | {name} "
                f"| {r['close']} | {r['change_pct']:+.2f}% "
                f"| {r['d_macd']:+.4f} | {r['w_macd'] if r.get('w_macd') is not None else '-'} "
                f"| {r['m_macd'] if r.get('m_macd') is not None else '-'} "
                f"| {pe_str} | {r['score']} |"
            )
        lines.append("")

    # 全量列表
    if len(sorted_results) > 10:
        lines.append("### 📋 全部共振股票")
        for r in sorted_results[10:]:
            name = r.get("name", "?")
            lines.append(
                f"- {r['ticker']} {name}: 收盘={r['close']}, "
                f"日MACD={r['d_macd']:+.4f}, 周MACD={r['w_macd'] if r.get('w_macd') is not None else 'N/A'}, "
                f"月MACD={r['m_macd'] if r.get('m_macd') is not None else 'N/A'}, "
                f"评分={r['score']}"
            )
        lines.append("")

    # AI 核实
    if ai_result and ai_result.get("analysis_summary", "").strip():
        lines.append("### 🤖 AI 核实")
        lines.append(ai_result["analysis_summary"])
        lines.append("")

    lines.append("---")
    lines.append("筛选条件：日线 MACD>0 且 周线 MACD>0 且 月线 MACD>0 且 PE>0 且非ST 且 评分≥50")
    lines.append(f"数据日期：{date}")
    return "\n".join(lines)


def _run_scan(
    tickers: list[str],
    db_path: Path,
    workers: int = 20,
    progress_interval: int = 200,
) -> list[dict[str, Any]]:
    """并发扫描所有股票，筛选三线共振标的。"""
    results: list[dict[str, Any]] = []
    total = len(tickers)
    start_time = time.time()

    def _worker(ticker: str) -> dict[str, Any] | None:
        try:
            return _compute_macd_for_ticker(ticker, db_path)
        except Exception as e:
            logger.debug(f"  {ticker} 处理异常: {str(e)[:80]}")
            return None

    logger.info(f"步骤 1/3：扫描 {total} 只股票...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tickers}
        completed = 0
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                results.append(r)
            completed += 1
            if completed % progress_interval == 0 or completed == total:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / rate / 60 if rate > 0 else 0
                logger.info(
                    f"  进度: {completed}/{total} ({completed * 100 // total}%), 已找到 {len(results)} 只共振, 预计剩余 {eta:.1f} 分钟"
                )

    logger.info(
        f"步骤 1/3：扫描完成（{time.time() - start_time:.1f}s），共振股票 {len(results)} 只"
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="全市场三周期 MACD 共振筛选")
    parser.add_argument("--date", default=None, help="分析日期（默认今天）")
    parser.add_argument("--top", type=int, default=0, help="只返回前N只（0=全部）")
    parser.add_argument("--workers", type=int, default=20, help="并发数（默认20）")
    args = parser.parse_args()

    _load_env = None
    try:
        from scripts.daily_analysis import _load_env as _le

        _load_env = _le
    except Exception:
        pass
    if _load_env:
        _load_env()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"📊 三周期 MACD 共振筛选，日期={date_str}，并发={args.workers}")

    # 数据检查
    logger.info(f"[数据检查] {check_data_freshness(logger.info)}")

    # 获取股票列表
    db_path = get_cache_db_path()
    tickers = _get_all_tickers(db_path)
    if not tickers:
        logger.error("❌ 未找到任何股票，退出")
        sys.exit(1)

    # 扫描
    results = _run_scan(tickers, db_path, workers=args.workers)

    # 获取 PE 快照（用于名称填充和筛选）
    pe_map: dict[str, dict[str, Any]] = {}
    a_stocks = [r["ticker"] for r in results if not r["ticker"].startswith("hk.")]
    hk_stocks = [r["ticker"] for r in results if r["ticker"].startswith("hk.")]
    try:
        pe_map = _fetch_pe_snapshot(a_stocks)
    except Exception as e:
        logger.debug(f"PE 获取失败: {str(e)[:80]}")
    if hk_stocks:
        try:
            hk_pe_map = _fetch_hk_pe_snapshot(hk_stocks)
            pe_map.update(hk_pe_map)
            logger.info(f"📊 港股 PE 获取：{len(hk_pe_map)}/{len(hk_stocks)} 只")
        except Exception as e:
            logger.debug(f"港股 PE 获取失败: {str(e)[:80]}")
    for r in results:
        code = _strip_prefix(r["ticker"])
        entry = pe_map.get(code)
        if entry:
            r["name"] = entry.get("name", "")
            r["pe"] = entry.get("pe")

    # ── 过滤：ST 股、PE≤0、评分<50 ──
    removed_st = 0
    removed_pe = 0
    removed_score = 0
    filtered: list[dict[str, Any]] = []
    for r in results:
        name = r.get("name", "")
        if _is_st(name):
            removed_st += 1
            continue
        pe = r.get("pe")
        if pe is None or pe <= 0:
            removed_pe += 1
            continue
        if r["score"] < 50:
            removed_score += 1
            continue
        filtered.append(r)

    logger.info(
        f"📊 过滤完成：共振 {len(results)} 只 → "
        f"剔除 ST {removed_st} 只、PE≤0/缺失 {removed_pe} 只、评分<50 {removed_score} 只 → "
        f"有效 {len(filtered)} 只"
    )
    results = filtered

    # 按评分排序
    results.sort(key=lambda x: x["score"], reverse=True)
    if args.top > 0:
        results = results[: args.top]

    # 保存原始结果
    result_file = _RESULTS_DIR / f"macd_resonance_{date_str}.json"
    result_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"💾 结果已保存: {result_file}")

    # AI 核实
    ai_result: dict[str, str] = {}
    if results:
        logger.info("🤖 启动 AI 核实...")
        try:
            ai_result = ai_verify(results[:30], date_str)
            ai_file = _RESULTS_DIR / f"macd_resonance_{date_str}_ai.json"
            ai_file.write_text(
                json.dumps(ai_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary_text = ai_result.get("analysis_summary", "")
            if not summary_text or summary_text.startswith("LLM"):
                ai_result = {}
                logger.info("AI 核实跳过：LLM 未配置")
            else:
                logger.info(f"AI 核实完成：{summary_text[:80]}")
        except Exception as e:
            logger.warning(f"AI 核实失败: {str(e)[:100]}")
            ai_result = {}

    # 飞书推送
    if results:
        summary = _build_summary_card(results, date_str, ai_result)
        config = load_config()

        # 逐只推送详情（最多 20 只）
        for r in results[:20]:
            card = (
                f"📊 **{date_str} · {r['ticker']}**\n"
                f"🟢 **综合分：{r['score']}**\n"
                f"收盘价：{r['close']}  今日涨跌：{r['change_pct']:+.2f}%\n"
                f"日MACD：{r['d_macd']:+.4f}　周MACD：{r.get('w_macd', 'N/A')}　月MACD：{r.get('m_macd', 'N/A')}\n"
                f"MA5={r['ma5']}　MA10={r['ma10']}　MA20={r['ma20']}　{r['ma_signal']}\n"
            )
            ok = send_notification(mode="text", config=config, content=card, title=r["ticker"])
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {r['ticker']}")

        # 汇总推送
        ok = send_notification(
            mode="text",
            config=config,
            content=summary,
            title=f"📊 三周期 MACD 共振筛选 {date_str}",
        )
        logger.info(f"{'✅' if ok else '❌'} 汇总飞书推送完成")
    else:
        logger.warning("⚠️ 未找到三线共振股票")


def _strip_prefix(ticker: str) -> str:
    """去除交易所前缀，返回 6 位纯数字代码。"""
    return ticker.split(".", 1)[1] if "." in ticker else ticker


if __name__ == "__main__":
    main()
