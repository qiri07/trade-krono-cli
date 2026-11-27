#!/usr/bin/env python3
"""scripts/launch_signal_scan.py — 大行情启动信号扫描器。

融合五大模型（文心一言 / 腾讯元宝 / DeepSeek / 通义千问 / 智谱 ChatGLM）
对“大行情启动前夕”量能与价格特征的结论，对全市场（或指定股票池）
逐只计算启动信号，落盘 JSON 并可选飞书推送。

信号逻辑（四维打分 + Kill-Switch 一票否决）：
  1. 蓄势质量（30 分）：均线粘合、波动率收缩、蓄势缩量
  2. 点火强度（25 分）：突破幅度、VR 倍数、换手率、K 线形态
  3. 确认质量（30 分，权重最高）：站稳突破位、回踩缩量、量能中枢上移、均线多头
  4. 环境与逻辑（15 分）：板块共振、大盘趋势

Kill-Switch（任一触发即 signal=False）：
  - 突破后 3 日内跌回突破位
  - 回踩放量而非缩量
  - 突破次日量能萎缩至均量线下
  - 换手率暴增而价格滞涨

用法：
  uv run python scripts/launch_signal_scan.py
  uv run python scripts/launch_signal_scan.py --date 2026-11-26 --tickers "600519,000858"
  uv run python scripts/launch_signal_scan.py --min-score 70 --vr-min 1.5 --vr-max 3.0
  uv run python scripts/launch_signal_scan.py --no-push            # 仅落盘，不推飞书
  uv run python scripts/launch_signal_scan.py --push 1 --per-stock-push 0
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from typing import cast

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import (
    expected_trading_day,
    filter_stock_universe,
    get_all_tickers,
    load_metadata_index,
)
from scripts._utils.launch_signals import (
    LaunchSignalParams,
    compute_launch_signal,
)
from scripts.feishu_utils import push_feishu
from trade_krono_cli.config import get_settings


def _load_kline_from_cache(ticker: str, db_path: Path) -> pd.DataFrame | None:
    """从 pipeline_cache.db 读取单只股票的全部日线 K 线（DataFrame）。

    参考 trade_krono_cli.prediction_eval_backtest._get_price_from_cache 的读取模式。

    Parameters
    ----------
    ticker : str
        带前缀 ticker，如 sh.600519。
    db_path : Path
        pipeline_cache.db 路径。

    Returns:
        按 timestamps 升序排列的 K 线 DataFrame，含 close/high/low/volume 列；
        读取失败或无数据时返回 None。
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM kline_cache WHERE ticker = ? ORDER BY end DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        df = pd.read_pickle(BytesIO(row[0]))
    except Exception:
        return None
    if df is None or df.empty:
        return None
    # 统一列名小写，按 timestamps 升序
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if "timestamps" in df.columns:
        df = df.sort_values("timestamps").reset_index(drop=True)
    required = {"close", "high", "low", "volume"}
    if not required.issubset(set(df.columns)):
        return None
    return df


def _ticker_to_code6(ticker: str) -> str:
    """把带前缀 ticker 转为 6 位裸代码，如 sh.600519 -> 600519。"""
    return ticker.split(".")[-1]


def _build_launch_signal_entry(
    ticker: str,
    result,
    metadata: dict[str, dict[str, object]],
) -> dict[str, object]:
    """构造单只信号的 JSON 条目。"""
    code6 = _ticker_to_code6(ticker)
    name = ""
    meta = _lookup_meta(metadata, ticker)
    if isinstance(meta, dict):
        name = str(meta.get("name", "") or "")
    entry: dict[str, object] = {
        "ticker": ticker,
        "code": code6,
        "name": name,
        "signal": result.signal,
        "score": result.score,
        "reasons": result.reasons,
        "kill_switch": result.kill_switch,
        "metrics": result.metrics,
    }
    return entry


