"""测试 trade_krono_cli.models.kronos_session — Kronos 会话管理。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trade_krono_cli.models.kronos_session import KronosSession


class TestKronosSession:
    """KronosSession 基础功能测试。"""

    def setup_method(self) -> None:
        """每个测试前清理单例缓存。"""
        KronosSession.clear_cache()

    def test_init_without_runner(self) -> None:
        """无 runner 参数时，应创建默认 runner。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            MockRunner.return_value = mock_runner
            session = KronosSession()
            assert session._runner is mock_runner

    def test_init_with_runner(self) -> None:
        """传入 runner 时应直接使用。"""
        mock_runner = MagicMock()
        session = KronosSession(runner=mock_runner)
        assert session._runner is mock_runner

    def test_is_loaded_initially_false(self) -> None:
        """初始状态 predictor 应为 None。"""
        session = KronosSession()
        assert session.is_loaded is False

    def test_predictor_returns_none_when_not_loaded(self) -> None:
        session = KronosSession()
        assert session.predictor is None

    def test_clear_cache_clears_singleton(self) -> None:
        """清除缓存后，新实例应不同。"""
        session1 = KronosSession(no_cache=True)
        KronosSession.clear_cache()
        session2 = KronosSession(no_cache=True)
        assert session1 is not session2

    def test_unload_clears_predictor(self) -> None:
        """调用 unload 后 is_loaded 应为 False。"""
        session = KronosSession(no_cache=True)
        session.unload()
        assert session.is_loaded is False


class TestKronosSessionSingleton:
    """KronosSession 单例缓存测试。"""

    def test_same_config_returns_same_instance(self) -> None:
        """相同配置应返回同一实例。"""
        session1 = KronosSession(
            device="cpu",
            model_name="kronos-base",
            sample_count=5,
            T=1.0,
            top_p=0.9,
            lookback=400,
        )
        session2 = KronosSession(
            device="cpu",
            model_name="kronos-base",
            sample_count=5,
            T=1.0,
            top_p=0.9,
            lookback=400,
        )
        assert session1 is session2

    def test_different_device_returns_different_instance(self) -> None:
        """不同设备配置应返回不同实例。"""
        session1 = KronosSession(device="cpu", model_name="kronos-base")
        session2 = KronosSession(device="cuda", model_name="kronos-base")
        assert session1 is not session2

    def test_different_model_returns_different_instance(self) -> None:
        """不同模型应返回不同实例。"""
        session1 = KronosSession(model_name="kronos-base")
        session2 = KronosSession(model_name="kronos-mini")
        assert session1 is not session2

    def test_explicit_runner_bypasses_cache(self) -> None:
        """传入 runner 时应绕过缓存。"""
        mock_runner = MagicMock()
        session1 = KronosSession(runner=mock_runner)
        session2 = KronosSession(runner=mock_runner)
        assert session1 is not session2

    def test_default_kwargs_create_cache_key(self) -> None:
        """默认参数应正常缓存。"""
        session1 = KronosSession()
        session2 = KronosSession()
        assert session1 is session2

    def test_case_insensitive_device_in_key(self) -> None:
        """设备名称大小写不影响缓存 key。"""
        session1 = KronosSession(device="CPU")
        session2 = KronosSession(device="cpu")
        assert session1 is session2


class TestKronosSessionPrivateAttrs:
    """KronosSession 私有属性访问测试。"""

    def test_device_pref_property(self) -> None:
        session = KronosSession(device="cuda:0")
        assert session._device_pref == "cuda:0"

    def test_model_name_private_attr(self) -> None:
        session = KronosSession(model_name="kronos-mini")
        assert session._model_name == "kronos-mini"

    def test_sample_count_private_attr(self) -> None:
        session = KronosSession(sample_count=10)
        assert session._sample_count == 10

    def test_device_property_returns_cpu_by_default(self) -> None:
        session = KronosSession(no_cache=True)
        assert session.device == "cpu"

    def test_runner_property(self) -> None:
        mock_runner = MagicMock()
        session = KronosSession(no_cache=True, runner=mock_runner)
        assert session.runner is mock_runner
