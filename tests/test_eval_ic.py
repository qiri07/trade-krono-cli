"""测试 eval_ic.py — 信息系数 (IC) 评估逻辑。

覆盖：_safe_pearson / _safe_spearman / _rank_transform /
_compute_ic_for_signal / compute_ic_aggregated / compute_ic_metrics。
"""

from __future__ import annotations

import numpy as np

from trade_krono_cli.eval_data import EvalRecord, HorizonMetrics
from trade_krono_cli.eval_ic import (
    ICResult,
    _compute_ic_for_signal,
    _rank_transform,
    _safe_pearson,
    _safe_spearman,
    compute_ic_aggregated,
    compute_ic_metrics,
)

# ═══════════════════════════════════════════════════════
# _safe_pearson
# ═══════════════════════════════════════════════════════


def test_safe_pearson_normal_positive_correlation():
    """完全正相关：返回 1.0。"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    assert _safe_pearson(x, y) == 1.0


def test_safe_pearson_normal_negative_correlation():
    """完全负相关：返回 -1.0。"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([10.0, 8.0, 6.0, 4.0, 2.0])
    assert _safe_pearson(x, y) == -1.0


def test_safe_pearson_uncorrelated():
    """无相关：接近 0。"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([1.0, 1.0, 1.0, 1.0, 1.0])  # 零方差
    assert _safe_pearson(x, y) == 0.0


def test_safe_pearson_less_than_3_samples():
    """样本数 < 3：返回 0.0。"""
    assert _safe_pearson(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == 0.0
    assert _safe_pearson(np.array([1.0]), np.array([2.0])) == 0.0
    assert _safe_pearson(np.array([]), np.array([])) == 0.0


def test_safe_pearson_zero_variance():
    """零方差：分母接近 0，返回 0.0。"""
    x = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _safe_pearson(x, y) == 0.0


# ═══════════════════════════════════════════════════════
# _safe_spearman
# ═══════════════════════════════════════════════════════


def test_safe_spearman_normal_positive():
    """正相关：返回接近 1.0。"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([2.0, 3.0, 1.0, 5.0, 4.0])
    result = _safe_spearman(x, y)
    assert 0.0 < result <= 1.0


def test_safe_spearman_perfect_positive():
    """完全正相关（相同排序）：返回 1.0。"""
    x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _safe_spearman(x, y) == 1.0


