"""测试 PredictionUncertainty / KronosForecastResult 数据类及 KronosRunner 内部方法。"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch


class TestPredictionUncertainty:
    """PredictionUncertainty 序列化/反序列化测试。"""

    def test_to_dict(self):
        from trade_krono_cli.kronos_runner import PredictionUncertainty
        pu = PredictionUncertainty(
            expected_return=3.2,
            direction="UP",
            direction_confidence=0.85,
            volatility=1.23,
            path_dispersion=0.045,
            confidence_score=78.5,
            sample_count_used=5,
        )
        d = pu.to_dict()
        assert d["expected_return"] == 3.2
        assert d["direction"] == "UP"
        assert d["confidence_score"] == 78.5
        assert d["sample_count_used"] == 5

    def test_from_dict(self):
        from trade_krono_cli.kronos_runner import PredictionUncertainty
        d = {
            "expected_return": -2.1,
            "direction": "DOWN",
            "direction_confidence": 0.6,
            "volatility": 0.8,
            "path_dispersion": 0.02,
            "confidence_score": 55.0,
            "sample_count_used": 3,
        }
        pu = PredictionUncertainty.from_dict(d)
        assert pu.expected_return == -2.1
        assert pu.direction == "DOWN"
        assert pu.confidence_score == 55.0

    def test_from_dict_ignores_extra_fields(self):
        """多余字段应被忽略。"""
        from trade_krono_cli.kronos_runner import PredictionUncertainty
        d = {
            "expected_return": 1.0,
            "direction": "UP",
            "direction_confidence": 0.5,
            "volatility": 0.1,
            "path_dispersion": None,
            "confidence_score": 50.0,
            "sample_count_used": 1,
            "extra_field": "should_be_ignored",
        }
        pu = PredictionUncertainty.from_dict(d)
        assert pu.expected_return == 1.0
        assert not hasattr(pu, "extra_field") or pu.__dict__.get("extra_field") is None


class TestKronosForecastResult:
    """KronosForecastResult 序列化测试。"""

    def test_to_dict_with_uncertainty(self):
        from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty
        pu = PredictionUncertainty(expected_return=2.0, direction="UP", confidence_score=70.0)
        r = KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-12", horizon=30,
            expected_change_pct=2.0, direction="UP",
            prediction_uncertainty=pu,
        )
        d = r.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["expected_change_pct"] == 2.0
        assert d["prediction_uncertainty"]["confidence_score"] == 70.0

    def test_to_dict_without_uncertainty(self):
        from trade_krono_cli.kronos_runner import KronosForecastResult
        r = KronosForecastResult(
            ticker="sh.600519", eval_date="2026-08-12", horizon=30,
        )
        d = r.to_dict()
        assert d["prediction_uncertainty"] is None


class TestKronosRunnerParsePredDf:
    """_parse_pred_df 核心预测解析逻辑。"""

    def _make_runner(self):
        """创建真实 KronosRunner 实例（跳过模型加载和 settings 依赖）。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.get_settings") as mock_settings:
            mock_settings.return_value.kronos_model = "kronos-base"
            mock_settings.return_value.kronos_tokenizer = "kronos-base"
            mock_settings.return_value.kronos_device = "cpu"
            mock_settings.return_value.kronos_lookback = 400
            mock_settings.return_value.kronos_pred_len = 30
            mock_settings.return_value.kronos_sample_count = 1
            mock_settings.return_value.kronos_T = 1.0
            mock_settings.return_value.kronos_top_p = 0.9
            mock_settings.return_value.kronos_use_sample_confidence = False
            return KronosRunner(no_cache=True, sample_count=1)

    def test_single_sample_up(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 102.0, 104.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0, sample_count=1)

        assert result["expected_change_pct"] == pytest.approx(4.0, abs=0.1)
        assert result["direction"] == "UP"
        assert result["predicted_close_mean"] == pytest.approx(102.0, abs=0.1)
        assert result["predicted_close_final"] == 104.0
        assert result["volatility_proxy"] == pytest.approx(1.633, abs=0.01)
        # 单样本：path_dispersion 应为 None
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

        assert result["direction"] == "FLAT"  # < 1% change

    def test_empty_pred_df_raises(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": []})
        with pytest.raises(RuntimeError, match="空预测"):
            runner._parse_pred_df(pred_df, last_close=100.0)

    def test_direction_confidence_bounds(self):
        """direction_confidence 应在 [0, 1] 区间内。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 150.0, 200.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0)
        dc = result["prediction_uncertainty"]["direction_confidence"]
        assert 0.0 <= dc <= 1.0

    def test_confidence_score_clamped_0_100(self):
        """confidence_score 应被 clamp 到 [0, 100]。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 50.0, 150.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0)
        cs = result["prediction_uncertainty"]["confidence_score"]
        assert 0.0 <= cs <= 100.0

    def test_multi_sample_path_dispersion(self):
        """多样本时应计算 path_dispersion。"""
        runner = self._make_runner()
        pred_df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
        result = runner._parse_pred_df(pred_df, last_close=100.0, sample_count=5)
        # 多样本时 path_dispersion 应是一个数值而非 None
        assert result["prediction_uncertainty"]["path_dispersion"] is not None
        assert isinstance(result["prediction_uncertainty"]["path_dispersion"], float)


