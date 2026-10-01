#!/usr/bin/env python3
"""全市场扫描多头排列股票 + 分析 + 飞书推送。

用法：
  uv run python scripts/multi_scan.py
  uv run python scripts/multi_scan.py --top 50
  uv run python scripts/multi_scan.py --date 2026-09-24
"""

from __future__ import annotations

import argparse
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import check_data_freshness
from scripts.daily_analysis import _load_env, ai_verify, analyze_stock
from scripts.feishu_core import load_config, send_notification
from scripts.valuation import _safe_float, evaluate_valuation
from trade_krono_cli.config import get_settings


def _get_results_dir() -> Path:
    """获取结果目录（优先从配置，回退到项目默认路径）。"""
    try:
        s = get_settings()
        return Path(s.results_dir)
    except Exception:
        pass
    # 回退：项目 outputs/results/
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "outputs" / "results"


# ── 路径常量 ────────────────────────────────────────────────────────────────

_RESULTS_DIR = _get_results_dir()
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 同花顺 Fuyao API（估值快照）─────────────────────────────────────────────
_FUYAO_BASE = "https://fuyao.aicubes.cn"
_FUYAO_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)

_ST_KEYWORDS = ("ST", "*ST", "退市", "N", "C")


def _get_cache_db_path() -> Path:
    """获取 pipeline_cache.db 路径（兼容测试隔离）"""
    try:
        settings = get_settings()
        return Path(settings.cache_dir) / "pipeline_cache.db"
    except Exception:
        return Path("outputs/cache/pipeline_cache.db")


