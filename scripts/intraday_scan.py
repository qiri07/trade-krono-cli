#!/usr/bin/env python3
"""盘中技术信号扫描器 — 分钟 K 线上检测金叉 / 多头 / MACD / 三线共振。

数据源：
  - 分钟 K 线：akshare ``stock_zh_a_minute``（支持 5/15/30/60min，覆盖股票 + ETF，约 13 个月）
  - 实时价：腾讯财经 ``fetch_realtime_quote``（免费无 key）

mootdx 通达信行情服务器在本网络环境不可达，故盘中 K 线改用 akshare 新浪分钟接口。

用法：
  # 扫白名单（静态 + 巴菲特动态白名单合并），全部 4 类信号
  uv run python scripts/intraday_scan.py

  # 仅检测 MA5×MA13 金叉
  uv run python scripts/intraday_scan.py --mode golden_cross

  # 三线共振（5min + 15min + 60min 三周期 MACD DIF 同时 > 0）
  uv run python scripts/intraday_scan.py --mode resonance

  # 指定股票代码（裸代码或带前缀均可）
  uv run python scripts/intraday_scan.py --tickers "600519,000858,510300"

  # 至少命中 2 类信号才显示
  uv run python scripts/intraday_scan.py --min-signals 2

  # 飞书推送
  uv run python scripts/intraday_scan.py --push 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from scripts._utils import (
    is_bse_or_hk,
    is_etf_ticker,
    normalize_ticker_key,
)
from scripts.daily_analysis import _get_dynamic_whitelist, _get_whitelist
from scripts.feishu_core import load_config, send_notification

# 项目根目录（供结果目录使用）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 常量 ─────────────────────────────────────────────────────────────────────
_RESULTS_DIR = _PROJECT_ROOT / "outputs" / "results"

# 扫描用的 15min 主周期；金叉 / 多头 / MACD 均在该周期上判断
PRIMARY_PERIOD = "15"
# 拉取 bar 数量上限（akshare 接口单次可返回 ~1970 根，取尾部足够多根即可）
_FETCH_PERIODS = 400
# 三线共振盘中变体：5min / 15min / 60min 三周期
RESONANCE_PERIODS = ("5", "15", "60")
# 用倒数第 2 根（已收盘 bar）作主判断，最后一根可能未完成
_REF_IDX = -2

# 信号类型
SIG_GOLDEN_CROSS = "golden_cross"  # MA5 上穿 MA13（触发型）
SIG_MULTI_HEAD = "multi_head"  # close > MA5 > MA10 > MA20（状态型）
SIG_MACD_RISING = "macd_rising"  # 15min MACD DIF > 0 且上升
SIG_RESONANCE = "resonance"  # 5min + 15min + 60min 三周期 DIF 同时 > 0

_SIGNAL_LABELS: dict[str, str] = {
    SIG_GOLDEN_CROSS: "🟡 MA5×MA13金叉",
    SIG_MULTI_HEAD: "🔴 多头排列",
    SIG_MACD_RISING: "🟢 MACD DIF↑",
    SIG_RESONANCE: "⭐ 三线共振",
}

_MODE_TO_SIGNALS: dict[str, list[str]] = {
    "all": [SIG_GOLDEN_CROSS, SIG_MULTI_HEAD, SIG_MACD_RISING, SIG_RESONANCE],
    "golden_cross": [SIG_GOLDEN_CROSS],
    "multi_head": [SIG_MULTI_HEAD],
    "macd": [SIG_MACD_RISING],
    "resonance": [SIG_RESONANCE],
}


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class IntradayResult:
    """单只股票的盘中扫描结果。"""

    ticker: str
    name: str = ""
    is_etf: bool = False
    price: float | None = None
    signals: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


# ── 分钟 K 线拉取 ────────────────────────────────────────────────────────────


def _ticker_to_akshare_symbol(ticker: str) -> str:
    """sh.600519 → sh600519（akshare stock_zh_a_minute 期望的符号格式）。"""
    prefix, _, code = ticker.partition(".")
    prefix = prefix.lower()
    if prefix not in ("sh", "sz"):
        # 归一化后非 sh/sz 的代码（如 bj）无法用新浪分钟接口
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def _fetch_minute_bars(ticker: str, period: str = PRIMARY_PERIOD) -> pd.DataFrame | None:
    """拉取指定周期分钟 K 线，返回按时间升序的 DataFrame（close 已转 float）。

    失败或无数据时返回 None。
    """
    import akshare as ak

    symbol = _ticker_to_akshare_symbol(ticker)
    try:
        df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="")
    except Exception as e:
        logger.debug(f"akshare 分钟 K 线异常 {ticker} {period}m: {str(e)[:150]}")
        return None
    if df is None or df.empty:
        return None
    df = df.copy()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    if df.empty:
        return None
    df = df.sort_values("day").reset_index(drop=True)
    return df


# ── 技术指标计算 ─────────────────────────────────────────────────────────────


def _coerce_floats(values: list[Any]) -> list[float]:
    """将序列安全转换为 float 列表（非数字项按 NaN 处理）。"""
    coerced: list[float] = []
    for v in values:
        try:
            coerced.append(float(v))
        except (TypeError, ValueError):
            coerced.append(float("nan"))
    return coerced


def _calc_ma(closes: list[float], window: int) -> list[float]:
    """简单移动平均，返回与 closes 等长的列表（前 window-1 个为 NaN）。"""
    closes = _coerce_floats(closes)
    n = len(closes)
    if n < window:
        return [float("nan")] * n
    result: list[float] = [float("nan")] * (window - 1)
    rolling = sum(closes[:window])
    result.append(rolling / window)
    for i in range(window, n):
        rolling += closes[i] - closes[i - window]
        result.append(rolling / window)
    return result


def _calc_macd_dif(closes: list[float], fast: int = 12, slow: int = 26) -> list[float]:
    """MACD DIF 线（EMA_fast - EMA_slow），与 closes 等长。"""
    closes = _coerce_floats(closes)
    if len(closes) < slow:
        return [float("nan")] * len(closes)

    def _ema(values: list[float], span: int) -> list[float]:
        k = 2 / (span + 1)
        ema: list[float] = [values[0]]
        for v in values[1:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    return [f - s for f, s in zip(ema_fast, ema_slow, strict=True)]


def _check_golden_cross(closes: list[float]) -> bool:
    """MA5 刚上穿 MA13（已收盘 bar 处：前一根 MA5<=MA13，当前 MA5>MA13）。"""
    if len(closes) < 14:
        return False
    ma5 = _calc_ma(closes, 5)
    ma13 = _calc_ma(closes, 13)
    i = _REF_IDX
    if ma5[i] != ma5[i] or ma13[i] != ma13[i]:  # NaN 检查
        return False
    return ma5[i] > ma13[i] and ma5[i - 1] <= ma13[i - 1]


def _check_multi_head(closes: list[float]) -> bool:
    """多头排列：close > MA5 > MA10 > MA20（在已收盘 bar 处判断）。"""
    if len(closes) < 21:
        return False
    i = _REF_IDX
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    px = closes[i]
    return px > ma5[i] > ma10[i] > ma20[i]


def _check_macd_rising(closes: list[float]) -> bool:
    """15min MACD DIF > 0 且 DIF 上升（已收盘 bar 处）。"""
    if len(closes) < 30:
        return False
    dif = _calc_macd_dif(closes)
    i = _REF_IDX
    return dif[i] > 0 and dif[i] > dif[i - 1]


def _check_resonance(ticker: str) -> bool:
    """三线共振盘中变体：5min + 15min + 60min 三周期 MACD DIF 同时 > 0。"""
    for period in RESONANCE_PERIODS:
        df = _fetch_minute_bars(ticker, period)
        if df is None or len(df) < 30:
            return False
        closes = df["close"].tolist()
        dif = _calc_macd_dif(closes)
        if dif[_REF_IDX] <= 0:
            return False
    return True


# ── 实时价 ───────────────────────────────────────────────────────────────────


def _get_price(ticker: str, fallback_close: float | None) -> float | None:
    """腾讯财经实时价（降级：分钟 K 线最新 close）。"""
    try:
        from trade_krono_cli.data import fetch_realtime_quote

        q = fetch_realtime_quote(ticker)
        if q.get("price") is not None:
            return float(q["price"])
    except Exception:
        pass
    return fallback_close


# ── 白名单 ───────────────────────────────────────────────────────────────────


def _load_whitelist() -> list[str]:
    """合并静态白名单 + 动态巴菲特白名单，返回带前缀的 A 股 ticker（排除北交所/港股）。"""
    static = _get_whitelist()
    static_codes = [c.strip() for c in static.split(",") if c.strip()]
    dynamic = _get_dynamic_whitelist()
    seen: set[str] = set()
    merged: list[str] = []
    for code in static_codes + dynamic:
        code = code.strip()
        if not code or is_bse_or_hk(code):
            continue
        normed = normalize_ticker_key(code)
        if normed in seen:
            continue
        seen.add(normed)
        merged.append(normed)
    return merged


def _normalize_tickers(raw: list[str]) -> list[str]:
    """将裸代码或带前缀代码统一归一化为 sh./sz. 前缀格式，排除北交所/港股。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        t = t.strip()
        if not t:
            continue
        normed = normalize_ticker_key(t)
        if is_bse_or_hk(normed):
            logger.debug(f"跳过北交所/港股: {normed}")
            continue
        if normed in seen:
            continue
        seen.add(normed)
        out.append(normed)
    return out