class TestKronosRunnerPredDfToDict:
    """_pred_df_to_dict 测试。"""

    def _make_runner(self):
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.get_settings") as mock_settings:
            s = mock_settings.return_value
            s.kronos_model = "kronos-base"
            s.kronos_tokenizer = "kronos-base"
            s.kronos_device = "cpu"
            s.kronos_lookback = 400
            s.kronos_pred_len = 30
            s.kronos_sample_count = 1
            s.kronos_T = 1.0
            s.kronos_top_p = 0.9
            s.kronos_use_sample_confidence = False
            return KronosRunner(no_cache=True, sample_count=1)

    def test_basic(self):
        runner = self._make_runner()
        pred_df = pd.DataFrame(
            {"open": [100, 101], "high": [102, 103], "low": [99, 100],
             "close": [101, 102], "volume": [1e6, 1.1e6]},
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
        # 注意：pd.Series(0).tolist() 只返回 [0.0]（单元素），不是 [0.0, 0.0]
        assert result["open"] == [0.0]
        assert result["volume"] == [0.0]


class TestKronosRunnerApplyUncertainty:
    """_apply_uncertainty 测试。"""

    def _make_runner(self):
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.get_settings") as mock_settings:
            s = mock_settings.return_value
            s.kronos_model = "kronos-base"
            s.kronos_tokenizer = "kronos-base"
            s.kronos_device = "cpu"
            s.kronos_lookback = 400
            s.kronos_pred_len = 30
            s.kronos_sample_count = 1
            s.kronos_T = 1.0
            s.kronos_top_p = 0.9
            s.kronos_use_sample_confidence = False
            return KronosRunner(no_cache=True, sample_count=1)

    def test_applies_fields(self):
        runner = self._make_runner()
        res = MagicMock()
        res.prediction_uncertainty = None
        parsed = {
            "expected_change_pct": 2.5,
            "direction": "UP",
            "prediction_uncertainty": {
                "expected_return": 2.5, "direction": "UP",
                "direction_confidence": 0.8, "volatility": 1.0,
                "path_dispersion": None, "confidence_score": 80.0,
                "sample_count_used": 1,
            },
        }
        runner._apply_uncertainty(res, parsed)
        assert res.expected_change_pct == 2.5
        assert res.direction == "UP"
        assert res.prediction_uncertainty is not None
        assert res.prediction_uncertainty.confidence_score == 80.0


class TestKronosRunnerPredictOneErrorPaths:
    """predict_one 错误路径测试。"""

    def test_cache_hit_returns_fast(self):
        """缓存命中时应直接返回，不调用预测。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False, sample_count=1)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519", "eval_date": "2026-08-12",
                "horizon": 30, "interval": "d",
                "expected_change_pct": 2.0, "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0, "direction": "UP",
                    "direction_confidence": 0.8, "volatility": 1.0,
                    "path_dispersion": None, "confidence_score": 80.0,
                    "sample_count_used": 1,
                },
            }
            result = runner.predict_one("sh.600519", "2026-08-12")
            assert result.expected_change_pct == 2.0
            assert result.error is None
            assert result.elapsed_sec == 0.0

    def test_cache_hit_restores_uncertainty(self):
        """缓存命中时应正确还原 PredictionUncertainty 对象。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519", "eval_date": "2026-08-12",
                "horizon": 30,
                "expected_change_pct": 1.5, "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 1.5, "direction": "UP",
                    "direction_confidence": 0.7, "volatility": 0.5,
                    "path_dispersion": None, "confidence_score": 70.0,
                    "sample_count_used": 1,
                },
            }
            result = runner.predict_one("sh.600519", "2026-08-12")
            assert result.prediction_uncertainty is not None
            assert isinstance(result.prediction_uncertainty.expected_return, float)

    def test_prepare_data_too_short(self):
        """数据不足时应设置 error 字段。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, lookback=400, sample_count=1)
            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.side_effect = RuntimeError("数据不足")
                result = runner.predict_one("sh.600519", "2026-08-12")
                assert result.error is not None
                assert "RuntimeError" in result.error

    def test_predictor_error_sets_result_error(self):
        """预测器抛异常时应捕获并记录。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1)
            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    MagicMock(), MagicMock(), MagicMock(), 100.0
                )
                runner._predictor = MagicMock()
                runner._predictor.predict.side_effect = RuntimeError("GPU OOM")
                result = runner.predict_one("sh.600519", "2026-08-12")
                assert result.error is not None
                assert "RuntimeError" in result.error


class TestKronosRunnerPredictBatch:
    """predict_batch 测试。"""

    def test_all_cached(self):
        """全部缓存命中时直接返回。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False, sample_count=1)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519", "eval_date": "2026-08-12",
                "horizon": 30, "expected_change_pct": 2.0,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0, "direction": "UP",
                    "direction_confidence": 0.8, "volatility": 1.0,
                    "path_dispersion": None, "confidence_score": 80.0,
                    "sample_count_used": 1,
                },
            }
            results = runner.predict_batch(["sh.600519"], "2026-08-12")
            assert len(results) == 1
            assert results[0].expected_change_pct == 2.0
            assert results[0].error is None

    def test_batch_fallback_to_single_on_failure(self):
        """批量预测失败时应降级为逐只预测。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1)

            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    MagicMock(), MagicMock(), MagicMock(), 100.0
                )
                runner._predictor = MagicMock()
                runner._predictor.predict_batch.side_effect = RuntimeError("batch failed")
                runner._predictor.predict.return_value = pd.DataFrame({"close": [102.0]})

                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [102.0]}
                    results = runner.predict_batch(["sh.600519"], "2026-08-12")
                    assert len(results) == 1
                    assert results[0].error is None

    def test_batch_all_prepare_fails(self):
        """所有股票数据准备失败时返回空结果。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1)

            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.side_effect = RuntimeError("no data")
                results = runner.predict_batch(["sh.600519"], "2026-08-12")
                assert len(results) == 1
                assert results[0].error is not None