def _lookup_meta(
    metadata: dict[str, dict[str, object]], ticker: str
) -> dict[str, object]:
    """在元数据索引中查单只票，兼容带前缀与裸代码两种键。"""
    from scripts._utils.metadata import normalize_ticker_key, strip_ticker_prefix

    normalized = normalize_ticker_key(ticker)
    bare = strip_ticker_prefix(normalized)
    for key in (ticker, normalized, bare):
        value = metadata.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _tune_params(base: LaunchSignalParams, iteration: int) -> LaunchSignalParams:
    """第 iteration 轮的放宽参数（iteration=0 为基准，越大越宽松）。

    用于 --auto-tune：当首轮无信号时，依次放宽缩量、突破幅度、VR、确认天数等
    阈值，并在放宽后的参数下重跑全市场，直至出现信号或达到 max_tune 轮。
    """
    relax = 1.0 + 0.25 * iteration
    return LaunchSignalParams(
        # ── 蓄势质量 ──
        vol_dry_ratio=min(0.7, base.vol_dry_ratio * relax),
        min_dry_days=max(4, base.min_dry_days - 2 * iteration),
        ma_squeeze_pct=min(0.10, base.ma_squeeze_pct * (1 + 0.15 * iteration)),
        ma_squeeze_days=max(4, base.ma_squeeze_days - 2 * iteration),
        vol_squeeze_percentile=min(0.7, base.vol_squeeze_percentile + 0.1 * iteration),
        lookback_days=min(25, base.lookback_days + 5 * iteration),
        # ── 点火强度 ──
        breakout_min_pct=max(0.005, base.breakout_min_pct * (1 - 0.25 * iteration)),
        breakout_max_pct=min(0.15, base.breakout_max_pct + 0.02 * iteration),
        vr_min=max(1.0, base.vr_min - 0.3 * iteration),
        vr_max=min(8.0, base.vr_max + 1.0 * iteration),
        turnover_min=max(0.005, base.turnover_min * (1 - 0.1 * iteration)),
        turnover_max=min(0.40, base.turnover_max * (1 + 0.1 * iteration)),
        turnover_warn=min(0.50, base.turnover_warn * (1 + 0.1 * iteration)),
        # ── 确认质量 ──
        confirm_days=max(1, base.confirm_days - iteration),
        pullback_vol_ratio_max=min(
            0.95, base.pullback_vol_ratio_max + 0.1 * iteration
        ),
        vol_center_shift_min=max(0.7, base.vol_center_shift_min - 0.1 * iteration),
        # ── 权重保持不变 ──
        weight_buildup=base.weight_buildup,
        weight_ignition=base.weight_ignition,
        weight_confirm=base.weight_confirm,
        weight_env=base.weight_env,
    )