def test_safe_spearman_perfect_negative():
    """完全负相关（相反排序）：返回 -1.0。"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    assert _safe_spearman(x, y) == -1.0


def test_safe_spearman_less_than_3_samples():
    """样本数 < 3：返回 0.0。"""
    assert _safe_spearman(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == 0.0


def test_safe_spearman_with_ties():
    """存在并列值：平均秩处理，不崩溃，返回合理值。"""
    x = np.array([1.0, 2.0, 2.0, 3.0, 4.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = _safe_spearman(x, y)
    assert -1.0 <= result <= 1.0


# ═══════════════════════════════════════════════════════
# _rank_transform
# ═══════════════════════════════════════════════════════


def test_rank_transform_empty():
    """空数组：返回空数组。"""
    result = _rank_transform(np.array([]))
    assert len(result) == 0


def test_rank_transform_single():
    """单元素：秩为 1。"""
    result = _rank_transform(np.array([5.0]))
    assert len(result) == 1
    assert result[0] == 1.0


def test_rank_transform_no_ties():
    """无并列：1-based 秩。"""
    result = _rank_transform(np.array([10.0, 30.0, 20.0]))
    assert result[0] == 1.0  # 10 最小 → 秩 1
    assert result[1] == 3.0  # 30 最大 → 秩 3
    assert result[2] == 2.0  # 20 居中 → 秩 2


def test_rank_transform_with_ties():
    """有并列：并列取平均秩。"""
    # [10, 20, 20, 30] → 排序 [10, 20, 20, 30]，20 并列占秩 2,3 → 平均 2.5
    result = _rank_transform(np.array([10.0, 20.0, 20.0, 30.0]))
    assert result[0] == 1.0
    assert result[1] == 2.5
    assert result[2] == 2.5
    assert result[3] == 4.0


# ═══════════════════════════════════════════════════════
# _compute_ic_for_signal
# ═══════════════════════════════════════════════════════


def test_compute_ic_for_signal_less_than_10():
    """样本数 < 10：返回幂等 ICResult（n_groups=1, n_records=n）。"""
    preds = np.array([1.0, 2.0, 3.0])
    acts = np.array([4.0, 5.0, 6.0])
    result = _compute_ic_for_signal(preds, acts)
    assert result.n_groups == 1
    assert result.n_records == 3
    assert result.ic_mean == 0.0


def test_compute_ic_for_signal_positive_correlation():
    """正相关：ic_mean > 0，rank_ic_mean > 0。"""
    preds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0])
    acts = np.array([2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0])
    result = _compute_ic_for_signal(preds, acts)
    assert result.ic_mean == 1.0
    assert result.rank_ic_mean == 1.0
    assert result.ic_positive_pct == 100.0


def test_compute_ic_for_signal_negative_correlation():
    """负相关：ic_mean < 0，rank_ic_mean < 0。"""
    preds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0])
    acts = np.array([22.0, 20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 4.0, 2.0])
    result = _compute_ic_for_signal(preds, acts)
    assert result.ic_mean == -1.0
    assert result.rank_ic_mean == -1.0
    assert result.ic_positive_pct == 0.0


def test_compute_ic_for_signal_with_nan():
    """含 NaN：自动过滤后计算。"""
    preds = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
    acts = np.array([2.0, 4.0, 6.0, np.nan, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0])
    result = _compute_ic_for_signal(preds, acts)
    # 过滤后 10 条有效记录，完全正相关
    assert result.n_records == 10
    assert result.ic_mean == 1.0


# ═══════════════════════════════════════════════════════
# compute_ic_aggregated
# ═══════════════════════════════════════════════════════


def test_compute_ic_aggregated_empty():
    """空列表：返回默认 ICResult。"""
    result = compute_ic_aggregated([])
    assert result.n_groups == 0
    assert result.n_records == 0
    assert result.ic_mean == 0.0


def test_compute_ic_aggregated_single_group():
    """单组：ic_std 取 |ic_mean|（单组 std 未定义）。"""
    r = ICResult(
        ic_mean=0.05,
        ic_std=0.0,
        rank_ic_mean=0.04,
        rank_ic_std=0.0,
        n_records=100,
        n_groups=1,
    )
    result = compute_ic_aggregated([r])
    assert result.ic_mean == 0.05
    assert result.ic_std == 0.05  # 单组：std = |mean|
    assert result.n_groups == 1


def test_compute_ic_aggregated_multiple_groups():
    """多组：计算跨日均值/标准差/ICIR。"""
    r1 = ICResult(
        ic_mean=0.05, ic_std=0.01, rank_ic_mean=0.04, rank_ic_std=0.01,
        n_records=100, n_groups=1,
    )
    r2 = ICResult(
        ic_mean=0.03, ic_std=0.01, rank_ic_mean=0.02, rank_ic_std=0.01,
        n_records=100, n_groups=1,
    )
    r3 = ICResult(
        ic_mean=0.07, ic_std=0.01, rank_ic_mean=0.06, rank_ic_std=0.01,
        n_records=100, n_groups=1,
    )
    result = compute_ic_aggregated([r1, r2, r3])
    assert result.n_groups == 3
    assert result.n_records == 300
    assert result.ic_mean == round((0.05 + 0.03 + 0.07) / 3, 4)
    assert result.rank_ic_mean == round((0.04 + 0.02 + 0.06) / 3, 4)
    assert result.ic_std > 0
    assert result.ic_positive_pct == 100.0  # 三个 rank_ic 均 > 0


def test_compute_ic_aggregated_mixed_signs():
    """正负混合：ic_positive_pct 反映正的比例。"""
    r1 = ICResult(
        ic_mean=0.05, rank_ic_mean=0.04, n_records=100, n_groups=1,
    )
    r2 = ICResult(
        ic_mean=-0.03, rank_ic_mean=-0.02, n_records=100, n_groups=1,
    )
    result = compute_ic_aggregated([r1, r2])
    assert result.ic_positive_pct == 50.0


# ═══════════════════════════════════════════════════════
# compute_ic_metrics
# ═══════════════════════════════════════════════════════


def _make_record(
    ticker: str,
    eval_date: str,
    horizon_days: int = 5,
    pred_return_pct: float | None = 1.0,
    composite_score: float | None = 50.0,
    ta_signal: str = "BUY",
    actual_return_pct: float = 2.0,
) -> EvalRecord:
    return EvalRecord(
        ticker=ticker,
        eval_date=eval_date,
        horizon_days=horizon_days,
        pred_direction="UP",
        pred_return_pct=pred_return_pct,
        actual_return_pct=actual_return_pct,
        actual_direction="UP",
        is_direction_correct=True,
        error_pct=-1.0,
        ta_signal=ta_signal,
        composite_score=composite_score,
    )


def test_compute_ic_metrics_empty_records():
    """无记录：返回 0，不写入 metrics。"""
    m = HorizonMetrics()
    n = compute_ic_metrics([], m)
    assert n == 0
    assert m.ic_composite_mean == 0.0


def test_compute_ic_metrics_less_than_3_dates():
    """eval_date < 3：跳过 IC 计算，返回 0。"""
    records = [
        _make_record(f"sh.60051{i}", "2026-08-11") for i in range(15)
    ]  # 同一日期
    m = HorizonMetrics()
    n = compute_ic_metrics(records, m)
    assert n == 0


def test_compute_ic_metrics_group_less_than_10():
    """每组记录数 < 10：该组跳过，不进入聚合，metrics 保持默认值。"""
    records = []
    for d in ["2026-08-11", "2026-08-12", "2026-08-13"]:
        for i in range(5):  # 每组仅 5 条 < 10
            records.append(_make_record(f"sh.60051{i}", d))
    m = HorizonMetrics()
    n = compute_ic_metrics(records, m)
    assert n == 15  # 返回总记录数（所有日期组记录之和）
    # 所有组都 < 10，未聚合，metrics 保持默认 0
    assert m.ic_composite_mean == 0.0
    assert m.rank_ic_composite_mean == 0.0
    assert m.ic_ta_mean == 0.0
    assert m.rank_ic_ta_mean == 0.0


def test_compute_ic_metrics_normal_composite_kronos_ta():
    """正常情况：composite / kronos / ta 三类 IC 均计算。"""
    records = []
    for d in ["2026-08-11", "2026-08-12", "2026-08-13"]:
        for i in range(15):
            records.append(
                _make_record(
                    f"sh.60051{i}",
                    d,
                    pred_return_pct=float(i % 5),
                    composite_score=float(i * 2),
                    ta_signal="BUY",
                    actual_return_pct=float(i % 7 - 3),
                )
            )
    m = HorizonMetrics()
    n = compute_ic_metrics(records, m)
    assert n > 0
    # composite IC 应被写入
    assert m.ic_composite_mean != 0.0 or m.rank_ic_composite_mean != 0.0
    # ta IC 应被写入（所有记录 ta_signal=BUY，每组 15 ≥ 10）
    assert m.ic_ta_mean != 0.0 or m.rank_ic_ta_mean != 0.0


def test_compute_ic_metrics_kronos_insufficient():
    """kronos 预测值不足（<10 条）：kronos IC 跳过，不写入。"""
    records = []
    for d in ["2026-08-11", "2026-08-12", "2026-08-13"]:
        for i in range(15):
            records.append(
                _make_record(
                    f"sh.60051{i}",
                    d,
                    pred_return_pct=None,  # 无 kronos 预测
                    composite_score=float(i),
                    ta_signal="BUY",
                    actual_return_pct=float(i),
                )
            )
    m = HorizonMetrics()
    n = compute_ic_metrics(records, m)
    assert n > 0
    assert m.ic_kronos_mean == 0.0  # 未写入


def test_compute_ic_metrics_ta_insufficient_buy():
    """TA BUY 记录不足（<10 条）：ta IC 跳过，不写入。"""
    records = []
    for d in ["2026-08-11", "2026-08-12", "2026-08-13"]:
        for i in range(15):
            records.append(
                _make_record(
                    f"sh.60051{i}",
                    d,
                    composite_score=float(i),
                    ta_signal="HOLD" if i < 5 else "BUY",  # 每组仅 10 条 BUY，刚好 ≥10
                    actual_return_pct=float(i),
                )
            )
    m = HorizonMetrics()
    n = compute_ic_metrics(records, m)
    assert n > 0
    # 每组 BUY 正好 10 条，应被计入
    assert m.ic_ta_mean != 0.0 or m.rank_ic_ta_mean != 0.0
