"""测试 PredictionUncertainty / KronosForecastResult 数据类。"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


class TestKronosRunnerPredictOneErrorPaths:
    """predict_one 错误路径测试。"""

    def test_cache_hit_returns_fast(self):
        """缓存命中时应直接返回，不调用预测。"""
        from trade_krono_cli.kronos_runner import KronosRunner

        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False, sample_count=1)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519",
                "eval_date": "2026-08-12",
                "horizon": 30,
                "interval": "d",
                "expected_change_pct": 2.0,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0,
                    "direction": "UP",
                    "direction_score": 0.8,
                    "volatility": 1.0,
                    "path_dispersion": None,
                    "confidence_score": 80.0,
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
                "ticker": "sh.600519",
                "eval_date": "2026-08-12",
                "horizon": 30,
                "expected_change_pct": 1.5,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 1.5,
                    "direction": "UP",
                    "direction_score": 0.7,
                    "volatility": 0.5,
                    "path_dispersion": None,
                    "confidence_score": 70.0,
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
                mock_prepare.return_value = (MagicMock(), MagicMock(), MagicMock(), 100.0)
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
                "ticker": "sh.600519",
                "eval_date": "2026-08-12",
                "horizon": 30,
                "expected_change_pct": 2.0,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0,
                    "direction": "UP",
                    "direction_score": 0.8,
                    "volatility": 1.0,
                    "path_dispersion": None,
                    "confidence_score": 80.0,
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
            mock_prepare.return_value = (MagicMock(), MagicMock(), MagicMock(), 100.0)
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
                    (
                        long_df,
                        self._make_mock_ts(400),
                        pd.date_range("2026-08-13", periods=30, freq="B"),
                        100.0,
                    ),
                    (
                        short_df,
                        self._make_mock_ts(300),
                        pd.date_range("2026-08-13", periods=30, freq="B"),
                        100.0,
                    ),
                ]
                mock_adapter = MagicMock()
                pred_df = pd.DataFrame({"close": [101.0, 102.0]})
                mock_adapter.predict_batch.return_value = [pred_df, pred_df]
                mock_session = MagicMock()
                mock_session.adapter = mock_adapter
                runner._session = mock_session
                with patch.object(runner, "_pred_df_to_dict") as mock_dict:
                    mock_dict.return_value = {"close": [101.0, 102.0]}
                    results = runner.predict_batch(["sh.600519", "sz.000001"], "2026-08-12")
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
                    results = runner.predict_batch(["sh.600519", "sz.000001"], "2026-08-12")
                    assert len(results) == 2
                    assert all(r.error is None for r in results)

    def test_predict_batch_handles_all_cached(self):
        """全部缓存命中时直接返回，不调用模型。"""
        from trade_krono_cli.kronos_runner import KronosRunner

        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=False, sample_count=1, batch_size=8)
            runner._cache = MagicMock()
            runner._cache.get_kronos.return_value = {
                "ticker": "sh.600519",
                "eval_date": "2026-08-12",
                "horizon": 30,
                "expected_change_pct": 2.0,
                "direction": "UP",
                "prediction_uncertainty": {
                    "expected_return": 2.0,
                    "direction": "UP",
                    "direction_score": 0.8,
                    "volatility": 1.0,
                    "path_dispersion": None,
                    "confidence_score": 80.0,
                    "sample_count_used": 1,
                },
            }
            results = runner.predict_batch(["sh.600519"], "2026-08-12")
            assert len(results) == 1
            assert results[0].expected_change_pct == 2.0
            assert results[0].error is None