# ── 扫描核心 ─────────────────────────────────────────────────────────────────


def _resolve_name(ticker: str) -> str:
    """解析股票名称（来自 _utils.STOCK_NAMES 映射，未收录返回空字符串）。"""
    try:
        from scripts._utils import STOCK_NAMES

        code = ticker.split(".")[-1]
        return STOCK_NAMES.get(code, "")
    except Exception:
        return ""


def _scan_one(ticker: str, signals: list[str]) -> IntradayResult:
    """扫描单只股票，返回命中结果。

    主周期（15min）拉一次；三线共振模式会额外拉 5min/60min。
    """
    result = IntradayResult(
        ticker=ticker,
        name=_resolve_name(ticker),
        is_etf=is_etf_ticker(ticker),
    )

    if SIG_RESONANCE in signals:
        if _check_resonance(ticker):
            result.signals.append(SIG_RESONANCE)

    # 主周期信号
    df = _fetch_minute_bars(ticker, PRIMARY_PERIOD)
    closes = df["close"].tolist() if df is not None else []
    fallback_close = closes[_REF_IDX] if len(closes) > 1 else None

    for sig in signals:
        if sig == SIG_RESONANCE:
            continue  # 上面已处理
        if not closes:
            continue
        if sig == SIG_GOLDEN_CROSS and _check_golden_cross(closes):
            result.signals.append(sig)
            result.detail["ma5"] = _calc_ma(closes, 5)[_REF_IDX]
            result.detail["ma13"] = _calc_ma(closes, 13)[_REF_IDX]
        elif sig == SIG_MULTI_HEAD and _check_multi_head(closes):
            result.signals.append(sig)
            result.detail["ma5"] = _calc_ma(closes, 5)[_REF_IDX]
            result.detail["ma10"] = _calc_ma(closes, 10)[_REF_IDX]
            result.detail["ma20"] = _calc_ma(closes, 20)[_REF_IDX]
            result.detail["price"] = closes[_REF_IDX]
        elif sig == SIG_MACD_RISING and _check_macd_rising(closes):
            result.signals.append(sig)
            result.detail["dif"] = _calc_macd_dif(closes)[_REF_IDX]

    if result.signals:
        result.price = _get_price(ticker, fallback_close)
        if "price" not in result.detail and result.price is not None:
            result.detail["price"] = result.price

    return result


