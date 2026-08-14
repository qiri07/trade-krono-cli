"""
向后兼容 shim：将 PredictionUncertainty 重定向到 PredictionDistribution。

新代码请直接使用 trade_krono_cli.prediction_distribution.PredictionDistribution，
它额外支持 p10/p25/p50/p75/p90 分位数字段。
"""
from __future__ import annotations

from trade_krono_cli.prediction_distribution import (  # noqa: F401
    PredictionDistribution as PredictionUncertainty,
    compute_single_sample,
    compute_multi_sample,
    build_distribution,
    build_result_dict,
)

# 旧 API 别名，供遗留代码使用
build_uncertainty = build_distribution
