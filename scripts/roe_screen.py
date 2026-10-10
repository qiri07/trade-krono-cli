#!/usr/bin/env python3
"""白名单股票 ROE > 15% 筛选 + AI 核实 + 飞书推送。

用法：
  uv run python scripts/roe_screen.py
  uv run python scripts/roe_screen.py --date 2026-10-09
  uv run python scripts/roe_screen.py --top 20
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import check_data_freshness, ticker_to_thscode
from scripts.daily_analysis import ai_verify
from scripts.feishu_core import load_config, send_notification
from trade_krono_cli.cli_commands.core import _load_env

# ── 常量 ───────────────────────────────────────────────────────────────────────
_RESULTS_DIR = Path("outputs/results")
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

_FUYAO_BASE = "https://fuyao.aicubes.cn"
_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
    or os.getenv("FUYAO_API_KEY", "").strip()
)

_ROE_THRESHOLD = 15.0  # ROE > 此值才入选
_DEFAULT_WHITELIST_FILE = Path("outputs/cache/whitelist.json")

# ── API 调用 ───────────────────────────────────────────────────────────────────


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """用 curl 发 GET 请求，返回 JSON 或 None。"""
    if not _API_KEY:
        logger.warning("HITHINK_FINANCE_API_KEY 未配置，跳过 ROE 筛选")
        return None
    url = f"{_FUYAO_BASE}{path}"
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}"
    cmd = [
        "curl",
        "-s",
        "--max-time",
        "10",
        "-w",
        "\n%{http_code}",
        full_url,
        "-H",
        f"X-api-key: {_API_KEY}",
        "-A",
        "ROEScreen/1.0",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        stdout = r.stdout.strip()
        nl = stdout.rfind("\n")
        if nl < 0:
            return None
        body, http_code = stdout[:nl], int(stdout[nl + 1 :])
        if http_code != 200:
            return None
        return json.loads(body)
    except Exception as e:
        logger.debug(f"API 请求异常: {str(e)[:60]}")
        return None


def _fetch_roe(thscode: str, year: str) -> dict[str, Any] | None:
    """获取单只股票的 ROE 指标。

    Returns
    -------
    dict | None
        {"roe": float, "name": str, "pe": float} 或 None
    """
    data = _api_get(
        "/api/a-share/financials/indicators",
        {"thscode": thscode, "report": f"{year}-4"},
    )
    if not data or data.get("code") != 0:
        return None
    d = data.get("data", {})
    name = d.get("name", "")
    roe: float | None = None
    for ab in d.get("abilities", []):
        for ind in ab.get("indicators", []):
            if ind.get("index_id") == "index_weighted_avg_roe":
                try:
                    roe = float(ind.get("value"))
                except (TypeError, ValueError):
                    pass
                break
        if roe is not None:
            break
    if roe is None:
        return None
    return {
        "roe": roe,
        "name": name,
    }


def _batch_fetch_roe(tickers: list[str], year: str) -> dict[str, dict[str, Any]]:
    """批量获取 ROE，带 50ms 间隔避免限流。"""
    results: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        thscode = ticker_to_thscode(ticker)
        data = _fetch_roe(thscode, year)
        if data:
            results[ticker] = data
        time.sleep(0.05)
    return results


def _fetch_stock_names(tickers: list[str]) -> dict[str, str]:
    """从 Baostock 批量获取股票中文名称。

    Returns
    -------
    dict[ticker, str]
        ticker → 中文名称映射，获取失败的股票名称为空字符串。
    """
    try:
        import baostock as bs  # type: ignore
    except ImportError:
        logger.warning("baostock 未安装，跳过股票名称获取")
        return {t: "" for t in tickers}

    names: dict[str, str] = {}
    lg = bs.login()
    if lg.error_code != "0":
        logger.warning(f"baostock 登录失败: {lg.error_msg}，跳过股票名称获取")
        return {t: "" for t in tickers}

    try:
        for ticker in tickers:
            rs = bs.query_stock_basic(code=ticker)
            if rs.error_code != "0":
                names[ticker] = ""
                continue
            while rs.next():
                row = rs.get_row_data()
                # row: [code, code_name, ipoDate, outDate, stockType, area]
                names[ticker] = row[1] if len(row) > 1 else ""
    finally:
        bs.logout()  # type: ignore

    return names


def _load_whitelist() -> list[str]:
    """从 .env 加载白名单 ticker 列表（带 sh./sz. 前缀）。"""
    ws_env = os.getenv("SYNC_WHITELIST", "").strip()
    if not ws_env:
        logger.error("SYNC_WHITELIST 未配置")
        sys.exit(1)
    codes = [c.strip() for c in ws_env.split(",") if c.strip()]
    # 补全交易所前缀
    prefixed: list[str] = []
    for code in codes:
        if code.startswith("sh.") or code.startswith("sz.") or code.startswith("hk."):
            prefixed.append(code)
        elif code.startswith("bj."):
            prefixed.append(code)
        else:
            # 默认按代码前缀判断
            if code.startswith("6"):
                prefixed.append(f"sh.{code}")
            elif code.startswith("0") or code.startswith("3"):
                prefixed.append(f"sz.{code}")
            else:
                prefixed.append(code)
    return prefixed


def _build_summary_card(
    results: list[dict[str, Any]], date: str, ai_result: dict[str, str]
) -> str:
    """构建飞书摘要卡片。"""
    lines = [
        f"📊 **ROE 筛选 {date}**",
        f"ROE > {_ROE_THRESHOLD}% 白名单股票：**{len(results)} 只**",
        "",
    ]
    if results:
        sorted_r = sorted(results, key=lambda x: x["roe"], reverse=True)
        lines.append("### 🏆 筛选结果（按 ROE 排序）")
        lines.append(
            "| 排名 | 代码 | 名称 | ROE% | PE | 收盘价 |"
        )
        lines.append("|:----:|------|------|-----:|-----:|-------:|")
        for i, r in enumerate(sorted_r, 1):
            pe = r.get("pe")
            pe_str = f"{pe:.1f}" if pe is not None else "-"
            lines.append(
                f"| {i} | {r['ticker']} | {r.get('name', '?')} "
                f"| {r['roe']:.1f}% | {pe_str} | - |"
            )
        lines.append("")

    if ai_result and ai_result.get("analysis_summary", "").strip():
        lines.append("### 🤖 AI 核实")
        lines.append(ai_result["analysis_summary"])
        lines.append("")

    lines.append("---")
    lines.append(f"筛选条件：白名单股票 + ROE > {_ROE_THRESHOLD}%（年报）")
    lines.append(f"数据日期：{date}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="白名单 ROE 筛选")
    parser.add_argument("--date", default=None, help="分析日期（默认今天）")
    parser.add_argument("--year", default=None, help="财报年份（默认最近可用年份）")
    parser.add_argument("--top", type=int, default=0, help="只返回前 N 只（0=全部）")
    args = parser.parse_args()

    _load_env()
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    report_year = args.year or datetime.now().year

    logger.info(f"📊 ROE 筛选，日期={date_str}，财报年={report_year}")

    # 数据检查
    logger.info(f"[数据检查] {check_data_freshness(logger.info)}")

    # 加载白名单
    whitelist = _load_whitelist()
    # 排除港股（无 ROE 接口）
    a_stocks = [t for t in whitelist if not t.startswith("hk.")]
    hk_stocks = [t for t in whitelist if t.startswith("hk.")]
    logger.info(f"📋 白名单共 {len(whitelist)} 只（A股 {len(a_stocks)}，港股 {len(hk_stocks)}）")

    if not a_stocks:
        logger.warning("⚠️ 无 A 股白名单，退出")
        return

    # 获取 ROE（优先当年年报，回退到最近可用年份）
    logger.info(f"📈 批量获取 ROE（{report_year} 年报）...")
    roe_map = _batch_fetch_roe(a_stocks, str(report_year))
    if len(roe_map) == 0 and report_year > 2020:
        fallback_year = str(report_year - 1)
        logger.info(f"⚠️ {report_year} 年数据不可用，回退到 {fallback_year} 年...")
        roe_map = _batch_fetch_roe(a_stocks, fallback_year)
    logger.info(f"✅ ROE 获取成功：{len(roe_map)}/{len(a_stocks)} 只")

    # 获取股票名称（通过 Baostock）
    logger.info("📋 批量获取股票名称...")
    name_map = _fetch_stock_names(a_stocks)
    logger.info(f"✅ 名称获取成功：{sum(1 for v in name_map.values() if v)}/{len(name_map)} 只")

    # 筛选 ROE > threshold
    results: list[dict[str, Any]] = []
    no_roe = 0
    low_roe = 0
    for ticker in a_stocks:
        if ticker not in roe_map:
            no_roe += 1
            continue
        roe = roe_map[ticker]["roe"]
        if roe <= _ROE_THRESHOLD:
            low_roe += 1
            continue
        results.append(
            {
                "ticker": ticker,
                "name": name_map.get(ticker, "") or roe_map[ticker].get("name", ""),
                "roe": round(roe, 2),
                "pe": roe_map[ticker].get("pe"),
            }
        )

    logger.info(
        f"📊 筛选结果：白名单 {len(a_stocks)} 只 → "
        f"ROE > {_ROE_THRESHOLD}% **{len(results)} 只** "
        f"（无数据 {no_roe} 只、ROE≤{_ROE_THRESHOLD}% {low_roe} 只）"
    )

    if not results:
        logger.warning("⚠️ 无股票满足 ROE 条件")
        return

    # 排序
    results.sort(key=lambda x: x["roe"], reverse=True)
    if args.top > 0:
        results = results[: args.top]

    # 保存结果
    result_file = _RESULTS_DIR / f"roe_screen_{date_str}.json"
    result_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"💾 结果已保存: {result_file}")

    # AI 核实
    ai_result: dict[str, str] = {}
    logger.info("🤖 启动 AI 核实...")
    try:
        ai_result = ai_verify(results[:20], date_str)
        ai_file = _RESULTS_DIR / f"roe_screen_{date_str}_ai.json"
        ai_file.write_text(json.dumps(ai_result, ensure_ascii=False, indent=2), encoding="utf-8")
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

        # 逐只推送
        for r in results:
            card = (
                f"📊 **{date_str} · {r['ticker']}**\n"
                f"🟢 **ROE：{r['roe']:.1f}%**\n"
                f"名称：{r.get('name', '?')}　PE：{r.get('pe', '-')}\n"
            )
            ok = send_notification(mode="text", config=config, content=card, title=r["ticker"])
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {r['ticker']}")

        # 汇总推送
        ok = send_notification(
            mode="text",
            config=config,
            content=summary,
            title=f"📊 ROE 筛选结果 {date_str}",
        )
        logger.info(f"{'✅' if ok else '❌'} 汇总飞书推送完成")
    else:
        logger.warning("⚠️ 无结果可推送")


if __name__ == "__main__":
    main()
