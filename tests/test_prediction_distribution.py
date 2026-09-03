"""测试 PredictionDistribution 及计算函数。"""

from __future__ import annotations

import numpy as np
import pytest

from trade_krono_cli.prediction_distribution import (
    PredictionDistribution,
    _compute_percentiles,
    build_distribution,
    build_result_dict,
    compute_multi_sample,
    compute_single_sample,
)

# ═══════════════════════════════════════════════════════
# PredictionDistribution 数据类
# ═══════════════════════════════════════════════════════


class TestPredictionDistribution:
    def test_basic_creation(self) -> None:
        d = PredictionDistribution(
            expected_return=3.2,
            direction="UP",
            direction_score=0.8,
            confidence_score=75.0,
            sample_count_used=1,
        )
        assert d.expected_return == 3.2
        assert d.direction == "UP"
        assert d.sample_count_used == 1

    def test_to_dict(self) -> None:
        d = PredictionDistribution(
            expected_return=5.0,
            direction="DOWN",
            direction_score=0.6,
            volatility=12.0,
            path_dispersion=None,
            confidence_score=60.0,
            sample_count_used=1,
            p10=95.0,
            p25=97.0,
            p50=100.0,
            p75=103.0,
            p90=105.0,
        )
        data = d.to_dict()
        assert data["expected_return"] == 5.0
        assert data["direction"] == "DOWN"
        assert data["p10"] == 95.0
        assert data["path_dispersion"] is None

    def test_from_dict(self) -> None:
        data = {
            "expected_return": 2.5,
            "direction": "UP",
            "direction_score": 0.7,
            "volatility": 8.0,
            "path_dispersion": 0.05,
            "confidence_score": 70.0,
            "sample_count_used": 3,
            "p10": 98.0,
            "p25": 99.0,
            "p50": 100.0,
            "p75": 101.0,
            "p90": 102.0,
        }
        d = PredictionDistribution.from_dict(data)
        assert d.expected_return == 2.5
        assert d.sample_count_used == 3
        assert d.p50 == 100.0
        # 多余的字段应被忽略
        extra = PredictionDistribution.from_dict({**data, "unknown_field": 42})
        assert not hasattr(extra, "unknown_field") or extra.unknown_field is None

    def test_defaults(self) -> None:
        d = PredictionDistribution()
        assert d.expected_return is None
        assert d.direction is None
        assert d.sample_count_used == 1


# ═══════════════════════════════════════════════════════
# compute_single_sample
# ═══════════════════════════════════════════════════════


class TestComputeSingleSample:
    def test_up_movement(self) -> None:
        closes = np.array([100.0, 102.0, 105.0, 108.0])
        last_close = 100.0
        change_pct, direction, _vol, path_disp, dir_score, conf_score, _pctiles = (
            compute_single_sample(closes, last_close)
        )
        assert direction == "UP"
        assert change_pct > 0
        assert path_disp is None  # 单样本无路径分散度
        assert 0 <= dir_score <= 1
        assert 0 <= conf_score <= 100

    def test_down_movement(self) -> None:
        closes = np.array([100.0, 97.0, 94.0, 90.0])
        last_close = 100.0
        change_pct, direction, _vol, _path_disp, _dir_score, _conf_score, _ = compute_single_sample(
            closes, last_close,
        )
        assert direction == "DOWN"
        assert change_pct < 0

    def test_flat_movement(self) -> None:
        # 完全平坦的路径，change_pct=0 → direction_score≈0.5 → conf_score≈50
        closes = np.array([100.0, 100.0, 100.0, 100.0])
        last_close = 100.0
        change_pct, direction, _, _, _, conf_score, _ = compute_single_sample(closes, last_close)
        assert direction == "FLAT"
        assert change_pct == 0.0
        assert conf_score == pytest.approx(50.0, abs=1.0)

    def test_high_volatility(self) -> None:
        closes = np.array([100.0, 200.0, 0.0, 150.0])
        last_close = 100.0
        _change_pct, direction, vol, _, dir_score, conf_score, _ = compute_single_sample(
            closes, last_close,
        )
        assert vol > 50.0  # 极高波动
        # 高波动稀释 direction_score（分母大），但 change_pct=50% 较大
        # 这里主要验证不会报错且方向正确
        assert direction in ("UP", "DOWN", "FLAT")
        assert 0 <= dir_score <= 1
        assert 0 <= conf_score <= 100

    def test_percentiles_degenerate(self) -> None:
        closes = np.array([100.0, 105.0, 110.0])
        last_close = 100.0
        _, _, _, _, _, _, pctiles = compute_single_sample(closes, last_close)
        # 单样本时百分位全部退化为 final close
        final = float(closes[-1])
        assert all(p == final for p in pctiles)

    def test_very_small_closes(self) -> None:
        closes = np.array([1.0, 1.05, 1.10])
        last_close = 1.0
        change_pct, direction, _, _, _, _, _ = compute_single_sample(closes, last_close)
        assert direction == "UP"
        assert abs(change_pct - 10.0) < 0.1