def _grid_param_sets(base: LaunchSignalParams) -> list[tuple[str, LaunchSignalParams, float]]:
    """预设多组参数组合，用于网格搜索测试不同启动行情阈值。

    每组返回 (组名, 参数, min_score)。不同组分别放宽不同维度的阈值，
    以挖掘在不同市场环境下可能启动的股票：

    - 基准：默认参数，min_score=60（较严格）
    - 放宽缩量突破：放宽 vol_dry_ratio / breakout_min_pct（适合温和启动）
    - 放宽VR确认：放宽 vr_min / confirm_days（适合低量启动）
    - 放宽均线粘合：放宽 ma_squeeze_pct / ma_squeeze_days（适合均线已粘合未突破）
    - 综合放宽：复用一轮 auto-tune 放宽，min_score=50（最宽松）
    """
    sets: list[tuple[str, LaunchSignalParams, float]] = []

    # 组 0：基准（严格）
    sets.append(("基准", base, 60.0))

    # 组 1：放宽缩量 + 突破幅度（适合温和启动，放宽量能与突破下限）
    p1 = LaunchSignalParams(
        vol_dry_ratio=min(0.7, base.vol_dry_ratio * 1.3),
        min_dry_days=max(4, base.min_dry_days - 2),
        ma_squeeze_pct=base.ma_squeeze_pct,
        ma_squeeze_days=base.ma_squeeze_days,
        vol_squeeze_percentile=min(0.7, base.vol_squeeze_percentile + 0.1),
        lookback_days=min(25, base.lookback_days + 5),
        breakout_min_pct=max(0.005, base.breakout_min_pct * 0.75),
        breakout_max_pct=min(0.15, base.breakout_max_pct + 0.02),
        vr_min=base.vr_min,
        vr_max=base.vr_max,
        turnover_min=base.turnover_min,
        turnover_max=base.turnover_max,
        turnover_warn=min(0.50, base.turnover_warn * 1.1),
        confirm_days=base.confirm_days,
        pullback_vol_ratio_max=min(0.95, base.pullback_vol_ratio_max + 0.1),
        vol_center_shift_min=max(0.7, base.vol_center_shift_min - 0.1),
        weight_buildup=base.weight_buildup,
        weight_ignition=base.weight_ignition,
        weight_confirm=base.weight_confirm,
        weight_env=base.weight_env,
    )
    sets.append(("放宽缩量突破", p1, 55.0))

    # 组 2：放宽 VR + 确认天数（适合低量启动，降低 VR 下限并缩短确认期）
    p2 = LaunchSignalParams(
        vol_dry_ratio=base.vol_dry_ratio,
        min_dry_days=base.min_dry_days,
        ma_squeeze_pct=base.ma_squeeze_pct,
        ma_squeeze_days=base.ma_squeeze_days,
        vol_squeeze_percentile=base.vol_squeeze_percentile,
        lookback_days=base.lookback_days,
        breakout_min_pct=base.breakout_min_pct,
        breakout_max_pct=base.breakout_max_pct,
        vr_min=max(1.0, base.vr_min - 0.5),
        vr_max=min(8.0, base.vr_max + 1.0),
        turnover_min=base.turnover_min,
        turnover_max=base.turnover_max,
        turnover_warn=base.turnover_warn,
        confirm_days=max(1, base.confirm_days - 1),
        pullback_vol_ratio_max=base.pullback_vol_ratio_max,
        vol_center_shift_min=base.vol_center_shift_min,
        weight_buildup=base.weight_buildup,
        weight_ignition=base.weight_ignition,
        weight_confirm=base.weight_confirm,
        weight_env=base.weight_env,
    )
    sets.append(("放宽VR确认", p2, 55.0))

    # 组 3：放宽均线粘合（适合均线已粘合但尚未突破的股票）
    p3 = LaunchSignalParams(
        vol_dry_ratio=base.vol_dry_ratio,
        min_dry_days=max(4, base.min_dry_days - 2),
        ma_squeeze_pct=min(0.10, base.ma_squeeze_pct * 1.3),
        ma_squeeze_days=max(4, base.ma_squeeze_days - 2),
        vol_squeeze_percentile=base.vol_squeeze_percentile,
        lookback_days=min(25, base.lookback_days + 5),
        breakout_min_pct=base.breakout_min_pct,
        breakout_max_pct=base.breakout_max_pct,
        vr_min=base.vr_min,
        vr_max=base.vr_max,
        turnover_min=base.turnover_min,
        turnover_max=base.turnover_max,
        turnover_warn=base.turnover_warn,
        confirm_days=base.confirm_days,
        pullback_vol_ratio_max=base.pullback_vol_ratio_max,
        vol_center_shift_min=base.vol_center_shift_min,
        weight_buildup=base.weight_buildup,
        weight_ignition=base.weight_ignition,
        weight_confirm=base.weight_confirm,
        weight_env=base.weight_env,
    )
    sets.append(("放宽均线粘合", p3, 55.0))

    # 组 4：综合放宽（复用一轮 auto-tune 放宽，min_score=50，最宽松）
    p4 = _tune_params(base, 1)
    sets.append(("综合放宽", p4, 50.0))

    return sets


