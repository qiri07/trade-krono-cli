"""
预测分布量化模块。

封装 Kronos 预测结果的不确定性计算逻辑，与 runner 解耦，便于测试和维护。

与旧版 PredictionUncertainty 的区别：
  · 旧版只存 5 个标量摘要（expected_return / direction / direction_score / volatility / confidence_score）
  · 新版存完整分位数（p10/p25/p50/p75/p90），支持从多样本路径矩阵直接计算
  · 单样本时百分位退化为 {p10:close, p25:close, p50:final, p75:close, p90:close}
  · 保留原有摘要字段向后兼容

数据结构：
  PredictionDistribution
    expected_return       预期收益率（%）
    direction             UP / DOWN / FLAT（阈值 ±1%）
    direction_score       方向强度评分 0-1，sigmoid(|change_pct| / (10*std + eps))
    confidence_score      综合不确定性评分 0-100
    volatility            预测路径的标准差
    path_dispersion       归一化路径分散度（多样本才有意义）
    sample_count_used     实际使用的样本数
    # 分位数（多样本时填充，单样本时退化为 close）
    p10                   10% 分位收盘价
    p25                   25% 分位收盘价
    p50                   中位数收盘价
    p75                   75% 分位收盘价
    p90                   90% 分位收盘价
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


# ═══════════════════════════════════════════════════════
#  PredictionDistribution — 核心数据类
# ═══════════════════════════════════════════════════════

@dataclass
class PredictionDistribution:
    """
    Kronos 预测结果的概率分布描述。

    字段语义：
      expected_return    预期收益率（%），= (final_close - last_close) / last_close * 100
      direction          UP / DOWN / FLAT（阈值 ±1%）
      direction_score    方向强度评分 0-1，基于 |change_pct| 与波动率的比率
                         = sigmoid(|change_pct| / (10 * std + 1e-8))
                         名称用 score 而非 confidence，避免与 confidence_score 混淆
      volatility         预测路径的标准差（绝对价格波动）
      path_dispersion    归一化路径分散度；多样本时为 std/|mean|，单样本时为 None
      confidence_score   综合不确定性评分 0-100
                         多样本：direction_score*50 + max(0, 50-dispersion*200)
                         单样本：direction_score * 100
      sample_count_used  实际使用的样本数
      # 分位数字段（多样本时填充，单样本时退化为最终价）
      p10 / p25 / p50 / p75 / p90 : 各分位收盘价
    """
    # ── 摘要字段 ───────────────────────────────────────────────────────────
    expected_return: Optional[float] = None
    direction: Optional[str] = None
    direction_score: Optional[float] = None
    volatility: Optional[float] = None
    path_dispersion: Optional[float] = None
    confidence_score: Optional[float] = None
    sample_count_used: int = 1

    # ── 分位数字段（多样本时填充）─────────────────────────────────────────
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PredictionDistribution":
        """从 dict 反序列化。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════
#  计算函数
# ═══════════════════════════════════════════════════════


def _compute_percentiles(
    stacked: np.ndarray,
    n_samples: int,
    last_close: float,
) -> tuple[float, float, float, float, float]:
    """
    从路径矩阵计算最终价的百分位。

    多样本：从各样本的最终价计算 p10/p25/p50/p75/p90。
    单样本：退化为 [last_close, last_close, final, last_close, last_close]。

    Returns
    -------
    (p10, p25, p50, p75, p90)
    """
    if n_samples <= 1 or len(stacked) <= 1:
        final = float(stacked[-1, -1])
        return final, final, final, final, final

    # 各样本最终价
    final_prices = stacked[:, -1].astype(float)
    p10 = float(np.percentile(final_prices, 10))
    p25 = float(np.percentile(final_prices, 25))
    p50 = float(np.percentile(final_prices, 50))
    p75 = float(np.percentile(final_prices, 75))
    p90 = float(np.percentile(final_prices, 90))
    return p10, p25, p50, p75, p90


def compute_single_sample(
    closes: np.ndarray,
    last_close: float,
) -> tuple[float, str, float, float, Optional[float], float, tuple]:
    """
    对单条预测路径计算分布指标。

    Parameters
    ----------
    closes : np.ndarray
        预测收盘价序列
    last_close : float
        历史最后一个收盘价

    Returns
    -------
    (change_pct, direction, vol, path_dispersion, direction_score, confidence_score, percentiles)
      percentiles = (p10, p25, p50, p75, p90) 单样本时退化为 (final, final, final, final, final)
    """
    closes_f = closes.astype(float)
    final_close = float(closes_f[-1])
    change_pct = (final_close - last_close) / last_close * 100.0
    direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
    vol = float(np.std(closes_f))

    # direction_score: sigmoid(|change_pct| / (10*std + eps))
    denom = 10.0 * vol + 1e-8
    raw_ratio = abs(change_pct) / denom
    direction_score = float(1.0 / (1.0 + np.exp(-raw_ratio)))

    # 单样本无跨路径方差
    path_dispersion = None

    # confidence_score = direction_score * 100（单样本规则）
    confidence_score = round(min(100.0, max(0.0, direction_score * 100.0)), 2)

    # 单样本百分位退化为最终价
    percentiles = (final_close, final_close, final_close, final_close, final_close)

    return (
        round(change_pct, 3),
        direction,
        round(vol, 4),
        path_dispersion,
        round(direction_score, 4),
        confidence_score,
        percentiles,
    )


