"""测试 PredictionUncertainty / KronosForecastResult 数据类。"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


class TestKronosRunnerParsePredDf:
    """_parse_pred_df 核心预测解析逻辑。"""

    def _make_runner(self):
        """创建真实 KronosRunner 实例（跳过模型加载和 settings 依赖）。"""
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        return KronosRunner(no_cache=True, sample_count=1, settings=settings)

    def test_single_sample_up(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 102.0, 104.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0, sample_count=1)

        assert result["expected_change_pct"] == pytest.approx(4.0, abs=0.1)
        assert result["direction"] == "UP"
        assert result["predicted_close_mean"] == pytest.approx(102.0, abs=0.1)
        assert result["predicted_close_final"] == 104.0
        assert result["volatility_proxy"] == pytest.approx(1.633, abs=0.01)
        assert result["prediction_uncertainty"]["path_dispersion"] is None
        assert result["prediction_uncertainty"]["confidence_score"] > 0

    def test_single_sample_down(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 97.0, 94.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0, sample_count=1)

        assert result["expected_change_pct"] == pytest.approx(-6.0, abs=0.1)
        assert result["direction"] == "DOWN"

    def test_single_sample_flat(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 100.5, 100.8]})
        result = runner._parse_pred_df(pred_df, last_close=100.0, sample_count=1)

        assert result["direction"] == "FLAT"

    def test_empty_pred_df_raises(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": []})
        with pytest.raises(RuntimeError, match="空预测"):
            runner._parse_pred_df(pred_df, last_close=100.0)

    def test_direction_score_bounds(self):
        """direction_score 应在 [0, 1] 区间内。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 150.0, 200.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0)
        dc = result["prediction_uncertainty"]["direction_score"]
        assert 0.0 <= dc <= 1.0

    def test_confidence_score_clamped_0_100(self):
        """confidence_score 应被 clamp 到 [0, 100]。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 50.0, 150.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0)
        cs = result["prediction_uncertainty"]["confidence_score"]
        assert 0.0 <= cs <= 100.0

    def test_multi_sample_path_dispersion(self):
        """多样本时应计算 path_dispersion（通过 build_result_dict 传入 stacked）。"""

        from trade_krono_cli.prediction_distribution import build_result_dict

        # 模拟 5 条不同路径
        stacked = np.array(
            [
                [100.0, 101.0, 102.0],
                [100.0, 100.5, 101.0],
                [100.0, 101.5, 103.0],
                [100.0, 99.5, 99.0],
                [100.0, 100.0, 100.5],
            ]
        )
        result = build_result_dict(
            stacked.mean(axis=0),
            last_close=100.0,
            stacked=stacked,
            sample_count=5,
        )
        assert result["prediction_uncertainty"]["path_dispersion"] is not None
        assert isinstance(result["prediction_uncertainty"]["path_dispersion"], float)
        # 验证百分位存在
        pd = result["prediction_distribution"]
        assert pd["p10"] is not None
        assert pd["p90"] is not None


class TestKronosRunnerPredDfToDict:
    """_pred_df_to_dict 测试。"""

    def _make_runner(self):
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        return KronosRunner(no_cache=True, sample_count=1, settings=settings)

    def test_basic(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame(
            {
                "open": [100, 101],
                "high": [102, 103],
                "low": [99, 100],
                "close": [101, 102],
                "volume": [1e6, 1.1e6],
            },
            index=pd.DatetimeIndex(["2026-08-11", "2026-08-12"]),
        )
        result = runner._pred_df_to_dict(pred_df)
        assert len(result["timestamps"]) == 2
        assert len(result["close"]) == 2
        assert result["close"][0] == 101.0
        assert result["close"][1] == 102.0

    def test_missing_columns_uses_defaults(self):
        """缺失列应使用默认值 0。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [101.0, 102.0]})
        result = runner._pred_df_to_dict(pred_df)
        assert result["open"] == [0.0]
        assert result["volume"] == [0.0]


class TestKronosRunnerApplyUncertainty:
    """_apply_uncertainty 测试。"""

    def _make_runner(self):
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        return KronosRunner(no_cache=True, sample_count=1, settings=settings)

    def test_applies_fields(self):
        runner = self._make_runner()
        res = MagicMock()
        res.prediction_uncertainty = None
        parsed = {
            "expected_change_pct": 2.5,
            "direction": "UP",
            "prediction_uncertainty": {
                "expected_return": 2.5,
                "direction": "UP",
                "direction_score": 0.8,
                "volatility": 1.0,
                "path_dispersion": None,
                "confidence_score": 80.0,
                "sample_count_used": 1,
            },
        }
        runner._apply_uncertainty(res, parsed)
        assert res.expected_change_pct == 2.5
        assert res.direction == "UP"
        assert res.prediction_uncertainty is not None
        assert res.prediction_uncertainty.confidence_score == 80.0
