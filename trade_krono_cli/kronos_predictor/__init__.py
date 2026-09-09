"""Kronos 预测器包 — 按功能拆分的预测执行模块。

子模块：
  - predictor: 核心预测执行（predict_one, predict_batch）
  - data_prep: 数据准备（fetch_lookback, x/y构造, batch分割）
  - result_parser: 结果解析（预测DataFrame → 结构化结果）
  - streaming: 流式预测（跳过缓存检查，直接处理预取数据）
  - cache: 缓存管理（读写预测结果）
"""

from __future__ import annotations

# 延迟导入以避免循环依赖
__all__ = ("KronosPredictor", "DataPreparator", "ResultParser", "StreamingPredictor", "KronosCacheManager")


def __getattr__(name: str):
    """延迟导入以支持 from trade_krono_cli.kronos_predictor import XXX。"""
    if name == "KronosPredictor":
        from trade_krono_cli.kronos_predictor.predictor import KronosPredictor
        return KronosPredictor
    if name == "DataPreparator":
        from trade_krono_cli.kronos_predictor.data_prep import DataPreparator
        return DataPreparator
    if name == "ResultParser":
        from trade_krono_cli.kronos_predictor.result_parser import ResultParser
        return ResultParser
    if name == "StreamingPredictor":
        from trade_krono_cli.kronos_predictor.streaming import StreamingPredictor
        return StreamingPredictor
    if name == "KronosCacheManager":
        from trade_krono_cli.kronos_predictor.cache import KronosCacheManager
        return KronosCacheManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
