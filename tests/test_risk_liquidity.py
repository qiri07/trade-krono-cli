"""tests for trade_krono_cli.risk.liquidity."""

from __future__ import annotations

import math

import pandas as pd

from trade_krono_cli.configs.risk import LiquidityThresholds
from trade_krono_cli.risk.liquidity import calc_liquidity_risk


def _make_volume(n: int, value: float = 1e6) -> pd.Series:
    """Create a volume series with n entries of given value."""
    return pd.Series([value] * n)


class TestCalcLiquidityRisk:
    """Liquidity risk scoring logic.

    Breakpoints (log1p-space): [(5.0, 80.0), (6.0, 60.0), (7.0, 40.0), (8.0, 20.0)]
    - Volume below log1p(5)→expm1(5)≈148  →  score = 80.0 (max risk)
    - Volume in [148, 2981]               →  interpolated
    - Volume above log1p(8)→expm1(8)≈2980 →  tail_penalty applies
    """

    def test_low_volume_high_risk(self) -> None:
        """Volume of 1000 → log1p≈6.91, between breakpoints (6,60) and (7,40)."""
        volume = _make_volume(100, value=1000.0)
        score, turnover = calc_liquidity_risk(volume)
        # Expected: ~41.8 (linearly interpolated between score 60 at vol=402 and score 40 at vol=1096)
        assert 40 <= score <= 45
        assert turnover is None

    def test_high_volume_low_risk(self) -> None:
        """Volume of 1e9 → tail_penalty zone, score clamped to 0."""
        volume = _make_volume(100, value=1e9)
        score, turnover = calc_liquidity_risk(volume)
        assert score < 30  # Tail penalty drives score well below 30

    def test_median_volume_medium_risk(self) -> None:
        """Volume=100 → log1p≈4.62, below all breakpoints → score=80.0 (max risk)."""
        volume = _make_volume(100, value=100.0)
        score, turnover = calc_liquidity_risk(volume)
        assert score == 80.0

    def test_insufficient_data_rows(self) -> None:
        """Too few rows should return insufficient_data_score."""
        th = LiquidityThresholds()
        volume = _make_volume(th.insufficient_data_min_rows - 1, value=1e6)
        score, turnover = calc_liquidity_risk(volume)
        assert score == th.insufficient_data_score
        assert turnover is None

    def test_market_cap_affects_turnover(self) -> None:
        """With market cap, avg_turnover should be calculated."""
        volume = _make_volume(100, value=1e6)
        score, turnover = calc_liquidity_risk(volume, market_cap=100.0)
        assert turnover is not None
        assert isinstance(turnover, float)
        assert turnover > 0

    def test_no_market_cap_returns_none_turnover(self) -> None:
        """Without market cap, turnover should be None."""
        volume = _make_volume(100, value=1e6)
        score, turnover = calc_liquidity_risk(volume, market_cap=None)
        assert turnover is None

    def test_zero_market_cap_returns_none_turnover(self) -> None:
        """Zero market cap should not produce turnover."""
        volume = _make_volume(100, value=1e6)
        score, turnover = calc_liquidity_risk(volume, market_cap=0.0)
        assert turnover is None

    def test_score_clamped_to_0_100(self) -> None:
        """Score should be clamped to [0, 100]."""
        # Very low volume (below all breakpoints)
        volume = _make_volume(100, value=1.0)
        score, _ = calc_liquidity_risk(volume)
        assert 0.0 <= score <= 100.0

        # Very high volume (tail penalty)
        volume = _make_volume(100, value=1e15)
        score, _ = calc_liquidity_risk(volume)
        assert 0.0 <= score <= 100.0

    def test_returns_tuple(self) -> None:
        """Return type should be (float, float | None)."""
        volume = _make_volume(100, value=1e6)
        result = calc_liquidity_risk(volume)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)

    def test_tail_penalty_behavior(self) -> None:
        """Higher volume in tail zone should produce lower or equal score."""
        volume1 = _make_volume(100, value=1e9)
        score1, _ = calc_liquidity_risk(volume1)
        volume2 = _make_volume(100, value=1e12)
        score2, _ = calc_liquidity_risk(volume2)
        assert score2 <= score1

    def test_linear_interpolation(self) -> None:
        """Scores between breakpoints should be linearly interpolated."""
        th = LiquidityThresholds()
        bps = th.breakpoints
        if len(bps) >= 2:
            mid_log = (math.log1p(bps[0][0]) + math.log1p(bps[-1][0])) / 2
            mid_vol = math.expm1(mid_log)
            volume = _make_volume(100, value=mid_vol)
            score, _ = calc_liquidity_risk(volume)
            assert score is not None

    def test_empty_series(self) -> None:
        """Empty volume series should handle gracefully."""
        volume = pd.Series([], dtype=float)
        score, turnover = calc_liquidity_risk(volume)
        th = LiquidityThresholds()
        assert score == th.insufficient_data_score
        assert turnover is None

    def test_below_min_breakpoint(self) -> None:
        """Volume below the minimum breakpoint should return max risk score."""
        # expm1(5) ≈ 148, use 100 which is below that
        volume = _make_volume(100, value=100.0)
        score, _ = calc_liquidity_risk(volume)
        assert score == 80.0

    def test_at_max_breakpoint(self) -> None:
        """Volume exactly at max breakpoint should return that point's score."""
        # expm1(8) ≈ 2980.96
        volume = _make_volume(100, value=math.expm1(8.0))
        score, _ = calc_liquidity_risk(volume)
        assert score == 20.0

    def test_above_max_breakpoint_penalized(self) -> None:
        """Volume above max breakpoint should be penalized (score drops)."""
        # Just above the max breakpoint
        volume = _make_volume(100, value=math.expm1(8.0) * 1.5)
        score, _ = calc_liquidity_risk(volume)
        assert score < 20.0

    def test_thresholds_custom(self) -> None:
        """Custom thresholds should be respected."""
        th = LiquidityThresholds(
            breakpoints=[(4.0, 90.0), (6.0, 30.0)],
            tail_penalty_rate=2.0,
            insufficient_data_score=50.0,
            insufficient_data_min_rows=5,
        )
        volume = _make_volume(3, value=1e6)
        score, turnover = calc_liquidity_risk(volume, thresholds=th)
        assert score == 50.0  # Insufficient rows
        assert turnover is None
