"""Signal IC 评估模块。

计算预测信号与未来收益之间的信息系数（Information Coefficient）：
  - Spearman 秩相关 IC（Rank IC）—— 核心指标，衡量信号排序能力
  - Pearson 相关 IC —— 衡量线性相关性
  - ICIR = IC / std(IC) —— 信息比率，衡量信号的稳定性
  - IC > 0.03 + ICIR > 0.5 为有效的预测信号

分 horizon 计算，支持 TA signal、Kronos score、composite score 三类信号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics

# ═══════════════════════════════════════════════════════
# IC 统计量
# ═══════════════════════════════════════════════════════


@dataclass
class ICResult:
    """单次 IC 计算结果。"""

    # Pearson IC
    ic_mean: float = 0.0
    ic_std: float = 0.0
    icir: float = 0.0
    ic_abs_mean: float = 0.0
    # Spearman Rank IC
    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    rank_icir: float = 0.0
    # 通过率
    ic_positive_pct: float = 0.0
    # 样本数
    n_groups: int = 0
    n_records: int = 0


def _safe_pearson(x: np.ndarray, y: np.ndarray) -> float:
    """安全计算 Pearson 相关系数，处理 NaN / 零方差。"""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    mx, my = np.mean(x), np.mean(y)
    dx, dy = x - mx, y - my
    denom = np.sqrt(np.sum(dx**2) * np.sum(dy**2))
    if denom < 1e-15:
        return 0.0
    return float(np.sum(dx * dy) / denom)


def _safe_spearman(x: np.ndarray, y: np.ndarray) -> float:
    """安全计算 Spearman 秩相关系数。"""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    rx = _rank_transform(x)
    ry = _rank_transform(y)
    return _safe_pearson(rx, ry)


def _rank_transform(arr: np.ndarray) -> np.ndarray:
    """将数组转换为秩（1-based，平均秩处理并列）。"""
    n = len(arr)
    if n == 0:
        return np.array([])
    order = np.argsort(arr)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # 沿排序后数组找并列组，取平均秩
    sorted_arr = arr[order]
    # tie[i]=True 表示排序位置 i 与相邻位置有并列
    tie = np.concatenate(
        (
            [sorted_arr[0] == sorted_arr[1]] if n > 1 else [False],
            (sorted_arr[1:-1] == sorted_arr[:-2]) | (sorted_arr[1:-1] == sorted_arr[2:]),
            [sorted_arr[-1] == sorted_arr[-2]] if n > 1 else [False],
        ),
    )
    i = 0
    while i < n:
        if tie[i]:
            # 找组的起始位置
            start = i
            while start > 0 and tie[start - 1]:
                start -= 1
            # 找组的结束位置
            end = i
            while end < n - 1 and tie[end + 1]:
                end += 1
            avg = np.mean(ranks[order[start : end + 1]])
            ranks[order[start : end + 1]] = avg
            i = end + 1
        else:
            i += 1
    return ranks


def _compute_ic_for_signal(
    predictions: np.ndarray,
    actuals: np.ndarray,
) -> ICResult:
    """对单组 (prediction, actual) 计算 IC 统计量。

    这是"截面" IC：在同一 eval_date 内，对所有股票按预测值排序，
    看与实际收益的相关性。

    Parameters
    ----------
    predictions : np.ndarray
        预测值（如 composite_score / kronos_change / ta_confidence）
    actuals : np.ndarray
        实际收益（actual_return_pct）

    Returns
    -------
    ICResult

    """
    mask = ~(np.isnan(predictions) | np.isnan(actuals))
    pred = predictions[mask]
    act = actuals[mask]
    n = len(pred)
    if n < 10:
        return ICResult(n_groups=1, n_records=n)

    rank_ic = _safe_spearman(pred, act)
    pearson_ic = _safe_pearson(pred, act)

    # ICIR = mean(IC) / std(IC)，单组时 ICIR = IC / 0 → 用 IC 本身近似
    # 多组时由 caller 聚合
    return ICResult(
        ic_mean=round(pearson_ic, 4),
        ic_std=0.0,  # 单组，std 未定义
        icir=round(pearson_ic / max(abs(pearson_ic), 1e-9), 4) if n > 10 else 0.0,
        ic_abs_mean=round(abs(pearson_ic), 4),
        rank_ic_mean=round(rank_ic, 4),
        rank_ic_std=0.0,
        rank_icir=round(rank_ic / max(abs(rank_ic), 1e-9), 4) if n > 10 else 0.0,
        ic_positive_pct=100.0 if rank_ic > 0 else 0.0,
        n_groups=1,
        n_records=n,
    )


def compute_ic_aggregated(results: list[ICResult]) -> ICResult:
    """聚合多个 ICResult（通常来自多个 eval_date 的截面 IC）。

    Returns
    -------
    聚合后的 ICResult，含跨日 IC 均值/标准差/ICIR。

    """
    if not results:
        return ICResult()

    ic_means = [r.ic_mean for r in results if r.n_groups > 0]
    ric_means = [r.rank_ic_mean for r in results if r.n_groups > 0]

    ic_std = (
        float(np.std(ic_means, ddof=1))
        if len(ic_means) > 1
        else (abs(ic_means[0]) if ic_means else 0.0)
    )
    ric_std = (
        float(np.std(ric_means, ddof=1))
        if len(ric_means) > 1
        else (abs(ric_means[0]) if ric_means else 0.0)
    )

    ic_mean_val = float(np.mean(ic_means)) if ic_means else 0.0
    ric_mean_val = float(np.mean(ric_means)) if ric_means else 0.0

    total_records = sum(r.n_records for r in results)

    return ICResult(
        ic_mean=round(ic_mean_val, 4),
        ic_std=round(ic_std, 4),
        icir=round(ic_mean_val / ic_std, 4) if ic_std > 1e-9 else 0.0,
        ic_abs_mean=round(float(np.mean([abs(r.ic_mean) for r in results])), 4) if results else 0.0,
        rank_ic_mean=round(ric_mean_val, 4),
        rank_ic_std=round(ric_std, 4),
        rank_icir=round(ric_mean_val / ric_std, 4) if ric_std > 1e-9 else 0.0,
        ic_positive_pct=round(sum(1 for r in ric_means if r > 0) / max(len(ric_means), 1) * 100, 1),
        n_groups=len(results),
        n_records=total_records,
    )


# ═══════════════════════════════════════════════════════
# 主入口：按 horizon 计算各类信号的 IC
# ═══════════════════════════════════════════════════════


def compute_ic_metrics(
    h_records: list[EvalRecord],
    metrics: HorizonMetrics,
) -> int:
    """计算指定 horizon 下各信号的 IC / ICIR。

    信号类型：
      1. composite_score → actual_return（综合评分 IC）
      2. kronos_change   → actual_return（Kronos 预测收益 IC）
      3. ta_confidence   → actual_return（TA 置信度 IC）

    计算方法：按 eval_date 分组，每组内做截面 Spearman 秩相关，
    然后聚合跨日 IC。

    Parameters
    ----------
    h_records : list[EvalRecord]
        同一 horizon 下的所有评估记录
    metrics : HorizonMetrics
        输出目标，写入 ic_mean / rank_ic_mean / icir 等字段

    Returns
    -------
    纳入 IC 计算的记录总数

    """
    if not h_records:
        return 0

    # 按 eval_date 分组（每日截面）
    date_groups: dict[str, list[EvalRecord]] = {}
    for r in h_records:
        date_groups.setdefault(r.eval_date, []).append(r)

    if len(date_groups) < 3:
        logger.debug(f"  IC 计算跳过：仅 {len(date_groups)} 个 eval_date（需 ≥ 3）")
        return 0

    # 对每组计算截面 IC
    composite_results: list[ICResult] = []
    kronos_results: list[ICResult] = []
    ta_results: list[ICResult] = []

    for group in date_groups.values():
        n = len(group)
        if n < 10:
            continue

        # composite_score IC
        comp_preds = np.array(
            [r.composite_score if r.composite_score is not None else 0.0 for r in group],
            dtype=float,
        )
        actuals = np.array([r.actual_return_pct for r in group], dtype=float)
        composite_results.append(_compute_ic_for_signal(comp_preds, actuals))

        # kronos_change IC（仅有预测值的记录）
        kronos_mask = [r.pred_return_pct is not None for r in group]
        if sum(kronos_mask) >= 10:
            kp = np.array(
                [r.pred_return_pct or 0.0 for r, m in zip(group, kronos_mask, strict=False) if m],
                dtype=float,
            )
            ka = np.array(
                [r.actual_return_pct for r, m in zip(group, kronos_mask, strict=False) if m],
                dtype=float,
            )
            kronos_results.append(_compute_ic_for_signal(kp, ka))

        # ta_confidence IC（仅 BUY 信号）
        ta_buy = [r for r in group if r.ta_signal == "BUY"]
        if len(ta_buy) >= 10:
            ta_conf = np.array(
                [r.composite_score if r.composite_score is not None else 50.0 for r in ta_buy],
                dtype=float,
            )
            ta_act = np.array([r.actual_return_pct for r in ta_buy], dtype=float)
            ta_results.append(_compute_ic_for_signal(ta_conf, ta_act))

    # 聚合
    if composite_results:
        comp_agg = compute_ic_aggregated(composite_results)
        metrics.ic_composite_mean = comp_agg.ic_mean
        metrics.ic_composite_std = comp_agg.ic_std
        metrics.ic_composite_ir = comp_agg.icir
        metrics.rank_ic_composite_mean = comp_agg.rank_ic_mean
        metrics.rank_ic_composite_std = comp_agg.rank_ic_std
        metrics.rank_ic_composite_ir = comp_agg.rank_icir
        metrics.ic_positive_pct = comp_agg.ic_positive_pct

    if kronos_results:
        kr_agg = compute_ic_aggregated(kronos_results)
        metrics.ic_kronos_mean = kr_agg.ic_mean
        metrics.ic_kronos_std = kr_agg.ic_std
        metrics.ic_kronos_ir = kr_agg.icir
        metrics.rank_ic_kronos_mean = kr_agg.rank_ic_mean
        metrics.rank_ic_kronos_std = kr_agg.rank_ic_std
        metrics.rank_ic_kronos_ir = kr_agg.rank_icir

    if ta_results:
        ta_agg = compute_ic_aggregated(ta_results)
        metrics.ic_ta_mean = ta_agg.ic_mean
        metrics.ic_ta_std = ta_agg.ic_std
        metrics.ic_ta_ir = ta_agg.icir
        metrics.rank_ic_ta_mean = ta_agg.rank_ic_mean
        metrics.rank_ic_ta_std = ta_agg.rank_ic_std
        metrics.rank_ic_ta_ir = ta_agg.rank_icir

    return sum(len(g) for g in date_groups.values())


# ═══════════════════════════════════════════════════════
# HorizonMetrics 扩展字段（在 eval_data.py 中定义，此处仅导出类型）
# ═══════════════════════════════════════════════════════

# 以下字段在 eval_data.py 的 HorizonMetrics 中定义，此处为文档引用：
#   ic_composite_mean/std/ir       — 综合评分 IC
#   rank_ic_composite_mean/std/ir  — 综合评分 Rank IC
#   ic_kronos_mean/std/ir          — Kronos 预测收益 IC
#   rank_ic_kronos_mean/std/ir     — Kronos Rank IC
#   ic_ta_mean/std/ir              — TA 置信度 IC
#   rank_ic_ta_mean/std/ir         — TA Rank IC
#   ic_positive_pct                — IC 为正的比例（%）