# ═══════════════════════════════════════════════════════
# compute_multi_sample
# ═══════════════════════════════════════════════════════


class TestComputeMultiSample:
    def test_multi_sample_basic(self) -> None:
        # 3 条路径，均值路径向上
        avg_close = np.array([100.0, 103.0, 107.0])
        stacked = np.array(
            [
                [100.0, 104.0, 108.0],  # 路径 1：更乐观
                [100.0, 102.0, 106.0],  # 路径 2：更悲观
                [100.0, 104.0, 106.0],  # 路径 3：接近均值
            ],
        )
        last_close = 100.0
        _change_pct, direction, _vol, path_disp, dir_score, conf_score, _pctiles = (
            compute_multi_sample(avg_close, stacked, last_close)
        )
        assert direction == "UP"
        assert path_disp is not None  # 多样本有路径分散度
        assert path_disp >= 0
        assert 0 <= dir_score <= 1
        assert 0 <= conf_score <= 100

    def test_multi_sample_high_dispersion(self) -> None:
        # 路径间差异大 → 高 dispersion → 低 confidence
        avg_close = np.array([100.0, 105.0, 110.0])
        stacked = np.array(
            [
                [100.0, 120.0, 140.0],  # 路径 1：大涨
                [100.0, 90.0, 80.0],  # 路径 2：大跌
            ],
        )
        last_close = 100.0
        _, _, _, path_disp, dir_score, conf_score, _ = compute_multi_sample(
            avg_close, stacked, last_close,
        )
        assert path_disp > 0.1  # 高分散
        # 高 dispersion 会降低 confidence_score
        assert conf_score < dir_score * 50 + 50

    def test_multi_sample_low_dispersion(self) -> None:
        # 路径高度一致 → 低 dispersion → 高 confidence
        avg_close = np.array([100.0, 103.0, 106.0])
        stacked = np.array(
            [
                [100.0, 103.0, 106.0],
                [100.0, 103.1, 106.1],
                [100.0, 102.9, 105.9],
            ],
        )
        last_close = 100.0
        _, _, _, path_disp, dir_score, conf_score, _ = compute_multi_sample(
            avg_close, stacked, last_close,
        )
        assert path_disp < 0.01  # 极低分散
        assert conf_score > dir_score * 50  # 高 confidence

    def test_multi_sample_percentiles(self) -> None:
        avg_close = np.array([100.0, 105.0, 110.0])
        stacked = np.array(
            [
                [100.0, 104.0, 108.0],
                [100.0, 106.0, 112.0],
                [100.0, 103.0, 107.0],
                [100.0, 107.0, 113.0],
            ],
        )
        last_close = 100.0
        _, _, _, _, _, _, pctiles = compute_multi_sample(avg_close, stacked, last_close)
        p10, p25, p50, p75, p90 = pctiles
        assert p10 <= p25 <= p50 <= p75 <= p90  # 百分位单调非递减