def scan(
    tickers: list[str],
    signals: list[str] | None = None,
    max_workers: int = 16,
    min_signals: int = 1,
    verbose: bool = True,
) -> list[IntradayResult]:
    """并行扫描股票列表，返回命中至少 min_signals 个信号的结果（按信号数降序）。"""
    target_signals = signals if signals is not None else _MODE_TO_SIGNALS["all"]
    if verbose:
        logger.info(
            f"🔍 盘中扫描：{len(tickers)} 只 | 信号: {target_signals} | 并发: {max_workers}"
        )

    t0 = time.time()
    hits: list[IntradayResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_one, t, target_signals): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                res = future.result()
                if len(res.signals) >= min_signals:
                    hits.append(res)
            except Exception as e:
                logger.warning(f"扫描异常 {ticker}: {e}")

    elapsed = time.time() - t0
    hits.sort(key=lambda r: len(r.signals), reverse=True)

    if verbose:
        logger.info(f"✅ 完成：{len(hits)} 只命中 / {len(tickers)} 只扫描（{elapsed:.1f}s）")

    return hits


# ── 输出 ─────────────────────────────────────────────────────────────────────


def _build_rich_table(results: list[IntradayResult]) -> Table:
    """构建 Rich 表格。"""
    table = Table(title="盘中技术信号", show_lines=False)
    table.add_column("代码", style="bold", no_wrap=True)
    table.add_column("名称", width=10, no_wrap=True)
    table.add_column("当前价", justify="right")
    table.add_column("命中信号", width=32, no_wrap=True)
    table.add_column("详情", width=34, no_wrap=True)
    for r in results:
        price_str = f"{r.price:.2f}" if r.price is not None else "-"
        sig_str = " ".join(_SIGNAL_LABELS.get(s, s) for s in r.signals)
        detail_parts: list[str] = []
        if "dif" in r.detail:
            detail_parts.append(f"DIF={float(r.detail['dif']):.4f}")
        if "ma5" in r.detail and "ma20" in r.detail:
            detail_parts.append(
                f"MA5={float(r.detail.get('ma5', 0)):.2f} "
                f"MA10={float(r.detail.get('ma10', 0)):.2f} "
                f"MA20={float(r.detail.get('ma20', 0)):.2f}"
            )
        detail_str = " ".join(detail_parts) if detail_parts else "-"
        table.add_row(r.ticker, r.name[:10] or "-", price_str, sig_str, detail_str[:48])
    return table


