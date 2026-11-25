"""trade_krono_cli.kronos_predictor.result_parser 单元测试。

覆盖 ResultParser.parse_pred_df / pred_df_to_dict / apply_parsed_to_result /
run_predict / apply_cached / parse_batch_result / create_empty_result。
依赖的 prediction_distribution 与 KronosForecastResult 通过 mock 注入，
不触发真实模型推理。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from trade_krono_cli.kronos_predictor.result_parser import ResultParser


def _make_settings(**overrides) -> MagicMock:
    defaults = {
        "kronos_pred_len": 30,
        "kronos_T": 1.0,
        "kronos_top_p": 0.9,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_runner(n_samples: int = 1) -> MagicMock:
    runner = MagicMock()
    runner.sample_count = n_samples
    runner.model_name = "kronos-base"
    runner._settings_obj = _make_settings()
    adapter = MagicMock()
    # adapter.predict 返回 pred_df，close 列始终为 1D（2D 数组无法构造 pd.DataFrame 列）。
    # 多 sample 路径会在 run_predict 内部把返回的 pred_df 替换为 _FakePredDf。
    close_vals = np.array([101.0, 102.0, 103.0])
    pred_df = pd.DataFrame({"close": close_vals})
    runner._adapter = adapter
    adapter.predict.return_value = pred_df
    return runner


class TestResultParserCreateEmptyResult:
    def test_create_empty_result(self) -> None:
        parser = ResultParser(_make_settings())
        res = parser.create_empty_result(
            ticker="sh.600519",
            eval_date="2026-08-11",
            horizon=30,
            model_name="kronos",
        )
        assert res.ticker == "sh.600519"
        assert res.eval_date == "2026-08-11"
        assert res.horizon == 30
        assert res.model_name == "kronos"
        assert res.error is None
        assert res.elapsed_sec == 0.0


class TestParsePredDf:
    def test_parse_pred_df_normal(self) -> None:
        parser = ResultParser(_make_settings())
        pred_df = pd.DataFrame({"close": [101.0, 102.0, 103.0]})
        parsed = parser.parse_pred_df(pred_df, 100.0, sample_count=1)
        assert "expected_change_pct" in parsed
        assert parsed["expected_change_pct"] == pytest.approx(3.0)

    def test_parse_pred_df_empty_raises(self) -> None:
        parser = ResultParser(_make_settings())
        pred_df = pd.DataFrame({"close": []})
        with pytest.raises(RuntimeError, match="Kronos 返回空预测"):
            parser.parse_pred_df(pred_df, 100.0)


class TestPredDfToDict:
    def test_pred_df_to_dict(self) -> None:
        parser = ResultParser(_make_settings())
        idx = pd.date_range("2026-08-12", periods=3, freq="B")
        pred_df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1e6, 1.1e6, 1.2e6],
            },
            index=idx,
        )
        d = parser.pred_df_to_dict(pred_df)
        assert len(d["timestamps"]) == 3
        assert d["close"] == [100.5, 101.5, 102.5]
        assert d["volume"] == [1000000.0, 1100000.0, 1200000.0]

    def test_pred_df_to_dict_non_datetime_index(self) -> None:
        parser = ResultParser(_make_settings())
        pred_df = pd.DataFrame({"close": [100.0, 101.0]})
        d = parser.pred_df_to_dict(pred_df)
        assert len(d["timestamps"]) == 2


class TestApplyParsedToResult:
    def test_apply_parsed_to_result(self) -> None:
        parser = ResultParser(_make_settings())
        from trade_krono_cli.domain.kronos_result import KronosForecastResult

        res = KronosForecastResult(ticker="s", eval_date="d", horizon=1)
        parsed = {
            "expected_change_pct": 5.0,
            "direction": "UP",
            "volatility_proxy": 0.1,
            "confidence_band": {"low": 99.0, "high": 101.0},
            "prediction_uncertainty": {
                "expected_return": 5.0,
                "direction": "UP",
                "direction_score": 0.8,
                "volatility": 0.1,
                "path_dispersion": None,
                "confidence_score": 80.0,
                "sample_count_used": 1,
                "distribution_status": "degenerate",
                "p10": 101.0,
                "p25": 101.0,
                "p50": 101.0,
                "p75": 101.0,
                "p90": 101.0,
            },
            "forecast_dict": {"close": [100.0]},
        }
        parser.apply_parsed_to_result(res, parsed)
        assert res.expected_change_pct == 5.0
        assert res.direction == "UP"
        assert res.prediction_uncertainty is not None
        assert res.prediction_uncertainty.expected_return == 5.0
        assert res.forecast_dict == {"close": [100.0]}  # forecast_dict 被 setattr 到 result
        assert "forecast_dict" in parsed  # apply_parsed_to_result 不 pop forecast_dict
        assert "prediction_uncertainty" not in parsed  # pop'd
        assert "prediction_distribution" not in parsed  # pop'd


class TestRunPredictSingleSample:
    def test_run_predict_single_sample(self) -> None:
        parser = ResultParser(_make_settings())
        runner = _make_runner(n_samples=1)
        x_df = pd.DataFrame({"close": [100.0] * 5, "open": [100.0] * 5, "high": [101.0] * 5,
                             "low": [99.0] * 5, "volume": [1e6] * 5, "amount": [1e8] * 5})
        x_ts = pd.Series([datetime(2026, 8, 1)] * 5)
        y_ts = pd.Series([datetime(2026, 8, 12)] * 3)
        res = MagicMock()
        res.ticker = "s"
        res.eval_date = "d"
        res.horizon = 3
        res.model_name = "kronos"

        parser.run_predict(runner, x_df, x_ts, y_ts, 100.0, res)
        assert res.forecast_dict is not None
        assert "close" in res.forecast_dict


class _CloseValues:
    """模拟 pred_df['close'].astype(float).values 返回二维数组。"""

    def __init__(self, arr) -> None:
        self._arr = arr

    def astype(self, _t) -> _CloseValues:
        return self

    @property
    def values(self):
        return self._arr


class _FakePredDf:
    """模拟 adapter.predict 返回的 DataFrame-like 对象，close 为二维数组。"""

    def __getitem__(self, key: str) -> _CloseValues:
        assert key == "close"
        return _CloseValues(np.array([[101.0, 102.0, 103.0], [100.5, 101.5, 102.5]]))


class TestRunPredictMultiSample:
    def test_run_predict_multi_sample(self) -> None:
        parser = ResultParser(_make_settings())
        runner = _make_runner(n_samples=3)
        runner._adapter.predict.return_value = _FakePredDf()
        x_df = pd.DataFrame({"close": [100.0] * 5, "open": [100.0] * 5, "high": [101.0] * 5,
                             "low": [99.0] * 5, "volume": [1e6] * 5, "amount": [1e8] * 5})
        x_ts = pd.Series([datetime(2026, 8, 1)] * 5)
        y_ts = pd.Series([datetime(2026, 8, 12)] * 3)
        res = MagicMock()
        res.ticker = "s"
        res.eval_date = "d"
        res.horizon = 3
        res.model_name = "kronos"

        parser.run_predict(runner, x_df, x_ts, y_ts, 100.0, res)
        assert res.predicted_close_mean is not None
        assert res.direction is not None


class TestApplyCached:
    def test_apply_cached(self) -> None:
        parser = ResultParser(_make_settings())
        from trade_krono_cli.domain.kronos_result import KronosForecastResult

        res = KronosForecastResult(ticker="s", eval_date="d", horizon=1)
        cached = {
            "expected_change_pct": 5.0,
            "direction": "UP",
            "prediction_uncertainty": {"change_pct": 5.0, "direction": "UP"},
        }
        out = parser.apply_cached(res, cached)
        assert out.expected_change_pct == 5.0
        assert out.direction == "UP"
        assert out.elapsed_sec == 0.0


class TestParseBatchResult:
    def test_parse_batch_result(self) -> None:
        parser = ResultParser(_make_settings())
        res = MagicMock()
        res.ticker = "s"
        res.eval_date = "d"
        res.horizon = 3
        res.model_name = "kronos"
        pred_df = pd.DataFrame({"close": [101.0, 102.0, 103.0]})

        out = parser.parse_batch_result(res, pred_df, 100.0, sample_count=1)
        assert out.last_close == 100.0
        assert out.forecast_dict is not None