def _run_scan_once(
    date_str: str,
    universe: list[str],
    meta: dict[str, dict[str, object]],
    db_path: Path,
    params: LaunchSignalParams,
    min_score: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """单次扫描全部候选，返回 (all_results, signals)。

    每只票均用给定参数与 min_score 计算信号，结果按得分降序。
    """
    results: list[dict[str, object]] = []
    signals: list[dict[str, object]] = []

    for i, ticker in enumerate(universe, 1):
        df = _load_kline_from_cache(ticker, db_path)
        if df is None or len(df) < 100:
            continue
        try:
            result = compute_launch_signal(
                ticker=ticker,
                close=df["close"],
                high=df["high"],
                low=df["low"],
                volume=df["volume"],
                params=params,
                min_score=min_score,
            )
        except Exception as exc:  # noqa: BLE001 单只失败不应中断全量扫描
            logger.warning(f"⚠️ {ticker} 信号计算失败：{exc}")
            continue

        entry = _build_launch_signal_entry(ticker, result, meta)
        results.append(entry)
        if result.signal:
            signals.append(entry)

        if i % 100 == 0:
            logger.info(f"   已扫描 {i}/{len(universe)} 只，信号 {len(signals)} 只")

    results.sort(key=lambda e: float(cast("float", e.get("score", 0.0))), reverse=True)
    signals.sort(key=lambda e: float(cast("float", e.get("score", 0.0))), reverse=True)
    return results, signals


def run_scan(
    date_str: str,
    tickers: list[str] | None = None,
    min_score: float = 60.0,
    vr_min: float = 1.5,
    vr_max: float = 3.0,
    push: bool = False,
    per_stock_push: bool = False,
    per_stock_limit: int = 10,
    exclude_etf: bool = True,
    params: LaunchSignalParams | None = None,
    auto_tune: bool = False,
    max_tunes: int = 6,
    tune_mode: str = "none",
    tune_groups: int | None = None,
) -> dict[str, object]:
    """执行启动信号扫描。

    Parameters
    ----------
    date_str : str
        分析日期 YYYY-MM-DD。
    tickers : list[str] | None
        指定股票池（带前缀 ticker），None 时扫描全市场。
    min_score : float
        信号触发最低得分（默认 60）。
    vr_min : float
        VR 最小阈值。
    vr_max : float
        VR 最大阈值。
    push : bool
        是否飞书推送汇总卡片。
    per_stock_push : bool
        是否逐只推送飞书。
    per_stock_limit : int
        逐只推送上限（<=0 表示不限）。
    exclude_etf : bool
        是否剔除 ETF（默认 True，大行情启动信号主要针对 A 股）。
    params : LaunchSignalParams | None
        自定义参数，None 时使用 vr_min/vr_max 构造基准参数。
    auto_tune : bool
        是否启用自动调参（首轮无信号时放宽阈值重跑，直至出现信号或达上限）。
        等价于 tune_mode="auto"，保留用于向后兼容。
    max_tunes : int
        auto 模式下最大放宽轮数（默认 6）。
    tune_mode : str
        调参模式："none"（单轮，默认）、"auto"（首轮无信号则放宽）、“grid”（多组参数网格测试）。
    tune_groups : int | None
        grid 模式下测试的参数组数（None 表示全部预设组）。

    Returns:
        可落盘的结果 dict。
    """
    settings = get_settings()
    results_dir = Path(settings.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(settings.cache_dir) / "pipeline_cache.db"

    # ── 股票池 ──
    all_tickers = get_all_tickers() if tickers is None else list(tickers)

    # 前置过滤：排除北交所/港股/ST；ETF 由参数控制
    meta = load_metadata_index()
    universe = filter_stock_universe(
        all_tickers,
        metadata=meta,
        exclude_st=True,
        exclude_bse=True,
        exclude_hk=True,
        exclude_etf=exclude_etf,
    )
    logger.info(f"📊 启动信号扫描：候选股票池 {len(universe)} 只（日期={date_str}）")

    # ── 基准参数 ──
    base_params = (
        params
        if params is not None
        else LaunchSignalParams(vr_min=vr_min, vr_max=vr_max)
    )

    # ── 统一调参模式 ──
    # auto_tune 保留向后兼容：True 时等价于 tune_mode="auto"
    effective_tune_mode = "auto" if auto_tune else tune_mode
    group_results: list[dict[str, object]] | None = None

    best_results: list[dict[str, object]] = []
    best_signals: list[dict[str, object]] = []
    best_params = base_params
    best_min_score = min_score
    best_iteration = 0

    if effective_tune_mode == "grid":
        # ── grid 模式：多组参数网格测试，每组跑一次全市场，汇总去重 ──
        param_groups = _grid_param_sets(base_params)
        if tune_groups is not None and tune_groups > 0:
            param_groups = param_groups[:tune_groups]
        group_results = []
        all_signal_entries: dict[str, dict[str, object]] = {}  # code6 -> 最高分 entry

        for gname, gparams, gmin_score in param_groups:
            results, signals = _run_scan_once(
                date_str, universe, meta, db_path, gparams, gmin_score
            )
            logger.info(
                f"   网格组 [{gname}]：扫描 {len(results)} 只，触发信号 {len(signals)} 只 "
                f"(min_score={gmin_score:.0f})"
            )
            group_results.append(
                {
                    "group": gname,
                    "min_score": gmin_score,
                    "scanned": len(results),
                    "signal_count": len(signals),
                    "params": {
                        "vr_min": gparams.vr_min,
                        "vr_max": gparams.vr_max,
                        "vol_dry_ratio": gparams.vol_dry_ratio,
                        "min_dry_days": gparams.min_dry_days,
                        "ma_squeeze_pct": gparams.ma_squeeze_pct,
                        "ma_squeeze_days": gparams.ma_squeeze_days,
                        "vol_squeeze_percentile": gparams.vol_squeeze_percentile,
                        "lookback_days": gparams.lookback_days,
                        "breakout_min_pct": gparams.breakout_min_pct,
                        "breakout_max_pct": gparams.breakout_max_pct,
                        "turnover_warn": gparams.turnover_warn,
                        "confirm_days": gparams.confirm_days,
                        "weight_buildup": gparams.weight_buildup,
                        "weight_ignition": gparams.weight_ignition,
                        "weight_confirm": gparams.weight_confirm,
                        "weight_env": gparams.weight_env,
                    },
                }
            )
            # 合并信号，按 ticker 去重，保留最高分
            for e in signals:
                code6 = str(e.get("code", ""))
                cur_score = float(cast("float", e.get("score", 0.0)))
                if code6 not in all_signal_entries or cur_score > float(
                    cast("float", all_signal_entries[code6].get("score", 0.0))
                ):
                    all_signal_entries[code6] = e

        signals = sorted(
            all_signal_entries.values(),
            key=lambda e: float(cast("float", e.get("score", 0.0))),
            reverse=True,
        )
        results = signals  # grid 模式下 all_results 即去重后的信号列表
        best_params = base_params
        best_min_score = min_score
        best_iteration = 0

    else:
        # ── none / auto 模式：单轮或首轮无信号时放宽重跑 ──
        iterations = range(max_tunes + 1) if effective_tune_mode == "auto" else [0]

        for iteration in iterations:
            cur_params = (
                base_params if iteration == 0 else _tune_params(base_params, iteration)
            )
            cur_min_score = (
                min_score if iteration == 0 else max(20.0, min_score - 10.0 * iteration)
            )
            results, signals = _run_scan_once(
                date_str, universe, meta, db_path, cur_params, cur_min_score
            )
            logger.info(
                f"   第 {iteration} 轮：扫描 {len(results)} 只，触发信号 {len(signals)} 只 "
                f"(min_score={cur_min_score:.0f})"
            )
            best_results, best_signals = results, signals
            best_params, best_min_score = cur_params, cur_min_score
            best_iteration = iteration
            if signals:
                break

        results, signals = best_results, best_signals

    # 按得分降序（_run_scan_once 已排序，此处保留显式排序以防万一）
    results.sort(key=lambda e: float(cast("float", e.get("score", 0.0))), reverse=True)
    signals.sort(key=lambda e: float(cast("float", e.get("score", 0.0))), reverse=True)

    dc = date_str.replace("-", "")
    out_file = results_dir / f"launch_signal_{dc}.json"
    payload: dict[str, object] = {
        "date": date_str,
        "total_scanned": len(results),
        "signal_count": len(signals),
        "min_score": best_min_score,
        "auto_tune": auto_tune,
        "tune_mode": effective_tune_mode,
        "tune_groups": tune_groups,
        "tune_iteration": best_iteration,
        "group_results": group_results,
        "params": {
            "vr_min": best_params.vr_min,
            "vr_max": best_params.vr_max,
            "vol_dry_ratio": best_params.vol_dry_ratio,
            "min_dry_days": best_params.min_dry_days,
            "ma_squeeze_pct": best_params.ma_squeeze_pct,
            "ma_squeeze_days": best_params.ma_squeeze_days,
            "vol_squeeze_percentile": best_params.vol_squeeze_percentile,
            "lookback_days": best_params.lookback_days,
            "breakout_min_pct": best_params.breakout_min_pct,
            "breakout_max_pct": best_params.breakout_max_pct,
            "turnover_warn": best_params.turnover_warn,
            "confirm_days": best_params.confirm_days,
            "weight_buildup": best_params.weight_buildup,
            "weight_ignition": best_params.weight_ignition,
            "weight_confirm": best_params.weight_confirm,
            "weight_env": best_params.weight_env,
        },
        "signals": signals,
        "all_results": results,
    }
    out_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        f"💾 启动信号结果已保存: {out_file} "
        f"（扫描 {len(results)} 只，触发信号 {len(signals)} 只，调参第 {best_iteration} 轮）"
    )

    # ── 飞书推送 ──
    if push:
        _push_feishu(
            date_str=date_str,
            signals=signals,
            total=len(results),
            per_stock_push=per_stock_push,
            per_stock_limit=per_stock_limit,
            group_results=group_results,
        )

    return payload


def _push_feishu(
    *,
    date_str: str,
    signals: list[dict[str, object]],
    total: int,
    per_stock_push: bool,
    per_stock_limit: int,
    group_results: list[dict[str, object]] | None = None,
) -> None:
    """通过飞书推送启动信号汇总。

    group_results 为非 None 时（grid 模式），会在汇总中列出各参数组的命中数。
    """
    title = f"📈 大行情启动信号扫描 — {date_str}"
    lines = [
        f"扫描范围：{total} 只（已剔除 ST/北交所/港股/ETF）",
        f"触发信号：{len(signals)} 只",
    ]
    if group_results:
        lines.append("各参数组命中数：")
        for g in group_results:
            lines.append(
                f"  [{g['group']}] {g['signal_count']} 只 "
                f"(min_score={g['min_score']:.0f})"
            )
    lines.append("")
    if signals:
        lines.append("🔥 信号股票（按得分降序，前 10 只）：")
        for e in signals[:10]:
            reasons = cast("list[object]", e.get("reasons", []))
            lines.append(
                f"  • {e.get('code', '')} {e.get('name', '')} — "
                f"得分 {e.get('score', 0)}：{', '.join(str(r) for r in reasons[:3])}"
            )
    else:
        lines.append("今日无触发信号股票。")

    content = "\n".join(lines)
    try:
        push_feishu(mode="text", content=content, title=title)
        logger.info("✅ 飞书汇总卡片已推送")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ 飞书推送失败：{exc}")

    if per_stock_push:
        limit = per_stock_limit if per_stock_limit and per_stock_limit > 0 else len(signals)
        for e in signals[:limit]:
            code6 = str(e.get("code", ""))
            name = str(e.get("name", ""))
            score = float(cast("float", e.get("score", 0.0)))
            kill_raw = e.get("kill_switch", [])
            # dict[str, object].get 在类型层返回 object，运行时实际为 list[str]
            kill: list[str] = cast("list[str]", kill_raw)  # type: ignore[assignment]  # 运行时值实际为 list[str]
            reasons_text = "；".join(
                str(r) for r in cast("list[object]", e.get("reasons", []))[:4]
            )
            ks_text = f" | ⚠️ 证伪：{', '.join(kill)}" if kill else ""
            per_content = (
                f"📊 {code6} {name}\n"
                f"启动信号得分：{score:.1f}\n"
                f"理由：{reasons_text}\n"
                f"日期：{date_str}{ks_text}"
            )
            try:
                push_feishu(mode="text", content=per_content, title=f"启动信号 {code6}")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"❌ {code6} 逐只推送失败：{exc}")


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="大行情启动信号扫描器")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="指定股票池（逗号分隔 6 位裸代码或带前缀 ticker），缺省扫描全市场",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=60.0,
        help="信号触发最低得分（默认 60）",
    )
    parser.add_argument(
        "--vr-min",
        type=float,
        default=1.5,
        help="VR 最小阈值（默认 1.5）",
    )
    parser.add_argument(
        "--vr-max",
        type=float,
        default=3.0,
        help="VR 最大阈值（默认 3.0，超过视为极端放量扣分）",
    )
    parser.add_argument(
        "--push",
        type=int,
        default=0,
        help="是否飞书推送汇总卡片（1=是，0=否，默认 0；测试阶段请用 --no-push）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="显式禁用飞书推送（等价于 --push 0）",
    )
    parser.add_argument(
        "--per-stock-push",
        type=int,
        default=0,
        help="是否逐只推送飞书（1=是，0=否，默认 0）",
    )
    parser.add_argument(
        "--per-stock-limit",
        type=int,
        default=10,
        help="逐只推送上限（默认 10；<=0 表示不限）",
    )
    parser.add_argument(
        "--include-etf",
        action="store_true",
        help="将 ETF 也纳入扫描（默认剔除 ETF，仅 A 股）",
    )
    parser.add_argument(
        "--auto-tune",
        action="store_true",
        help="启用自动调参：首轮无信号时放宽阈值重跑，直至出现信号或达上限（等价于 --tune-mode auto）",
    )
    parser.add_argument(
        "--tune-mode",
        choices=["none", "auto", "grid"],
        default="none",
        help="调参模式：none=单轮（默认），auto=首轮无信号则放宽，grid=多组参数网格测试，挖掘更多可能启动股票",
    )
    parser.add_argument(
        "--tune-groups",
        type=int,
        default=None,
        help="grid 模式下测试的参数组数（默认全部预设组，共 5 组）",
    )
    args = parser.parse_args()

    _load_env()

    push_enabled = args.push == 1 and not args.no_push
    per_stock_enabled = args.per_stock_push == 1

    tickers: list[str] | None = None
    if args.tickers:
        raw = [t.strip() for t in args.tickers.split(",") if t.strip()]
        norm: list[str] = []
        for t in raw:
            if len(t) == 6 and t.isdigit():
                norm.append(t)
            else:
                norm.append(t)
        tickers = norm

    run_scan(
        date_str=args.date,
        tickers=tickers,
        min_score=args.min_score,
        vr_min=args.vr_min,
        vr_max=args.vr_max,
        push=push_enabled,
        per_stock_push=per_stock_enabled,
        per_stock_limit=args.per_stock_limit,
        exclude_etf=not args.include_etf,
        auto_tune=args.auto_tune,
        tune_mode=args.tune_mode,
        tune_groups=args.tune_groups,
    )


def _load_env() -> None:
    """加载 .env 环境变量（轻量版，复用 cli_commands.core._load_env）。"""
    from trade_krono_cli.cli_commands.core import _load_env as _le  # noqa: PLC0415

    _le()


if __name__ == "__main__":
    main()
