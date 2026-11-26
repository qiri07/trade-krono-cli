"""double_ma 选股 — 工具函数。

包含 _limit_pct / load_metadata / get_st_tickers / calc_ma_ratio / calc_macd
等通用辅助函数。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts._utils import (
    load_metadata_index,
)
from scripts.double_ma.constants import MA_FAST, MA_SLOW
from scripts.indicators import calc_ma as _indicators_calc_ma
from scripts.indicators import calc_macd_dif as _indicators_calc_macd_dif


def _limit_pct(bare_code: str) -> float:
    """涨跌停幅度（%）：创业板(30)/科创板(68) 20%，主板 10%。"""
    return 20.0 if bare_code[:2] in ("30", "68") else 10.0


def load_metadata(cache_dir: Path) -> dict:
    """加载 PE/行业/ST 元数据（键统一为带前缀格式）。

    实现见 :func:`scripts._utils.load_metadata_index`（``include_bare_keys=False``
    保持仅带前缀键，避免条数统计翻倍）。
    """
    return load_metadata_index(cache_dir, include_bare_keys=False)


def get_st_tickers(metadata: dict) -> set[str]:
    """从元数据中提取 ST 股票列表。"""
    return {t for t, info in metadata.items() if isinstance(info, dict) and info.get("is_st")}


def calc_ma_ratio(df: pd.DataFrame, fast: int = MA_FAST, slow: int = MA_SLOW) -> float | None:
    """计算快速均线/慢速均线比值（>1 表示快线在慢线之上）。

    下沉实现见 :func:`scripts.indicators.calc_ma`；本函数为向后兼容包装。
    """
    if len(df) < slow:
        return None
    close = df["close"] if "close" in df.columns else df.iloc[:, 0]
    ma_fast = float(_indicators_calc_ma(close, fast).iloc[-1])
    ma_slow = float(_indicators_calc_ma(close, slow).iloc[-1])
    if ma_slow == 0 or pd.isna(ma_fast) or pd.isna(ma_slow):
        return None
    return ma_fast / ma_slow


def calc_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float | None:
    """计算 MACD 线值（dif），MACD > 0 表示在 0 轴上方。

    下沉实现见 :func:`scripts.indicators.calc_macd_dif`；本函数为向后兼容包装，
    将数据不足时的 float("nan") 规范化为 None，保持与原签名一致。
    """
    val = _indicators_calc_macd_dif(closes, fast=fast, slow=slow)
    return None if pd.isna(val) else val
