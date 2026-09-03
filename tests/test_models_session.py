"""测试模型常驻会话（Phase 4）— 验证资源管理职责已迁移到 Session 层。"""

from unittest.mock import MagicMock, patch

import pytest


class TestKronosSession:
    """KronosSession 单元测试 — 资源管理层。"""

    def _make_mock_runner(self):
        """创建模拟 runner，避免真实构造依赖 settings。"""
        from trade_krono_cli.kronos_runner import KronosRunner

        mock_runner = MagicMock(spec=KronosRunner)
        mock_runner._settings_obj = MagicMock()
        mock_runner._settings_obj.kronos_model = "kronos-base"
        mock_runner._settings_obj.kronos_pred_len = 30
        return mock_runner

    def test_default_init_creates_runner(self) -> None:
        """默认初始化创建 KronosRunner。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = self._make_mock_runner()
            MockRunner.return_value = mock_runner
            session = KronosSession()
            MockRunner.assert_called_once()
            assert session.runner is mock_runner

    def test_is_loaded_false_by_default(self) -> None:
        """默认状态下模型未加载。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession()
            assert session.is_loaded is False

    def test_ensure_loaded_triggers_load(self) -> None:
        """ensure_loaded 触发模型加载（首次）。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosSession._load") as mock_load:
            with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
                session = KronosSession()
            session.ensure_loaded()
            mock_load.assert_called_once()

    def test_ensure_loaded_noop_when_already_loaded(self) -> None:
        """已加载时 ensure_loaded 是 no-op。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosSession._load") as mock_load:
            with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
                session = KronosSession()
            session._loaded = True
            session.ensure_loaded()
            session.ensure_loaded()  # 第二次调用
            mock_load.assert_not_called()

    def test_resolve_device_cpu(self) -> None:
        """CPU 设备应直接返回 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cpu")
        assert session._resolve_device() == "cpu"

    def test_resolve_device_cuda_no_torch(self) -> None:
        """无 torch 时 cuda 回退到 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda:0")
        with patch.dict("sys.modules", {"torch": None}):
            result = session._resolve_device()
        assert result == "cpu"

    def test_resolve_device_cuda_available(self) -> None:
        """torch.cuda.is_available() 为 True 时返回 cuda 设备。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda:0")
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = session._resolve_device()
        assert result == "cuda:0"

    def test_get_adapter_lazy_loads(self) -> None:
        """_get_adapter 懒加载适配器。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.adapters.KronosAdapterImpl") as MockAdapter:
            mock_adapter = MagicMock()
            MockAdapter.return_value = mock_adapter
            with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
                session = KronosSession()
            adapter = session._get_adapter()
            assert adapter is mock_adapter
            MockAdapter.assert_called_once()
            adapter2 = session._get_adapter()
            assert adapter2 is adapter
            assert MockAdapter.call_count == 1

    def test_unload_clears_state(self) -> None:
        """Unload 应清除所有内部状态。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession()
        session._predictor = MagicMock()
        session._kronos_adapter = MagicMock()
        session._loaded = True
        session.unload()
        assert session._predictor is None
        assert session._kronos_adapter is None
        assert session._loaded is False

    def test_predict_batch_delegates_to_runner(self) -> None:
        """预测批量方法委托给 runner（通过 session.runner）。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = self._make_mock_runner()
            mock_runner.predict_batch.return_value = []
            MockRunner.return_value = mock_runner
            session = KronosSession()
            session.runner.predict_batch(["sh.600519"], "2026-08-12")
            mock_runner.predict_batch.assert_called_once()


