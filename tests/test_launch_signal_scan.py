"""tests/test_launch_signal_scan.py — 大行情启动信号扫描器单元测试。

对 scripts/_utils/launch_signals.py 的纯计算函数做覆盖测试：
  - OBV / ATR 基础计算
  - 突破在最后一行判定的端到端场景（无突破、数据不足、极端 VR、自定义参数）
  - 蓄势质量指标落盘
  - Kill-Switch（直接检测分量函数）：突破后跌回、回踩放量
  - 确认质量（直接检测分量函数）：守住突破位得分

所有测试使用合成 pandas DataFrame，不涉及网络或数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import pandas as pd

from scripts._utils.launch_signals import (
    LaunchSignalParams,
    check_confirmation_quality,
    check_kill_switch,
    compute_atr,
    compute_launch_signal,
    compute_obv,
)

# ═════════════════════════════════════════════════════════════════════════════
# 测试数据构造
# ═════════════════════════════════════════════════════════════════════════════


def _make_series(values: list[float], start: datetime = datetime(2026, 1, 1)) -> pd.Series:
    """用数值列表构造带日期索引的 pd.Series。"""
    idx = [start + timedelta(days=i) for i in range(len(values))]
    return pd.Series(values, index=idx)


def _make_launch_df(
    *,
    n_pre: int = 100,  # 蓄势期天数
    breakout_pct: float = 0.025,  # 突破幅度（相对前 60 日高）
    base_price: float = 10.0,
    base_vol: float = 10_000_000.0,
    vr_breakout: float = 2.0,  # 突破日 VR
) -> pd.DataFrame:
    """构造模拟“大行情启动”的日线 DataFrame，突破日为**最后一行**。

    结构：
      - 前 n_pre 天：缩量横盘，价格在 base_price 附近窄幅震荡
      - 最后一行（突破日）：放量，收盘突破前 60 日最高 (1+breakout_pct)

    注意：compute_launch_signal 在最后一行判定突破，因此突破日必须是序列末尾；
    突破后的确认与 Kill-Switch 需后续交易日数据，不在本函数内构造。
    """
    rows: list[dict[str, object]] = []
    d0 = datetime(2026, 1, 5)

    # ── 蓄势期：缩量横盘，价格窄幅震荡 ──
    for i in range(n_pre):
        d = d0 + timedelta(days=i)
        px = base_price * (1 + 0.01 * ((i % 5) - 2) / 5)
        vol = base_vol * (0.3 + 0.05 * (i % 3))
        rows.append(
            {
                "timestamps": d,
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": vol,
            }
        )

    # 前 60 日最高收盘价（蓄势期末）
    pre_high = cast("float", max(cast("float", r["close"]) for r in rows[-60:]))

    # ── 突破日（最后一行）──
    d_break = d0 + timedelta(days=n_pre)
    break_px = pre_high * (1 + breakout_pct)
    rows.append(
        {
            "timestamps": d_break,
            "open": pre_high,
            "high": break_px * 1.01,
            "low": pre_high,
            "close": break_px,
            "volume": base_vol * vr_breakout,
        }
    )

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamps").reset_index(drop=True)
    return df


def _make_flat_df(n: int = 120, base_price: float = 10.0) -> pd.DataFrame:
    """构造完全平坦、无波动的 DataFrame（用于无信号场景）。"""
    d0 = datetime(2026, 1, 5)
    rows = []
    for i in range(n):
        d = d0 + timedelta(days=i)
        rows.append(
            {
                "timestamps": d,
                "open": base_price,
                "high": base_price,
                "low": base_price,
                "close": base_price,
                "volume": 10_000_000.0,
            }
        )
    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════════════
# 计算工具函数测试
# ═════════════════════════════════════════════════════════════════════════════


def test_compute_obv_basic() -> None:
    """OBV 首值为 0，随后按涨跌方向累加成交量。"""
    close = pd.Series([10.0, 11.0, 11.0, 9.0])
    volume = pd.Series([100.0, 200.0, 300.0, 400.0])
    obv = compute_obv(close, volume)
    # Day0: 首值 0; Day1: 涨 +200 -> 200; Day2: 平 +0 -> 200; Day3: 跌 -400 -> -200
    expected = pd.Series([0.0, 200.0, 200.0, -200.0], dtype="float64")
    pd.testing.assert_series_equal(obv.reset_index(drop=True), expected)


def test_compute_atr_basic() -> None:
    """ATR 应为正数且长度与输入一致。"""
    n = 50
    high = pd.Series([11.0] * n)
    low = pd.Series([9.0] * n)
    close = pd.Series([10.0] * n)
    atr = compute_atr(high, low, close, 20)
    assert len(atr) == n
    assert (atr.dropna() > 0).all()


# ═════════════════════════════════════════════════════════════════════════════
# compute_launch_signal 端到端测试（突破日为最后一行）
# ═════════════════════════════════════════════════════════════════════════════


def test_launch_signal_breakout_detected() -> None:
    """最后一行突破前 60 日高：应检测出突破，metrics 含 breakout_pct 与 vr，且无 kill-switch。"""
    df = _make_launch_df(n_pre=100, breakout_pct=0.025, vr_breakout=2.0)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    # 突破被检测到（breakout_pct 约 2.5%），VR 落入 metrics
    assert abs(cast("float", result.metrics["breakout_pct"]) - 0.025) < 1e-9
    assert "vr" in result.metrics
    # 突破日为序列末尾，无后续交易日，故无 kill-switch 可触发
    assert result.kill_switch == []
    # 确认期需后续数据，单日断点得分未达 60，signal 为 False 是预期行为
    assert result.signal is False
    assert result.score > 0


def test_launch_signal_no_breakout() -> None:
    """未突破前 60 日高点时 signal=False。"""
    df = _make_flat_df(n=120, base_price=10.0)
    result = compute_launch_signal(
        ticker="sz.000858",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    assert result.signal is False
    assert "未突破" in "".join(result.reasons)


def test_launch_signal_insufficient_data() -> None:
    """数据长度不足（<65 日）时 signal=False。"""
    df = _make_flat_df(n=30, base_price=10.0)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    assert result.signal is False


def test_launch_signal_extreme_vr_deduction() -> None:
    """VR 极端放量（>3.0）应触发扣分理由。"""
    df = _make_launch_df(n_pre=100, breakout_pct=0.025, vr_breakout=5.0)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    reasons_text = "".join(result.reasons)
    assert "极端放量" in reasons_text or "VR" in reasons_text


def test_launch_signal_custom_params() -> None:
    """自定义 vr_min=2.0 而突破 VR=1.8，放量不足，得分偏低且 signal=False。"""
    params = LaunchSignalParams(vr_min=2.0, vr_max=2.5)
    df = _make_launch_df(n_pre=100, breakout_pct=0.025, vr_breakout=1.8)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
        params=params,
    )
    assert result.signal is False


def test_launch_signal_no_pullback() -> None:
    """无回踩数据（突破为末尾），确认阶段不扣“回踩未缩量”。"""
    df = _make_launch_df(n_pre=100, breakout_pct=0.025, vr_breakout=2.0)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    # 末尾突破后无后续日，确认被跳过，不应出现“回踩未缩量”式扣分
    assert not any("回踩未缩量" in str(r) for r in result.reasons)


def test_buildup_quality_reflected_in_metrics() -> None:
    """蓄势质量指标应在 metrics 中体现。"""
    df = _make_launch_df(n_pre=100, breakout_pct=0.025, vr_breakout=2.0)
    result = compute_launch_signal(
        ticker="sh.600519",
        close=df["close"],
        high=df["high"],
        low=df["low"],
        volume=df["volume"],
    )
    metrics = result.metrics
    assert "ma_squeeze_days" in metrics
    assert "vol_dry_ratio" in metrics


# ════════════════════════════════════════════════════════════════════════════
# 分量函数直接测试（Kill-Switch / 确认质量）
# ═════════════════════════════════════════════════════════════════════════════


def _make_kill_series(
    *,
    breakout_close: float = 10.5,
    breakout_low: float = 10.4,
    post_close: float = 10.3,
    post_low: float = 10.1,
    breakout_vol: float = 2_000_000.0,
    post_vol: float = 1_000_000.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """构造“第 90 天突破、随后 3 天”的序列，用于检测 Kill-Switch（需留出突破后空间）。"""
    closes = [10.0] * 90 + [breakout_close, post_close, post_close]
    highs = [10.1] * 90 + [breakout_close + 0.1, post_close + 0.1, post_close + 0.1]
    lows = [9.9] * 90 + [breakout_low, post_low, post_low]
    vols = [1_000_000.0] * 90 + [breakout_vol, post_vol, post_vol]
    return (
        pd.Series(closes),
        pd.Series(highs),
        pd.Series(lows),
        pd.Series(vols),
    )


def test_launch_signal_kill_switch_breakdown() -> None:
    """突破后次日跌回突破位 → Kill-Switch 触发“跌回突破位”。"""
    close, high, low, volume = _make_kill_series(
        post_close=10.3, post_low=10.1, post_vol=1_000_000.0
    )
    # 第 90 天（索引 90）突破，收盘 10.5；第 91 天收盘 10.3 跌回 10.5 之下
    kills = check_kill_switch(
        close, high, low, volume, LaunchSignalParams(), breakout_price=10.5, breakout_idx=90
    )
    assert any("跌回突破位" in k for k in kills)


def test_launch_signal_kill_switch_pullback_volume() -> None:
    """突破后次日低开回踩且放量 → Kill-Switch 触发“回踩放量”。"""
    close, high, low, volume = _make_kill_series(
        post_close=10.6,  # 收盘收回，不跌回突破位
        post_low=10.1,  # 但盘中低点 <= 突破低点 10.4，算回踩
        post_vol=3_000_000.0,  # 回踩日量 > 突破日量，放量
    )
    kills = check_kill_switch(
        close, high, low, volume, LaunchSignalParams(), breakout_price=10.5, breakout_idx=90
    )
    assert any("回踩放量" in k for k in kills)


def test_confirmation_quality_holds() -> None:
    """突破后 3 日均守住突破位 → 确认质量得分 > 0，且有“守住突破位”理由。"""
    close = pd.Series([10.0] * 80 + [10.5, 10.51, 10.52, 10.53])
    volume = pd.Series([1_000_000.0] * 80 + [2_000_000.0, 1_500_000.0, 1_500_000.0, 1_500_000.0])
    score, reasons, metrics = check_confirmation_quality(
        close, volume, LaunchSignalParams(), breakout_price=10.5, breakout_idx=80
    )
    assert score > 0
    assert any("守住突破位" in r for r in reasons)
    assert metrics.get("confirm_days_ok") == 3