def compute_multi_sample(
    avg_close: np.ndarray,
    stacked: np.ndarray,
    last_close: float,
) -> tuple[float, str, float, float, float, float, tuple]:
    """
    对多样本预测（N 条路径取均值）计算分布指标。

    Parameters
    ----------
    avg_close : np.ndarray
        各样本路径收盘价均值（pred_len 长度）
    stacked : np.ndarray
        原始 N×pred_len 路径矩阵，用于计算样本间方差和百分位
    last_close : float
        历史最后一个收盘价

    Returns
    -------
    (change_pct, direction, vol, path_dispersion, direction_score, confidence_score, percentiles)
    """
    final_close = float(avg_close[-1])
    change_pct = (final_close - last_close) / last_close * 100.0
    direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
    vol = float(np.std(avg_close))

    # 跨样本最终价的变异系数 → 真正的路径间不确定性
    n_samples = len(stacked)
    sample_std = float(np.std(stacked[:, -1]))
    sample_cv = sample_std / abs(final_close) if abs(final_close) > 1e-8 else 0.0

    raw_ratio = abs(change_pct) / (10.0 * sample_std + 1e-8)
    direction_score = float(1.0 / (1.0 + np.exp(-raw_ratio)))

    conf_score = direction_score * 50.0 + max(0.0, 50.0 - sample_cv * 200.0)
    conf_score = round(min(100.0, max(0.0, conf_score)), 2)

    path_dispersion = round(sample_cv, 6)

    # 计算百分位
    percentiles = _compute_percentiles(stacked, n_samples, last_close)

    return (
        round(change_pct, 3),
        direction,
        round(vol, 4),
        path_dispersion,
        round(direction_score, 4),
        conf_score,
        percentiles,
    )


def build_distribution(
    change_pct: float,
    direction: str,
    vol: float,
    path_dispersion: Optional[float],
    direction_score: float,
    confidence_score: float,
    sample_count: int,
    percentiles: tuple[float, float, float, float, float],
) -> PredictionDistribution:
    """
    根据已计算的分布指标构建 PredictionDistribution 对象。

    Parameters
    ----------
    change_pct       : 预期收益率（%）
    direction        : UP / DOWN / FLAT
    vol              : 预测路径标准差
    path_dispersion  : 归一化路径分散度（单样本为 None）
    direction_score  : 方向强度评分 0-1
    confidence_score : 综合不确定性评分 0-100
    sample_count     : 实际使用的样本数
    percentiles      : (p10, p25, p50, p75, p90) 最终价分位数
    """
    p10, p25, p50, p75, p90 = percentiles
    return PredictionDistribution(
        expected_return=change_pct,
        direction=direction,
        direction_score=direction_score,
        volatility=vol,
        path_dispersion=path_dispersion,
        confidence_score=confidence_score,
        sample_count_used=sample_count,
        p10=p10,
        p25=p25,
        p50=p50,
        p75=p75,
        p90=p90,
    )


def build_result_dict(
    closes: np.ndarray,
    last_close: float,
    stacked: Optional[np.ndarray] = None,
    sample_count: int = 1,
) -> dict:
    """
    从预测路径构建完整结果 dict（含百分位和 distribution）。

    Parameters
    ----------
    closes     : 预测收盘价序列（单样本路径 或 均值路径）
    last_close : 历史最后一个收盘价
    stacked    : 原始路径矩阵 N×pred_len（多样本时传入，用于计算百分位）
    sample_count : 样本数
    """
    closes_f = closes.astype(float)
    final_close = float(closes_f[-1])
    mean_close = float(np.mean(closes_f))

    q_low = float(np.percentile(closes_f, 25)) if len(closes_f) > 1 else mean_close
    q_high = float(np.percentile(closes_f, 75)) if len(closes_f) > 1 else mean_close

    # 计算分布指标
    if stacked is not None and len(stacked) > 1:
        (
            change_pct, direction, vol, path_disp,
            direction_score, confidence_score, percentiles,
        ) = compute_multi_sample(closes_f, stacked, last_close)
    else:
        (
            change_pct, direction, vol, path_disp,
            direction_score, confidence_score, percentiles,
        ) = compute_single_sample(closes_f, last_close)

    distribution = PredictionDistribution(
        expected_return=change_pct,
        direction=direction,
        direction_score=direction_score,
        volatility=vol,
        path_dispersion=path_disp,
        confidence_score=confidence_score,
        sample_count_used=sample_count,
        p10=percentiles[0],
        p25=percentiles[1],
        p50=percentiles[2],
        p75=percentiles[3],
        p90=percentiles[4],
    )

    return {
        "predicted_close_mean": round(mean_close, 4),
        "predicted_close_final": round(final_close, 4),
        "expected_change_pct": change_pct,
        "direction": direction,
        "volatility_proxy": vol,
        "confidence_band": {
            "low": round(q_low, 4),
            "high": round(q_high, 4),
        },
        "prediction_distribution": distribution.to_dict(),
        # 向后兼容：旧测试仍查找 prediction_uncertainty 键
        "prediction_uncertainty": distribution.to_dict(),
    }
