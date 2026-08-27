"""测试 PredictionUncertainty 向后兼容 shim。"""
from __future__ import annotations

import pytest

from trade_krono_cli import prediction_uncertainty


def test_prediction_uncertainty_is_alias():
    """PredictionUncertainty 应为 PredictionDistribution 的别名。"""
    from trade_krono_cli.prediction_distribution import PredictionDistribution
    assert prediction_uncertainty.PredictionUncertainty is PredictionDistribution


def test_build_uncertainty_alias():
    """build_uncertainty 应为 build_distribution 的别名。"""
    from trade_krono_cli.prediction_distribution import build_distribution
    assert prediction_uncertainty.build_uncertainty is build_distribution


def test_other_exports():
    """其他导出项应正确转发。"""
    assert hasattr(prediction_uncertainty, "compute_single_sample")
    assert hasattr(prediction_uncertainty, "compute_multi_sample")
    assert hasattr(prediction_uncertainty, "build_distribution")


def test_use_as_prediction_distribution():
    """可以通过别名创建实例并调用方法。"""
    pu = prediction_uncertainty.PredictionUncertainty(
        expected_return=3.2, direction="UP", direction_score=0.8,
        confidence_score=75.0, sample_count_used=1,
    )
    d = pu.to_dict()
    assert d["expected_return"] == 3.2
    assert d["direction"] == "UP"
