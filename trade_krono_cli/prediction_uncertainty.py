"""
预测不确定性量化模块。

封装 Kronos 预测结果的不确定性计算逻辑，与 runner 解耦，便于测试和维护。

计算指标：
  expected_return       预期收益率（%）
  direction             UP / DOWN / FLAT（阈值 ±1%）
  direction_confidence  方向置信度 0-1，sigmoid(|change_pct| / (10*std + eps))
  volatility            预测路径的标准差
  path_dispersion       归一化路径分散度（多样本才有意义）
  confidence_score      综合不确定性评分 0-100
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


@dataclass
class PredictionUncertainty:
    """
    预测不确定性量化结果。

    字段语义：
      expected_return       预期收益率（%），= (final_close - last_close) / last_close * 100
      direction             UP / DOWN / FLAT（阈值 ±1%）
      direction_confidence  方向置信度 0-1，基于 |change_pct| 与波动率的比率
                            = sigmoid(|change_pct| / (10 * std + 1e-8))
      volatility            预测路径的标准差（绝对价格波动）
      path_dispersion       归一化路径分散度；多样本时为 std/|mean|，单样本时为 None
      confidence_score      综合不确定性评分 0-100
                            多样本：direction_confidence*50 + max(0, 50-dispersion*200)
                            单样本：direction_confidence * 100
      sample_count_used     实际使用的样本数
    """
    expected_return: Optional[float] = None
    direction: Optional[str] = None
    direction_confidence: Optional[float] = None
    volatility: Optional[float] = None
    path_dispersion: Optional[float] = None
    confidence_score: Optional[float] = None
    sample_count_used: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PredictionUncertainty":
        """从 dict 反序列化。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def compute_single_sample(
    closes: np.ndarray,
    last_close: float,
) -> tuple[float, str, float, float, Optional[float], float]:
    """
    对单条预测路径计算不确定性指标。

    Parameters
    ----------
    closes : np.ndarray
        预测收盘价序列
    last_close : float
        历史最后一个收盘价

    Returns
    -------
    (change_pct, direction, vol, path_dispersion, direction_confidence, confidence_score)
      path_dispersion 为 None（单样本无跨路径方差意义）
    """
    closes_f = closes.astype(float)
    final_close = float(closes_f[-1])
    mean_close = float(np.mean(closes_f))
    change_pct = (final_close - last_close) / last_close * 100.0
    direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
    vol = float(np.std(closes_f))

    # direction_confidence: sigmoid(|change_pct| / (10*std + eps))
    denom = 10.0 * vol + 1e-8
    raw_ratio = abs(change_pct) / denom
    direction_confidence = float(1.0 / (1.0 + np.exp(-raw_ratio)))

    # 单样本无跨路径方差
    path_dispersion = None

    # confidence_score = direction_confidence * 100（单样本规则）
    confidence_score = round(min(100.0, max(0.0, direction_confidence * 100.0)), 2)

    return (
        round(change_pct, 3),
        direction,
        round(vol, 4),
        path_dispersion,
        round(direction_confidence, 4),
        confidence_score,
    )


def compute_multi_sample(
    avg_close: np.ndarray,
    stacked: np.ndarray,
    last_close: float,
) -> tuple[float, str, float, float, float, float]:
    """
    对多样本预测（N 条路径取均值）计算不确定性指标。

    Parameters
    ----------
    avg_close : np.ndarray
        各样本路径收盘价均值（pred_len 长度）
    stacked : np.ndarray
        原始 N×pred_len 路径矩阵，用于计算样本间方差
    last_close : float
        历史最后一个收盘价

    Returns
    -------
    (change_pct, direction, vol, path_dispersion, direction_confidence, confidence_score)
      path_dispersion = std(stacked[:, -1]) / |final_close|
    """
    final_close = float(avg_close[-1])
    mean_close = float(np.mean(avg_close))
    change_pct = (final_close - last_close) / last_close * 100.0
    direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
    vol = float(np.std(avg_close))

    # 跨样本最终价的变异系数 → 真正的路径间不确定性
    sample_std = float(np.std(stacked[:, -1]))
    sample_cv = sample_std / abs(final_close) if abs(final_close) > 1e-8 else 0.0

    raw_ratio = abs(change_pct) / (10.0 * sample_std + 1e-8)
    direction_confidence = float(1.0 / (1.0 + np.exp(-raw_ratio)))

    conf_score = direction_confidence * 50.0 + max(0.0, 50.0 - sample_cv * 200.0)
    conf_score = round(min(100.0, max(0.0, conf_score)), 2)

    return (
        round(change_pct, 3),
        direction,
        round(vol, 4),
        round(sample_cv, 6),
        round(direction_confidence, 4),
        conf_score,
    )


def build_uncertainty(
    change_pct: float,
    direction: str,
    vol: float,
    path_dispersion: Optional[float],
    direction_confidence: float,
    confidence_score: float,
    sample_count: int,
) -> PredictionUncertainty:
    """
    根据已计算的不确定性指标构建 PredictionUncertainty 对象。

    Parameters
    ----------
    change_pct : float
        预期收益率（%）
    direction : str
        UP / DOWN / FLAT
    vol : float
        预测路径标准差
    path_dispersion : float or None
        归一化路径分散度（单样本为 None）
    direction_confidence : float
        方向置信度 0-1
    confidence_score : float
        综合不确定性评分 0-100
    sample_count : int
        实际使用的样本数
    """
    return PredictionUncertainty(
        expected_return=change_pct,
        direction=direction,
        direction_confidence=direction_confidence,
        volatility=vol,
        path_dispersion=path_dispersion,
        confidence_score=confidence_score,
        sample_count_used=sample_count,
    )


def build_result_dict(
    closes: np.ndarray,
    last_close: float,
    sample_count: int = 1,
) -> dict:
    """
    从单条预测路径构建完整结果 dict（含 confidence_band 和 prediction_uncertainty）。

    Parameters
    ----------
    closes : np.ndarray
        预测收盘价序列
    last_close : float
        历史最后一个收盘价
    sample_count : int
        样本数，用于 uncertainty 的 sample_count_used 字段
    """
    closes_f = closes.astype(float)
    final_close = float(closes_f[-1])
    mean_close = float(np.mean(closes_f))

    q_low = float(np.percentile(closes_f, 25)) if len(closes_f) > 1 else mean_close
    q_high = float(np.percentile(closes_f, 75)) if len(closes_f) > 1 else mean_close

    (
        change_pct,
        direction,
        vol,
        _path_disp,  # None for single sample
        direction_confidence,
        confidence_score,
    ) = compute_single_sample(closes_f, last_close)

    uncertainty = PredictionUncertainty(
        expected_return=change_pct,
        direction=direction,
        direction_confidence=direction_confidence,
        volatility=vol,
        path_dispersion=None,
        confidence_score=confidence_score,
        sample_count_used=sample_count,
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
        "prediction_uncertainty": uncertainty.to_dict(),
    }
