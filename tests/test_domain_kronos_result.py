"""trade_krono_cli.domain.kronos_result — KronosForecastResult 数据类单元测试。

覆盖构造、to_dict 往返、默认参数、prediction_uncertainty 可选。
"""

from __future__ import annotations

from trade_krono_cli.domain.kronos_result import KronosForecastResult


def test_defaults() -> None:
    res = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
    )
    assert res.ticker == "sh.600519"
    assert res.eval_date == "2026-08-11"
    assert res.horizon == 30
    assert res.interval == "d"
    assert res.last_close is None
    assert res.predicted_close_mean is None
    assert res.direction is None
    assert res.error is None
    assert res.elapsed_sec == 0.0
    assert res.prediction_uncertainty is None


def test_full_values() -> None:
    res = KronosForecastResult(
        ticker="sz.000858",
        eval_date="2026-08-12",
        horizon=10,
        interval="d",
        last_close=100.0,
        predicted_close_mean=105.0,
        predicted_close_final=105.0,
        expected_change_pct=5.0,
        direction="UP",
        volatility_proxy=0.15,
        confidence_band={"low": 102.0, "high": 108.0},
        forecast_dict={"close": [105.0]},
        model_name="kronos-base",
        error=None,
        elapsed_sec=12.5,
    )
    assert res.ticker == "sz.000858"
    assert res.expected_change_pct == 5.0
    assert res.direction == "UP"
    assert res.confidence_band == {"low": 102.0, "high": 108.0}
    assert res.forecast_dict == {"close": [105.0]}
    assert res.elapsed_sec == 12.5


def test_to_dict_roundtrip() -> None:
    res = KronosForecastResult(
        ticker="bj.920001",
        eval_date="2026-08-11",
        horizon=30,
        last_close=50.0,
        predicted_close_mean=52.0,
        direction="UP",
        model_name="kronos-base",
        elapsed_sec=3.0,
    )
    d = res.to_dict()
    assert d["ticker"] == "bj.920001"
    assert d["eval_date"] == "2026-08-11"
    assert d["horizon"] == 30
    assert d["last_close"] == 50.0
    assert d["predicted_close_mean"] == 52.0
    assert d["direction"] == "UP"
    assert d["model_name"] == "kronos-base"
    assert d["elapsed_sec"] == 3.0
    assert d["prediction_uncertainty"] is None
    assert d["error"] is None


def test_to_dict_with_uncertainty() -> None:
    class _FakeDist:
        def to_dict(self):
            return {"std": 0.02}

    res = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        prediction_uncertainty=_FakeDist(),
    )
    d = res.to_dict()
    assert d["prediction_uncertainty"] == {"std": 0.02}