def _get_all_tickers(db_path: Path) -> list[str]:
    """从缓存数据库获取所有股票代码（排除北交所）"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM kline_cache ORDER BY ticker")
    all_tickers = [r[0] for r in cur.fetchall()]
    conn.close()

    # 排除北交所（bj.开头）
    astock_tickers = [t for t in all_tickers if not t.startswith("bj.")]
    logger.info(f"📊 全市场扫描：共 {len(all_tickers)} 只，排除北交所后 {len(astock_tickers)} 只")
    return astock_tickers


def _strip_prefix(ticker: str) -> str:
    """去除交易所前缀，返回 6 位纯数字代码。"""
    return ticker.split(".", 1)[1] if "." in ticker else ticker


def _is_st(name: str) -> bool:
    """判断股票名称是否含 ST 标识。"""
    return any(kw in name for kw in _ST_KEYWORDS)


def _ticker_to_thscode(ticker: str) -> str:
    """将 sh.600887 / sz.000001 转换为同花顺 thscode 格式 (600887.SH / 000001.SZ)。"""
    code = _strip_prefix(ticker)
    if ticker.startswith("sh."):
        return f"{code}.SH"
    elif ticker.startswith("sz."):
        return f"{code}.SZ"
    return code


def _fetch_pe_snapshot(tickers: list[str], batch_size: int = 50) -> dict[str, dict]:
    """批量获取股票的 PE_TTM 和名称（通过同花顺 Fuyao API）。

    Parameters
    ----------
    tickers : list[str]
        sh./sz. 前缀的股票代码列表
    batch_size : int
        每批数量，默认 50

    Returns
    -------
    dict[str, dict]
        {ticker（6位代码）: {"pe": pe_ttm, "name": stock_name}}，
        获取失败或 PE 缺失的 code 不出现在结果中
    """
    if not _FUYAO_API_KEY:
        logger.warning("FUYAO_API_KEY 未配置，跳过 PE 过滤")
        return {}

    result: dict[str, dict] = {}
    for i in range(0, len(tickers), batch_size):
        batch_tickers = tickers[i : i + batch_size]
        ths_param = ",".join(_ticker_to_thscode(t) for t in batch_tickers)
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
            "MultiScan/1.0",
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
                # thscode 格式: 600887.SH → 取前6位作为 key
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
            logger.debug(f"PE 获取失败（批次 {i // batch_size + 1}）: {e}")
    return result


def _filter_by_pe(
    results: list[dict[str, Any]], pe_map: dict[str, dict]
) -> tuple[list[dict[str, Any]], int]:
    """根据 PE 过滤结果，剔除亏损（PE<0）、PE≥40 及 PE 缺失的股票。

    Parameters
    ----------
    results : list[dict[str, Any]]
        扫描结果列表，每条含 'ticker' 字段
    pe_map : dict[str, dict]
        {6位代码: {"pe": pe_ttm, "name": stock_name}}

    Returns
    -------
    tuple[list[dict[str, Any]], int]
        (过滤后结果, 剔除数量)
    """
    filtered: list[dict[str, Any]] = []
    removed = 0
    for r in results:
        code = _strip_prefix(r.get("ticker", ""))
        entry = pe_map.get(code)
        if entry is None:
            # PE 数据缺失，剔除
            removed += 1
            logger.debug(f"  剔除 {r.get('ticker')} PE=缺失")
            continue
        pe = entry["pe"]
        if pe < 0 or pe >= 40:
            removed += 1
            logger.debug(f"  剔除 {r.get('ticker')} PE={pe:.1f}（{'亏损' if pe < 0 else '过高'}）")
            continue
        # 填充股票名称（如果当前结果为空）
        if not r.get("name"):
            r["name"] = entry.get("name", "")
        filtered.append(r)
    return filtered, removed


def _fetch_pe_percentiles(tickers: list[str], batch_size: int = 10) -> dict[str, float]:
    """批量获取股票的 PE 历史分位（0-100）。

    使用 akshare 的 stock_value_em 接口获取每只股票的历史 PE 序列并计算分位。

    Parameters
    ----------
    tickers : list[str]
        6位纯数字股票代码列表
    batch_size : int
        每批处理数量，默认 10

    Returns
    -------
    dict[str, float]
        {code: pe_percentile}，获取失败则不出现在结果中
    """
    try:
        import akshare as _ak
    except ImportError:
        logger.warning("akshare 未安装，跳过 PE 历史分位获取")
        return {}

    result: dict[str, float] = {}
    total = len(tickers)
    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        logger.info(f"  PE分位批次 [{i+1}~{i+len(batch)}/{total}]...")
        for code in batch:
            try:
                df = _ak.stock_value_em(symbol=code)
                if df is None or len(df) == 0:
                    continue
                pe_col = "PE(TTM)"
                pe_values = df[pe_col].dropna().astype(float)
                pe_values = pe_values[pe_values > 0]
                if len(pe_values) == 0:
                    continue
                current_pe = float(pe_values.iloc[-1])
                count_below = int((pe_values < current_pe).sum())
                percentile = round((count_below / len(pe_values)) * 100.0, 1)
                result[code] = percentile
            except Exception as e:
                logger.debug(f"{code} PE分位获取失败: {e}")
        time.sleep(0.5)  # 避免限流
    return result


def scan_multi_head_stocks(
    tickers: list[str], max_workers: int = 20, top_n: int | None = None
) -> list[dict[str, Any]]:
    """扫描全市场，找出多头排列的股票（ST 股在内部剔除）。

    Args:
        tickers: 股票代码列表（带 sh./sz. 前缀）
        max_workers: 最大并发数
        top_n: 只返回分数最高的 N 只（None 表示全部）

    Returns:
        按分数降序排列的股票分析结果列表
    """
    results: list[dict[str, Any]] = []
    total = len(tickers)
    logger.info(f"🔍 开始扫描 {total} 只股票，最大并发 {max_workers}...")

    # 批量分析
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {executor.submit(analyze_stock, ticker): ticker for ticker in tickers}

        completed = 0
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            completed += 1
            if completed % 500 == 0:
                logger.info(f"  进度: {completed}/{total} ({completed * 100 // total}%)")

            try:
                result = future.result()
                if result.get("status") == "ok":
                    name = result.get("name", "")
                    # 剔除 ST / *ST 等异常股票
                    if _is_st(name):
                        logger.debug(f"  剔除 ST 股: {ticker} {name}")
                        continue
                    # 筛选多头排列且分数>=50的股票
                    if result.get("ma_signal") == "多头排列✅" and result.get("score", 0) >= 50:
                        results.append(result)
            except Exception as e:
                logger.warning(f"  ⚠️ {ticker} 分析失败: {e}")

    # 按分数降序排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    if top_n:
        results = results[:top_n]

    logger.info(f"✅ 扫描完成：找到 {len(results)} 只多头排列股票（分数≥50，已剔除 ST）")
    return results


def _build_summary_card(
    results: list[dict[str, Any]], date_str: str, ai_result: dict[str, str] | None = None
) -> str:
    """构建汇总消息卡片，可选附加 AI 核实摘要。"""
    if not results:
        return f"📊 {date_str} 全市场扫描结果\n\n未找到多头排列且分数≥50的股票"

    lines = [f"📊 **{date_str} 全市场多头排列扫描**\n", f"共找到 **{len(results)}** 只符合条件"]
    lines.append("")

    # 分类统计
    buy_stocks = [r for r in results if r.get("score", 0) >= 65]
    hold_stocks = [r for r in results if 50 <= r.get("score", 0) < 65]

    if buy_stocks:
        lines.append(f"🟢 **买入关注**（{len(buy_stocks)}只）")
        for r in buy_stocks[:10]:
            val_extra = ""
            if r.get("valuation_score") is not None:
                v_emoji = "🟢" if r["valuation_score"] >= 70 else ("🟡" if r["valuation_score"] >= 50 else "🔴")
                vp = r.get("buy_price_suggested")
                vp_str = f" 建议买入价={vp:.2f}" if vp else ""
                val_extra = f"  估值{v_emoji}{r['valuation_score']:.0f}分{vp_str}"
            lines.append(
                f"  • {r['ticker']} {r['name']}: {r['score']}分 {r['trend']} {r['vol_signal']}{val_extra}"
            )
        lines.append("")

    if hold_stocks:
        lines.append(f"🟡 **观望等待**（{len(hold_stocks)}只）")
        for r in hold_stocks[:10]:
            lines.append(
                f"  • {r['ticker']} {r['name']}: {r['score']}分 {r['trend']} {r['vol_signal']}"
            )
        lines.append("")

    # Top 5 详情
    lines.append("---")
    lines.append("📈 **Top 5 详情**")
    for i, r in enumerate(results[:5], 1):
        lines.append(f"\n**{i}. {r['ticker']} {r['name']}** ({r['score']}分)")
        lines.append(f"   收盘价: {r.get('close', '?')}  今日涨跌: {r.get('change', 0):+.2f}%")
        lines.append(f"   趋势: {r['trend']}  均线: {r['ma_signal']}  量能: {r['vol_signal']}")
        lines.append(
            f"   MA5={r.get('ma5', '?')} MA10={r.get('ma10', '?')} MA20={r.get('ma20', '?')}"
        )
        buy_points = r.get("buy_points", [])
        if buy_points:
            lines.append(f"   买点: {'、'.join(buy_points[:3])}")
        sell_points = r.get("sell_points", [])
        if sell_points:
            lines.append(f"   卖点: {'、'.join(sell_points[:3])}")
        # 估值信息
        if r.get("valuation_score") is not None:
            v_emoji = "🟢" if r["valuation_score"] >= 70 else ("🟡" if r["valuation_score"] >= 50 else "🔴")
            lines.append(f"   估值{v_emoji} {r['valuation_score']:.0f}分  PE分位={r.get('pe_percentile', 'N/A')}")
            if r.get("buy_price_suggested") is not None:
                lines.append(f"   💡 建议买入价: {r['buy_price_suggested']:.2f}")
            if r.get("valuation_conclusion"):
                lines.append(f"   📝 {r['valuation_conclusion']}")

    # AI 核实摘要
    if ai_result:
        lines.append("")
        lines.append("---")
        lines.append("🤖 **AI 核实摘要**")
        summary = ai_result.get("analysis_summary", "")
        if summary:
            lines.append(f"\n{summary}")
        top_picks = ai_result.get("top_picks", "")
        if top_picks and top_picks != "无":
            lines.append(f"\n**🏆 Top 推荐：** {top_picks}")
        risk_alerts = ai_result.get("risk_alerts", "")
        if risk_alerts and risk_alerts != "无" and risk_alerts != "无明显异常风险":
            lines.append(f"\n**⚠️ 风险提示：** {risk_alerts}")
        conclusion = ai_result.get("conclusion", "")
        if conclusion:
            lines.append(f"\n**📝 结论：** {conclusion}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="全市场扫描多头排列股票")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="分析日期")
    parser.add_argument("--top", type=int, default=None, help="只返回前N只（默认全部）")
    parser.add_argument("--workers", type=int, default=20, help="并发数（默认20）")
    args = parser.parse_args()

    _load_env()

    # 前置：检查缓存数据新鲜度
    logger.info(f"[数据检查] {check_data_freshness(logger.info)}")

    # 获取数据库路径
    db_path = _get_cache_db_path()
    if not db_path.exists():
        logger.error(f"❌ 缓存数据库不存在: {db_path}")
        sys.exit(1)

    # 获取所有A股代码
    tickers = _get_all_tickers(db_path)

    # 扫描多头排列股票
    results = scan_multi_head_stocks(tickers, max_workers=args.workers, top_n=args.top)

    # PE 过滤：剔除亏损（PE<0）、PE≥40、PE 缺失的股票
    pe_map: dict[str, dict] = {}
    if results:
        pe_tickers = [r["ticker"] for r in results]
        logger.info(f"📊 获取 {len(pe_tickers)} 只股票的 PE 数据用于过滤...")
        pe_map = _fetch_pe_snapshot(pe_tickers)
        logger.info(f"  PE API 返回 {len(pe_map)} 只有效数据")
        results, pe_removed = _filter_by_pe(results, pe_map)
        logger.info(f"PE 过滤：剔除 {pe_removed} 只（亏损/PE≥40/缺失），剩余 {len(results)} 只")

    # 估值评估：为每只股票计算估值评分和建议买入价
    if results:
        logger.info("📊 启动估值评估...")
        val_tickers = [_strip_prefix(r["ticker"]) for r in results]
        # 批量获取 PE 分位（akshare）
        pe_percentiles = _fetch_pe_percentiles(val_tickers)
        for r in results:
            code = _strip_prefix(r["ticker"])
            price = r.get("close", 0)
            pe = _safe_float(pe_map.get(code, {}).get("pe"))
            try:
                pe_pct = pe_percentiles.get(code)
                # 反推 EPS 和每股净资产（PB 从估值快照中获取）
                eps = price / pe if pe and pe > 0 else None
                # PB 不在 pe_map 中，用默认值估算
                val_result = evaluate_valuation(
                    ticker=code,
                    name=r.get("name", ""),
                    current_price=price,
                    pe_ttm=pe,
                    pb=None,  # 暂不获取 PB，避免额外 API 调用
                    roe=None,
                    pe_percentile=pe_pct,
                    eps_ttm=eps,
                )
                r["valuation_score"] = val_result.valuation_score
                r["buy_price_suggested"] = val_result.combined_price
                r["valuation_conclusion"] = val_result.conclusion
                r["pe_percentile"] = pe_pct
            except Exception as e:
                logger.debug(f"{r['ticker']} 估值评估失败: {e}")
                r["valuation_score"] = None
                r["buy_price_suggested"] = None
                r["valuation_conclusion"] = ""
        logger.info(f"✅ 估值评估完成：{len(results)} 只股票")

    # AI 核实
    logger.info("🤖 启动 AI 核实...")
    ai_result = ai_verify(results, args.date)
    if ai_result.get("analysis_summary", "").startswith("LLM"):
        logger.info("AI 核实跳过：LLM 未配置")
    else:
        logger.info(f"AI 核实完成：{ai_result.get('analysis_summary', '')}")

    # 保存结果
    date_str = args.date.replace("-", "")
    result_file = _RESULTS_DIR / f"multi_head_{date_str}.txt"
    result_file.write_text(_build_summary_card(results, args.date, ai_result), encoding="utf-8")
    logger.info(f"💾 结果已保存: {result_file}")

    # 飞书推送
    if results:
        config = load_config()
        summary = _build_summary_card(results, args.date, ai_result)

        # 逐只推送详情
        for r in results[:20]:  # 最多推送20只
            card = (
                f"📊 **{args.date} · {r['ticker']} {r['name']}**\n"
                f"🟢 **综合分：{r['score']}**\n"
                f"收盘价：{r.get('close', '?')}  今日涨跌：{r.get('change', 0):+.2f}%\n"
                f"趋势：{r['trend']}　均线信号：{r['ma_signal']}　量能信号：{r['vol_signal']}\n"
                f"MA5={r.get('ma5', '?')}　MA10={r.get('ma10', '?')}　MA20={r.get('ma20', '?')}\n"
            )
            buy_points = r.get("buy_points", [])
            if buy_points:
                card += f"🟢 买点信号：{'、'.join(buy_points[:3])}\n"
            ok = send_notification(
                mode="text", config=config, content=card, title=f"{r['ticker']} {r['name']}"
            )
            logger.info(f"  {'✅' if ok else '❌'} 已推送飞书: {r['ticker']} {r['name']}")

        # 汇总推送
        ok = send_notification(
            mode="text",
            config=config,
            content=summary,
            title=f"📊 全市场多头排列扫描 {args.date}",
        )
        logger.info(f"{'✅' if ok else '❌'} 汇总飞书推送完成")
    else:
        logger.warning("⚠️ 未找到符合条件的股票")


if __name__ == "__main__":
    main()
