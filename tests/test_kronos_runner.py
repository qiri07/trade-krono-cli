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
            direction_score=0.85,
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
            "direction_score": 0.6,
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
            "direction_score": 0.5,
            "volatility": 0.1,
            "path_dispersion": None,
            "confidence_score": 50.0,
            "sample_count_used": 1,
            "extra_field": "should_be_ignored",
        }
        pu = PredictionUncertainty.from_dict(d)
        assert pu.expected_return == 1.0
        assert not hasattr(pu, "extra_field") or pu.__dict__.get("extra_field") is None

    def test_percentile_fields(self):
        """PredictionDistribution 应包含 p10/p25/p50/p75/p90 分位数字段。"""
        from trade_krono_cli.kronos_runner import PredictionDistribution
        pd_obj = PredictionDistribution(
            expected_return=2.0, direction="UP", confidence_score=72.0,
            p10=1700.0, p25=1750.0, p50=1800.0, p75=1850.0, p90=1900.0,
        )
        d = pd_obj.to_dict()
        assert d["p10"] == 1700.0
        assert d["p25"] == 1750.0
        assert d["p50"] == 1800.0
        assert d["p75"] == 1850.0
        assert d["p90"] == 1900.0
        restored = PredictionDistribution.from_dict(d)
        assert restored.p50 == 1800.0
        assert restored.p90 == 1900.0

    def test_percentiles_default_to_none(self):
        """未设置百分位时应为 None。"""
        from trade_krono_cli.kronos_runner import PredictionDistribution
        pd_obj = PredictionDistribution(expected_return=1.0, direction="UP")
        assert pd_obj.p10 is None
        assert pd_obj.p50 is None
        assert pd_obj.p90 is None


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
        from tests.conftest import make_mock_settings
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
        import numpy as np
        # 模拟 5 条不同路径
        stacked = np.array([
            [100.0, 101.0, 102.0],
            [100.0, 100.5, 101.0],
            [100.0, 101.5, 103.0],
            [100.0, 99.5,  99.0],
            [100.0, 100.0, 100.5],
        ])
        result = build_result_dict(
            stacked.mean(axis=0), last_close=100.0,
            stacked=stacked, sample_count=5,
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
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings
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
        assert result["open"] == [0.0]
        assert result["volume"] == [0.0]


class TestKronosRunnerApplyUncertainty:
    """_apply_uncertainty 测试。"""

    def _make_runner(self):
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings
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
                "expected_return": 2.5, "direction": "UP",
                "direction_score": 0.8, "volatility": 1.0,
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
                    "direction_score": 0.8, "volatility": 1.0,
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
                    "direction_score": 0.7, "volatility": 0.5,
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
            runner = KronosRunner(no_cache=True, sample_count=1)
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
                mock_adapter = MagicMock()
                mock_adapter.predict.side_effect = RuntimeError("GPU OOM")
                # 通过 patch session 的 adapter 来避免 _adapter property 报错
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
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
                    "direction_score": 0.8, "volatility": 1.0,
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
        runner = KronosRunner(no_cache=True, sample_count=1)

        with patch.object(runner, "_prepare") as mock_prepare:
            mock_prepare.return_value = (
                MagicMock(), MagicMock(), MagicMock(), 100.0
            )
            mock_adapter = MagicMock()
            mock_adapter.predict_batch.side_effect = RuntimeError("batch failed")
            mock_adapter.predict.return_value = pd.DataFrame({"close": [102.0]})
            mock_session = MagicMock()
            mock_session.adapter = mock_adapter
            runner._session = mock_session
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


class TestKronosBatchInference:
    """分批推理 + padding + per-batch fallback 测试。"""

    def _make_mock_df(self, rows: int = 400) -> pd.DataFrame:
        """创建含 6 列的 mock DataFrame（长度 rows）。"""
        return pd.DataFrame(
            np.random.rand(rows, 6).astype(np.float32),
            columns=["open", "high", "low", "close", "volume", "amount"],
        )

    def _make_mock_ts(self, rows: int = 400) -> pd.Series:
        return pd.date_range("2025-01-01", periods=rows, freq="B")

    def test_batch_size_from_settings(self):
        """batch_size 应从 settings 读取。"""
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner
        settings = make_mock_settings(kronos_batch_size=16)
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)
            assert runner.batch_size == 16

    def test_default_batch_size_is_8(self):
        """默认 batch_size 应为 8。"""
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner
        settings = make_mock_settings()
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, settings=settings)
            assert runner.batch_size == 8

    def test_pad_df_shorter_than_target(self):
        """短 DataFrame 应被 padding 到 target_len。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        df = pd.DataFrame({"a": [1, 2]})
        padded = KronosRunner._pad_df_to_length(df, 5)
        assert len(padded) == 5
        # 最后一行重复填充
        assert padded["a"].tolist() == [1, 2, 2, 2, 2]

    def test_pad_df_longer_than_target(self):
        """长 DataFrame 应截断到 target_len。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
        padded = KronosRunner._pad_df_to_length(df, 3)
        assert len(padded) == 3
        assert padded["a"].tolist() == [3, 4, 5]

    def test_pad_df_exact_length(self):
        """长度相同时不做修改。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        df = pd.DataFrame({"a": [1, 2, 3]})
        padded = KronosRunner._pad_df_to_length(df, 3)
        assert len(padded) == 3
        assert padded["a"].tolist() == [1, 2, 3]

    def test_split_batches_basic(self):
        """基本分批逻辑。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        items = list(range(7))
        batches = KronosRunner._split_batches(items, 3)
        assert batches == [[0, 1, 2], [3, 4, 5], [6]]

    def test_split_batches_exact(self):
        """整除时批次大小均匀。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        items = list(range(6))
        batches = KronosRunner._split_batches(items, 3)
        assert batches == [[0, 1, 2], [3, 4, 5]]

    def test_split_batches_larger_than_items(self):
        """batch_size 大于 items 数量时只有一批。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        items = [1, 2]
        batches = KronosRunner._split_batches(items, 10)
        assert batches == [[1, 2]]

    def test_predict_batch_sends_single_batch_when_within_limit(self):
        """股票数 <= batch_size 时只发一批。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1, batch_size=8)
            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    self._make_mock_df(400),
                    self._make_mock_ts(400),
                    pd.date_range("2026-08-13", periods=30, freq="B"),
                    100.0,
                )
                mock_adapter = MagicMock()
                pred_df = pd.DataFrame({"close": [101.0, 102.0]})
                mock_adapter.predict_batch.return_value = [pred_df]
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [101.0, 102.0]}
                    results = runner.predict_batch(["sh.600519"], "2026-08-12")
                    assert len(results) == 1
                    assert results[0].error is None
                    # predict_batch 应被调用 1 次
                    mock_adapter.predict_batch.assert_called_once()

    def test_predict_batch_splits_into_multiple_batches(self):
        """股票数 > batch_size 时应拆分为多批。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1, batch_size=2)
            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    self._make_mock_df(400),
                    self._make_mock_ts(400),
                    pd.date_range("2026-08-13", periods=30, freq="B"),
                    100.0,
                )
                mock_adapter = MagicMock()
                pred_df = pd.DataFrame({"close": [101.0, 102.0]})
                # 每批返回对应数量的预测结果（批次1有2只，批次2有1只）
                mock_adapter.predict_batch.side_effect = [
                    [pred_df, pred_df],
                    [pred_df],
                ]
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [101.0, 102.0]}
                    results = runner.predict_batch(
                        ["sh.600519", "sz.000001", "sh.601318"], "2026-08-12"
                    )
                    assert len(results) == 3
                    # 3 只股票，batch_size=2 → 2 批
                    assert mock_adapter.predict_batch.call_count == 2

    def test_predict_batch_pads_shorter_series(self):
        """较短序列应被 padding 到与同批最长序列相同的长度。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1, batch_size=4)
            with patch.object(runner, "_prepare") as mock_prepare:
                # 第一只股票有 400 行，第二只有 300 行
                long_df = self._make_mock_df(400)
                short_df = self._make_mock_df(300)
                mock_prepare.side_effect = [
                    (long_df, self._make_mock_ts(400),
                     pd.date_range("2026-08-13", periods=30, freq="B"), 100.0),
                    (short_df, self._make_mock_ts(300),
                     pd.date_range("2026-08-13", periods=30, freq="B"), 100.0),
                ]
                mock_adapter = MagicMock()
                pred_df = pd.DataFrame({"close": [101.0, 102.0]})
                mock_adapter.predict_batch.return_value = [pred_df, pred_df]
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [101.0, 102.0]}
                    results = runner.predict_batch(
                        ["sh.600519", "sz.000001"], "2026-08-12"
                    )
                    assert len(results) == 2
                    # 验证传入 predict_batch 的 df 都已 padding 到 400
                    call_args = mock_adapter.predict_batch.call_args
                    padded_dfs = call_args[1]["df_list"]
                    assert all(len(df) == 400 for df in padded_dfs)

    def test_predict_batch_single_batch_success(self):
        """单批成功时直接返回结果。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1, batch_size=8)
            with patch.object(runner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    self._make_mock_df(400),
                    self._make_mock_ts(400),
                    pd.date_range("2026-08-13", periods=30, freq="B"),
                    100.0,
                )
                mock_adapter = MagicMock()
                pred_df = pd.DataFrame({"close": [101.0, 102.0]})
                mock_adapter.predict_batch.return_value = [pred_df, pred_df]
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [101.0, 102.0]}
                    results = runner.predict_batch(
                        ["sh.600519", "sz.000001"], "2026-08-12"
                    )
                    assert len(results) == 2
                    assert all(r.error is None for r in results)

    def test_predict_batch_handles_all_cached(self):
        """全部缓存命中时直接返回，不调用模型。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False, sample_count=1, batch_size=8)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519", "eval_date": "2026-08-12",
                "horizon": 30, "expected_change_pct": 2.0,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0, "direction": "UP",
                    "direction_score": 0.8, "volatility": 1.0,
                    "path_dispersion": None, "confidence_score": 80.0,
                    "sample_count_used": 1,
                },
            }
            results = runner.predict_batch(["sh.600519"], "2026-08-12")
            assert len(results) == 1
            assert results[0].expected_change_pct == 2.0
            assert results[0].error is None


class TestKronosRunnerResolveDevice:
    """_resolve_device 测试（由 KronosSession 负责）。"""

    def test_cpu_device(self):
        from trade_krono_cli.models.kronos_session import KronosSession
        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cpu")
        assert session._resolve_device() == "cpu"

    def test_cuda_device_no_torch(self):
        """无 torch 时 cuda 回退到 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession
        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        with patch.dict("sys.modules", {"torch": None}):
            result = session._resolve_device()
        assert result == "cpu"

    def test_cuda_with_torch_not_available(self):
        """torch.cuda.is_available() 为 False 时回退到 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession
        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = session._resolve_device()
        assert result == "cpu"

    def test_cuda_with_torch_available(self):
        """torch.cuda.is_available() 为 True 时返回 cuda。"""
        from trade_krono_cli.models.kronos_session import KronosSession
        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = session._resolve_device()
        assert result == "cuda"

    def test_large_model_warning(self):
        """large 模型名应触发警告并切换为 base。"""
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings
        with patch("trade_krono_cli.kronos_runner.logger.warning"):
            settings = make_mock_settings(
                kronos_model="kronos-large",
                kronos_tokenizer="kronos-base",
                kronos_device="cpu",
                kronos_lookback=400,
                kronos_pred_len=30,
                kronos_sample_count=1,
                kronos_T=1.0,
                kronos_top_p=0.9,
                kronos_use_sample_confidence=False,
            )
            runner = KronosRunner(no_cache=True, settings=settings)
        assert runner.model_name == "kronos-base"


    def test_prepare_uses_eval_date_not_last_data_date(self):
        """
        _prepare 的 future 日期应从 eval_date 起算，而非数据末尾日期。
        """
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings
        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=5,
            kronos_pred_len=3,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        runner = KronosRunner(no_cache=True, settings=settings)

        import pandas as pd
        mock_df = pd.DataFrame({
            "timestamps": pd.to_datetime([
                "2026-07-27", "2026-07-28", "2026-07-29",
                "2026-07-30", "2026-07-31",
            ]),
            "open":   [100.0] * 5,
            "high":   [101.0] * 5,
            "low":    [99.0]  * 5,
            "close":  [100.0] * 5,
            "volume": [1e6]   * 5,
            "amount": [1e8]   * 5,
        })
        with patch("trade_krono_cli.kronos_runner.fetch_lookback", return_value=mock_df):
            x_df, x_ts, y_ts, last_close = runner._prepare("sh.600519", "2026-08-11")

        assert str(y_ts.iloc[0]) == "2026-08-12 00:00:00"
        assert len(y_ts) == 3
        assert last_close == 100.0

    def test_prepare_raises_on_suspended_stock(self):
        """
        当 fetch_lookback 抛出数据过旧异常（停牌超过阈值），
        _prepare 应将异常传播出去，阻止预测。
        """
        from trade_krono_cli.kronos_runner import KronosRunner
        from tests.conftest import make_mock_settings
        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=5,
            kronos_pred_len=3,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        runner = KronosRunner(no_cache=True, settings=settings)

        with patch("trade_krono_cli.kronos_runner.fetch_lookback") as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "数据过旧: sh.600519 最后交易日 2026-06-05 与评估日 2026-08-11 "
                "相差 34 个交易日（阈值 10），疑似停牌或退市"
            )
            with pytest.raises(RuntimeError, match="数据过旧|疑似停牌"):
                runner._prepare("sh.600519", "2026-08-11")


class TestKronosRunnerSaveResults:
    """save_results 测试。"""

    def test_saves_json(self, tmp_path):
        from trade_krono_cli.kronos_runner import KronosRunner, KronosForecastResult
        from tests.conftest import make_mock_settings
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
        runner = KronosRunner(no_cache=True, settings=settings)

        results = [
            KronosForecastResult(ticker="sh.600519", eval_date="2026-08-12", horizon=30, direction="UP", expected_change_pct=2.0),
            KronosForecastResult(ticker="sz.000858", eval_date="2026-08-12", horizon=30, direction="DOWN", expected_change_pct=-1.5),
        ]
        path = str(tmp_path / "kronos_out.json")
        returned = runner.save_results(results, path)
        assert returned == path
        import json
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]["ticker"] == "sh.600519"