# ═══════════════════════════════════════════════════════
# build_distribution
# ═══════════════════════════════════════════════════════


class TestBuildDistribution:
    def test_build_single_sample(self) -> None:
        dist = build_distribution(
            change_pct=3.5,
            direction="UP",
            vol=5.0,
            path_dispersion=None,
            direction_score=0.75,
            confidence_score=75.0,
            sample_count=1,
            percentiles=(105.0, 105.0, 105.0, 105.0, 105.0),
        )
        assert dist.expected_return == 3.5
        assert dist.direction == "UP"
        assert dist.path_dispersion is None
        assert dist.sample_count_used == 1

    def test_build_multi_sample(self) -> None:
        dist = build_distribution(
            change_pct=4.0,
            direction="UP",
            vol=6.0,
            path_dispersion=0.03,
            direction_score=0.7,
            confidence_score=68.0,
            sample_count=5,
            percentiles=(102.0, 103.5, 105.0, 106.5, 108.0),
        )
        assert dist.sample_count_used == 5
        assert dist.path_dispersion == 0.03
        assert dist.p10 == 102.0
        assert dist.p90 == 108.0


# ═══════════════════════════════════════════════════════
# build_result_dict
# ═══════════════════════════════════════════════════════


class TestBuildResultDict:
    def test_single_sample_result(self) -> None:
        closes = np.array([100.0, 103.0, 107.0])
        last_close = 100.0
        result = build_result_dict(closes, last_close, sample_count=1)
        assert result["direction"] == "UP"
        assert result["expected_change_pct"] > 0
        assert "prediction_distribution" in result
        assert "prediction_uncertainty" in result  # 向后兼容
        pd = result["prediction_distribution"]
        assert pd["direction"] == "UP"
        assert pd["sample_count_used"] == 1

    def test_multi_sample_result(self) -> None:
        avg_close = np.array([100.0, 104.0, 108.0])
        stacked = np.array(
            [
                [100.0, 105.0, 110.0],
                [100.0, 103.0, 106.0],
            ],
        )
        last_close = 100.0
        result = build_result_dict(avg_close, last_close, stacked=stacked, sample_count=2)
        assert result["direction"] == "UP"
        pd = result["prediction_distribution"]
        assert pd["sample_count_used"] == 2
        assert pd["path_dispersion"] is not None
        assert pd["p10"] is not None

    def test_result_has_all_keys(self) -> None:
        closes = np.array([100.0, 102.0, 105.0])
        last_close = 100.0
        result = build_result_dict(closes, last_close, sample_count=1)
        expected_keys = {
            "predicted_close_mean",
            "predicted_close_final",
            "expected_change_pct",
            "direction",
            "volatility_proxy",
            "confidence_band",
            "prediction_distribution",
            "prediction_uncertainty",
        }
        assert expected_keys.issubset(result.keys())


# ═══════════════════════════════════════════════════════
# _compute_percentiles
# ═══════════════════════════════════════════════════════


class TestComputePercentiles:
    def test_single_sample_degenerates(self) -> None:
        stacked = np.array([[100.0, 105.0, 110.0]])
        last_close = 100.0
        p10, p25, p50, p75, p90 = _compute_percentiles(stacked, 1, last_close)
        final = 110.0
        assert p10 == p25 == p50 == p75 == p90 == final

    def test_multi_sample_sorted(self) -> None:
        stacked = np.array(
            [
                [100.0, 105.0, 108.0],
                [100.0, 103.0, 106.0],
                [100.0, 107.0, 112.0],
                [100.0, 104.0, 109.0],
            ],
        )
        last_close = 100.0
        p10, p25, p50, p75, p90 = _compute_percentiles(stacked, 4, last_close)
        assert p10 <= p25 <= p50 <= p75 <= p90