class TestTASession:
    """TASession 单元测试 — 资源管理层。"""

    def _make_mock_runner(self):
        from trade_krono_cli.ta_runner import TradingAgentsRunner

        return MagicMock(spec=TradingAgentsRunner)

    def test_default_init_creates_runner(self) -> None:
        """默认初始化创建 TradingAgentsRunner。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = self._make_mock_runner()
            MockRunner.return_value = mock_runner
            session = TASession()
            MockRunner.assert_called_once()
            assert session.runner is mock_runner

    def test_is_loaded_false_by_default(self) -> None:
        """默认状态下 adapter 未初始化。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
            session = TASession()
            assert session.is_loaded is False

    def test_ensure_loaded_calls_get_adapter(self) -> None:
        """ensure_loaded 触发 adapter 初始化。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TASession._get_adapter") as mock_get:
            with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
                session = TASession()
            session.ensure_loaded()
            mock_get.assert_called_once()

    def test_ensure_loaded_noop_when_already_loaded(self) -> None:
        """已初始化时 ensure_loaded 是 no-op。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TASession._get_adapter") as mock_get:
            with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
                session = TASession()
            session._initialized = True
            session.ensure_loaded()
            session.ensure_loaded()  # 第二次调用
            assert mock_get.call_count == 0

    def test_validate_provider_no_keys_raises(self) -> None:
        """无 LLM 密钥时应抛出 RuntimeError。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
            session = TASession()
        with patch("trade_krono_cli.security.KeyVault") as mock_vault_cls:
            mock_vault = MagicMock()
            mock_vault.available_providers.return_value = []
            mock_vault_cls.return_value = mock_vault
            with pytest.raises(RuntimeError, match="未检测到任何 LLM API 密钥"):
                session._validate_provider()

    def test_validate_provider_with_keys_passes(self) -> None:
        """有密钥时应正常通过。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
            session = TASession(llm_provider="deepseek")
        with patch("trade_krono_cli.security.KeyVault") as mock_vault_cls:
            mock_vault = MagicMock()
            mock_vault.available_providers.return_value = ["deepseek"]
            mock_vault_cls.return_value = mock_vault
            session._validate_provider()

    def test_get_adapter_lazy_loads(self) -> None:
        """_get_adapter 懒加载 adapter。"""
        from trade_krono_cli.models.ta_session import TASession

        mock_adapter = MagicMock()
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._make_adapter.return_value = mock_adapter
            MockRunner.return_value = mock_runner
            session = TASession()
        adapter = session._get_adapter()
        # _get_adapter 调用 runner._make_adapter()，后者创建 TradingAgentsAdapterImpl
        # 断言调用已发生且 adapter 被缓存
        assert adapter is mock_adapter
        assert session._adapter is adapter
        mock_runner._make_adapter.assert_called_once()
        # 第二次调用应返回缓存的 adapter
        adapter2 = session._get_adapter()
        assert adapter2 is adapter
        assert mock_runner._make_adapter.call_count == 1

    def test_unload_clears_state(self) -> None:
        """Unload 应清除所有内部状态。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner"):
            session = TASession()
        session._adapter = MagicMock()
        session._initialized = True
        session.unload()
        assert session._adapter is None
        assert session._initialized is False

    def test_analyze_batch_delegates_to_runner(self) -> None:
        """analyze_batch 委托给 runner（通过 session.runner）。"""
        from trade_krono_cli.models.ta_session import TASession

        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = self._make_mock_runner()
            mock_runner.analyze_batch.return_value = []
            MockRunner.return_value = mock_runner
            session = TASession()
            session.runner.analyze_batch(["sh.600519"], "2026-08-12")
            mock_runner.analyze_batch.assert_called_once()


class TestKronosSessionSingleton:
    """KronosSession 进程级单例缓存测试。"""

    def _patch_kronos_runner(self):
        """Patch KronosRunner at the module level where KronosSession imports it."""
        return patch(
            "trade_krono_cli.models.kronos_session.KronosRunner",
            return_value=MagicMock(),
        )

    def test_same_config_reuses_instance(self) -> None:
        """相同配置的多次构造应返回同一实例。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with self._patch_kronos_runner():
            s1 = KronosSession(device="cpu", model_name="kronos-base")
            s2 = KronosSession(device="cpu", model_name="kronos-base")
        assert s1 is s2
        # 清理
        KronosSession.clear_cache()

    def test_different_config_creates_new_instance(self) -> None:
        """不同 device 应创建新实例。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with self._patch_kronos_runner():
            s1 = KronosSession(device="cpu")
            s2 = KronosSession(device="cuda")
        assert s1 is not s2
        KronosSession.clear_cache()

    def test_explicit_runner_skips_cache(self) -> None:
        """显式传入 runner 时应跳过缓存，每次创建新实例。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        mock_runner = MagicMock()
        with self._patch_kronos_runner():
            s1 = KronosSession(runner=mock_runner)
            s2 = KronosSession(runner=mock_runner)
        assert s1 is not s2
        KronosSession.clear_cache()

    def test_clear_cache_resets_state(self) -> None:
        """clear_cache 后相同配置应创建新实例。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with self._patch_kronos_runner():
            s1 = KronosSession(device="cpu")
            s2 = KronosSession(device="cpu")
        assert s1 is s2
        KronosSession.clear_cache()
        with self._patch_kronos_runner():
            s3 = KronosSession(device="cpu")
        assert s3 is not s1


class TestTASessionSingleton:
    """TASession 进程级单例缓存测试。"""

    def _patch_ta_runner(self):
        return patch(
            "trade_krono_cli.models.ta_session.TradingAgentsRunner",
            return_value=MagicMock(),
        )

    def test_same_config_reuses_instance(self) -> None:
        """相同配置的多次构造应返回同一实例。"""
        from trade_krono_cli.models.ta_session import TASession

        with self._patch_ta_runner():
            s1 = TASession(llm_provider="deepseek")
            s2 = TASession(llm_provider="deepseek")
        assert s1 is s2
        TASession.clear_cache()

    def test_different_provider_creates_new_instance(self) -> None:
        """不同 provider 应创建新实例。"""
        from trade_krono_cli.models.ta_session import TASession

        with self._patch_ta_runner():
            s1 = TASession(llm_provider="deepseek")
            s2 = TASession(llm_provider="openai")
        assert s1 is not s2
        TASession.clear_cache()

    def test_explicit_runner_skips_cache(self) -> None:
        """显式传入 runner 时应跳过缓存，每次创建新实例。"""
        from trade_krono_cli.models.ta_session import TASession

        mock_runner = MagicMock()
        with self._patch_ta_runner():
            s1 = TASession(runner=mock_runner)
            s2 = TASession(runner=mock_runner)
        assert s1 is not s2
        TASession.clear_cache()

    def test_clear_cache_resets_state(self) -> None:
        """clear_cache 后相同配置应创建新实例。"""
        from trade_krono_cli.models.ta_session import TASession

        with self._patch_ta_runner():
            s1 = TASession(llm_provider="deepseek")
            s2 = TASession(llm_provider="deepseek")
        assert s1 is s2
        TASession.clear_cache()
        with self._patch_ta_runner():
            s3 = TASession(llm_provider="deepseek")
        assert s3 is not s1
