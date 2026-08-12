"""测试 KronosRunner 多采样行为（Phase 2）。"""
import pytest
from unittest.mock import MagicMock, patch
from trade_krono_cli.kronos_runner import KronosRunner, PredictionUncertainty


class TestKronosSampleCount:
    """验证 sample_count 参数传递与缓存 key 行为。"""

    def test_default_sample_count_in_settings(self):
        """Settings 默认 sample_count 应为 5。"""
        from trade_krono_cli.config import get_settings
        s = get_settings()
        assert s.kronos_sample_count == 5

    def test_runner_uses_default_sample_count(self):
        """KronosRunner 未传 sample_count 时使用 settings 默认值。"""
        from tests.conftest import make_mock_settings
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            settings = make_mock_settings(kronos_sample_count=5)
            runner = KronosRunner(no_cache=True, settings=settings)
            assert runner.sample_count == 5

    def test_runner_uses_explicit_sample_count(self):
        """KronosRunner 接受显式 sample_count 参数。"""
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=1)
            assert runner.sample_count == 1

    def test_runner_sample_count_ten(self):
        """sample_count=10 可正确设置。"""
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner = KronosRunner(no_cache=True, sample_count=10)
            assert runner.sample_count == 10

    def test_predict_one_calls_cache_with_sample_count(self):
        """predict_one 的缓存查询包含 sample_count。"""
        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            with patch.object(KronosRunner, "_prepare") as mock_prepare:
                mock_prepare.return_value = (
                    MagicMock(), MagicMock(), MagicMock(), 100.0
                )
                runner = KronosRunner(no_cache=False, sample_count=5)
                runner._cache = MagicMock()
                runner._cache.get_kronos.return_value = None

                with patch.object(runner, "_load"):
                    with patch.object(runner, "_predictor") as mock_pred:
                        import numpy as np
                        pred_df = MagicMock()
                        pred_df.__getitem__ = lambda self, key: (
                            np.array([101.0, 102.0, 103.0]) if key == "close" else MagicMock()
                        )
                        mock_pred.predict.return_value = pred_df

                        result = runner.predict_one("sh.600519", "2026-08-12")

                        # 验证缓存查询传入了 sample_count=5
                        runner._cache.get_kronos.assert_called_once()
                        call_args = runner._cache.get_kronos.call_args
                        assert call_args[0][3] == 5  # sample_count 参数

    def test_cache_key_differs_by_sample_count(self):
        """不同 sample_count 产生不同的缓存 key。"""
        import numpy as np
        import pandas as pd

        with patch("trade_krono_cli.kronos_runner.KronosRunner._load"):
            runner_1 = KronosRunner(no_cache=False, sample_count=1)
            runner_5 = KronosRunner(no_cache=False, sample_count=5)

            runner_1._cache = MagicMock()
            runner_5._cache = MagicMock()

            # 模拟缓存未命中
            runner_1._cache.get_kronos.return_value = None
            runner_5._cache.get_kronos.return_value = None

            # 创建真正的 DataFrame 作为预测结果
            pred_df_1 = pd.DataFrame({"close": [101.0, 102.0]})
            pred_df_5 = pd.DataFrame({"close": [101.0, 102.0]})

            with patch.object(runner_1, "_prepare") as mock_p1:
                with patch.object(runner_5, "_prepare") as mock_p2:
                    mock_p1.return_value = (MagicMock(), MagicMock(), MagicMock(), 100.0)
                    mock_p2.return_value = (MagicMock(), MagicMock(), MagicMock(), 100.0)

                    with patch.object(runner_1, "_predictor") as mock_pred1:
                        mock_pred1.predict.return_value = pred_df_1

                        with patch.object(runner_1, "_pred_df_to_dict") as mock_dict1:
                            mock_dict1.return_value = {"close": [101.0, 102.0]}
                            r1 = runner_1.predict_one("sh.600519", "2026-08-12")

                    with patch.object(runner_5, "_predictor") as mock_pred5:
                        # 5次采样都返回相同结果
                        mock_pred5.predict.return_value = pred_df_5

                        with patch.object(runner_5, "_pred_df_to_dict") as mock_dict5:
                            mock_dict5.return_value = {"close": [101.0, 102.0]}
                            r5 = runner_5.predict_one("sh.600519", "2026-08-12")

                    # 两次缓存写入应使用不同的 sample_count
                    write_calls_1 = runner_1._cache.set_kronos.call_args_list
                    write_calls_5 = runner_5._cache.set_kronos.call_args_list
                    assert len(write_calls_1) == 1
                    assert len(write_calls_5) == 1
                    assert write_calls_1[0][1]["sample_count"] == 1
                    assert write_calls_5[0][1]["sample_count"] == 5