def _build_feishu_card(results: list[IntradayResult], mode: str) -> str:
    """生成飞书文本卡片。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    sig_names = _MODE_TO_SIGNALS.get(mode, _MODE_TO_SIGNALS["all"])
    labels = [_SIGNAL_LABELS.get(s, s) for s in sig_names]
    header = f"⚡️ 盘中扫描 {ts}\n模式：{'/'.join(labels)}\n命中 {len(results)} 只"
    lines = [header, ""]
    for r in results:
        price = f"{r.price:.2f}" if r.price is not None else "?"
        sigs = " ".join(_SIGNAL_LABELS.get(s, s) for s in r.signals)
        lines.append(f"  {r.ticker} {r.name}  ¥{price}  {sigs}")
    lines.append("")
    return "\n".join(lines)


def _save_json(results: list[IntradayResult], mode: str) -> Path:
    """保存结果到 JSON。"""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = _RESULTS_DIR / f"intraday_scan_{ts}.json"
    payload: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "hits": len(results),
        "results": [
            {
                "ticker": r.ticker,
                "name": r.name,
                "is_etf": r.is_etf,
                "price": r.price,
                "signals": r.signals,
                "detail": r.detail,
            }
            for r in results
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="盘中技术信号扫描器")
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="逗号分隔代码（裸代码或带前缀），未指定则扫白名单",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="all",
        choices=list(_MODE_TO_SIGNALS.keys()),
        help="信号模式（默认 all）",
    )
    parser.add_argument(
        "--min-signals",
        type=int,
        default=1,
        help="至少命中 N 个信号才显示（默认 1）",
    )
    parser.add_argument("--push", type=int, default=0, help="飞书推送：1=汇总推送")
    parser.add_argument("--verbose", type=int, default=1, help="0=安静模式")
    args = parser.parse_args()

    console = Console()
    tickers = _normalize_tickers(args.tickers.split(",")) if args.tickers else _load_whitelist()

    if not tickers:
        console.print(
            "[yellow]⚠️ 白名单为空，请检查 outputs/results/buffett_dynamic_whitelist.txt[/yellow]"
        )
        return

    logger.info(f"扫描范围：{len(tickers)} 只 | 模式：{args.mode} | min-signals={args.min_signals}")

    results = scan(
        tickers=tickers,
        signals=_MODE_TO_SIGNALS[args.mode],
        min_signals=args.min_signals,
        verbose=bool(args.verbose),
    )

    if results:
        console.print(_build_rich_table(results))
    else:
        console.print("[dim]无命中信号（当前时段无符合全部条件的股票）[/dim]")

    out_path = _save_json(results, mode=args.mode)
    logger.info(f"💾 结果已保存: {out_path}")

    if args.push and results:
        config = load_config()
        card = _build_feishu_card(results, mode=args.mode)
        ok = send_notification(
            mode="text",
            config=config,
            content=card,
            title=f"⚡️ 盘中扫描 {datetime.now().strftime('%H:%M')} — 命中 {len(results)} 只",
        )
        logger.info(f"{'✅' if ok else '❌'} 飞书推送完成")
    elif args.push and not results:
        logger.debug("无命中，跳过飞书推送")


if __name__ == "__main__":
    main()
