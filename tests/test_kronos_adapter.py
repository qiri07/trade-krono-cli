"""测试 KronosAdapterImpl 适配器实现。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestKronosAdapterImpl:
    """KronosAdapterImpl 核心功能测试。"""

    def _make_adapter(self):
        """创建适配器实例。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        return KronosAdapterImpl()

    def test_init_default_values(self) -> None:
        """初始化时应设置默认值。"""
        adapter = self._make_adapter()
        assert adapter._predictor is None
        assert adapter._device == "cpu"
        assert adapter._max_context == 512

    def test_device_property_returns_cpu_by_default(self) -> None:
        """device 属性应返回 'cpu'。"""
        adapter = self._make_adapter()
        assert adapter.device == "cpu"

    def test_predictor_property_returns_none_when_not_loaded(self) -> None:
        """模型未加载时 predictor 应返回 None。"""
        adapter = self._make_adapter()
        assert adapter.predictor is None

    def test_resolve_device_cpu_directly(self) -> None:
        """CPU 设备应直接返回。"""
        adapter = self._make_adapter()
        result = adapter._resolve_device("cpu")
        assert result == "cpu"

    def test_resolve_device_cuda_without_torch(self) -> None:
        """CUDA 设备在无 torch 时应回退到 CPU。"""
        adapter = self._make_adapter()
        with patch.dict("sys.modules", {"torch": None}):
            result = adapter._resolve_device("cuda:0")
            assert result == "cpu"

    def test_resolve_device_cuda_with_import_error(self) -> None:
        """CUDA 设备在导入 torch 失败时应回退到 CPU。"""
        adapter = self._make_adapter()
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            result = adapter._resolve_device("cuda:0")
            assert result == "cpu"

    def test_load_model_already_loaded_returns_early(self) -> None:
        """模型已加载时应直接返回。"""
        adapter = self._make_adapter()
        adapter._predictor = MagicMock()

        # 不应调用任何外部导入
        with patch("trade_krono_cli.adapters.kronos.ensure_import_path") as mock_path:
            adapter.load_model(MagicMock())
            mock_path.assert_not_called()

    def test_load_model_import_error_raises(self) -> None:
        """导入失败时应抛出 ModelLoadError。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl
        from trade_krono_cli.errors import ModelLoadError

        adapter = KronosAdapterImpl()
        mock_settings = MagicMock()
        mock_settings.kronos_root = Path("/tmp/fake_kronos")
        mock_settings.kronos_device = "cpu"
        mock_settings.kronos_model = "kronos-base"

        # 创建一个假的 cli_anything.kronos 模块
        fake_module = MagicMock()
        fake_module.load_model.side_effect = ImportError("no module")

        with (
            patch("trade_krono_cli.adapters.kronos.ensure_import_path"),
            patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.kronos": MagicMock(),
                    "cli_anything.kronos.utils": MagicMock(),
                    "cli_anything.kronos.utils.kronos_backend": fake_module,
                },
            ),
            pytest.raises(ModelLoadError, match="cli_anything.kronos"),
        ):
            adapter.load_model(mock_settings)


class TestKronosAdapterPredict:
    """predict / predict_batch 方法测试。"""

    def test_predict_without_model_raises(self) -> None:
        """模型未加载时 predict 应抛出 RuntimeError。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        adapter = KronosAdapterImpl()
        with pytest.raises(RuntimeError, match="尚未加载"):
            adapter.predict(
                df=MagicMock(),
                x_timestamp=MagicMock(),
                y_timestamp=MagicMock(),
                pred_len=30,
                T=1.0,
                top_p=0.9,
            )

    def test_predict_calls_predictor(self) -> None:
        """模型加载后 predict 应调用内部预测器。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        adapter = KronosAdapterImpl()
        mock_predictor = MagicMock()
        mock_predictor.predict.return_value = MagicMock()
        adapter._predictor = mock_predictor

        adapter.predict(
            df=MagicMock(),
            x_timestamp="x_ts",
            y_timestamp="y_ts",
            pred_len=30,
            T=1.0,
            top_p=0.9,
            sample_count=5,
        )

        # 验证 predict 被调用，参数正确
        mock_predictor.predict.assert_called_once()
        call_kwargs = mock_predictor.predict.call_args[1]
        assert call_kwargs["pred_len"] == 30
        assert call_kwargs["T"] == 1.0
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["sample_count"] == 5
        assert call_kwargs["verbose"] is False

    def test_predict_batch_without_model_raises(self) -> None:
        """模型未加载时 predict_batch 应抛出 RuntimeError。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        adapter = KronosAdapterImpl()
        with pytest.raises(RuntimeError, match="尚未加载"):
            adapter.predict_batch(
                df_list=[],
                x_timestamp_list=[],
                y_timestamp_list=[],
                pred_len=30,
                T=1.0,
                top_p=0.9,
            )

    def test_predict_batch_calls_predictor(self) -> None:
        """模型加载后 predict_batch 应调用内部预测器的批量方法。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        adapter = KronosAdapterImpl()
        mock_predictor = MagicMock()
        mock_predictor.predict_batch.return_value = [MagicMock()]
        adapter._predictor = mock_predictor

        result = adapter.predict_batch(
            df_list=[MagicMock()],
            x_timestamp_list=["x"],
            y_timestamp_list=["y"],
            pred_len=30,
            T=1.0,
            top_p=0.9,
            sample_count=3,
        )

        # 验证 predict_batch 被调用
        mock_predictor.predict_batch.assert_called_once()
        call_kwargs = mock_predictor.predict_batch.call_args[1]
        assert call_kwargs["pred_len"] == 30
        assert call_kwargs["T"] == 1.0
        assert call_kwargs["verbose"] is False
        assert len(result) == 1

    def test_predict_batch_passes_verbose_true(self) -> None:
        """显式传入 verbose=True 时应透传给内部预测器。"""
        from trade_krono_cli.adapters.kronos import KronosAdapterImpl

        adapter = KronosAdapterImpl()
        mock_predictor = MagicMock()
        mock_predictor.predict_batch.return_value = [MagicMock()]
        adapter._predictor = mock_predictor

        adapter.predict_batch(
            df_list=[MagicMock()],
            x_timestamp_list=["x"],
            y_timestamp_list=["y"],
            pred_len=30,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=True,
        )

        call_kwargs = mock_predictor.predict_batch.call_args[1]
        assert call_kwargs["verbose"] is True


class TestKronosPredictorBatchLoad:
    """KronosPredictor.predict_batch 批量推理前应确保模型已加载。"""

    def test_predict_batch_loads_model_before_inference(self) -> None:
        from unittest.mock import MagicMock

        from trade_krono_cli.kronos_predictor.predictor import KronosPredictor

        predictor = KronosPredictor.__new__(KronosPredictor)
        predictor._settings = MagicMock(kronos_pred_len=30, kronos_T=1.0, kronos_top_p=0.9)
        predictor.use_cache = False
        predictor.sample_count = 1
        predictor.batch_size = 8
        predictor._cache = None
        predictor._config_hash = "h"
        predictor._model_version = "v"

        runner = MagicMock()
        runner._load = MagicMock()
        runner._adapter = MagicMock()
        runner._adapter.predict_batch.return_value = [MagicMock()]
        predictor._runner = runner

        import pandas as pd
        n = 400
        df = pd.DataFrame({c: [1.0] * n for c in ["open", "high", "low", "close", "volume", "amount"]})
        predictor._data_prep = MagicMock()
        predictor._data_prep.prepare.return_value = (
            df,
            pd.Series(range(n)),
            pd.date_range("2026-08-13", periods=30, freq="B"),
            100.0,
        )
        predictor._data_prep.split_batches = staticmethod(
            lambda items, bs: [items[i : i + bs] for i in range(0, len(items), bs)]
        )

        predictor._result_parser = MagicMock()
        predictor._result_parser.create_empty_result.side_effect = lambda **k: type(
            "R", (), {**k, "error": None}
        )()
        predictor._result_parser.parse_batch_result.side_effect = lambda res, pred_df, lc, sc: (
            setattr(res, "expected_change_pct", 1.0) or res
        )

        results = predictor.predict_batch(["sh.600519"], "2026-08-12")
        assert runner._load.called
        assert len(results) == 1
        assert results[0].error is None
