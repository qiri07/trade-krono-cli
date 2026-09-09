"""测试 trade_krono_cli.models.ta_session — TA 会话管理。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trade_krono_cli.models.ta_session import TASession


class TestTASession:
    """TASession 基础功能测试。"""

    def setup_method(self) -> None:
        """每个测试前清理单例缓存。"""
        TASession.clear_cache()

    def test_init_without_runner(self) -> None:
        """无 runner 参数时，应创建默认 runner。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            MockRunner.return_value = mock_runner
            session = TASession()
            assert session._runner is mock_runner

    def test_init_with_runner(self) -> None:
        """传入 runner 时应直接使用。"""
        mock_runner = MagicMock()
        session = TASession(runner=mock_runner)
        assert session._runner is mock_runner

    def test_is_loaded_initially_false(self) -> None:
        """初始状态 adapter 应为 None。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            MockRunner.return_value = MagicMock()
            session = TASession()
        assert session.is_loaded is False

    def test_clear_cache_clears_singleton(self) -> None:
        """清除缓存后，新实例应不同。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            MockRunner.return_value = MagicMock()
            session1 = TASession(no_cache=True)
            TASession.clear_cache()
            session2 = TASession(no_cache=True)
        assert session1 is not session2

    def test_adapter_lazy_loads_on_access(self) -> None:
        """访问 adapter 属性时应触发懒加载。"""
        with (
            patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner,
            patch.object(TASession, "_get_adapter") as mock_get,
        ):
            MockRunner.return_value = MagicMock()
            mock_get.return_value = MagicMock()
            session = TASession(no_cache=True)
            _ = session.adapter  # 触发懒加载
            mock_get.assert_called_once()

    def test_unload_clears_adapter(self) -> None:
        """调用 unload 后 is_loaded 应为 False。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            MockRunner.return_value = MagicMock()
            session = TASession(no_cache=True)
            session.unload()
        assert session.is_loaded is False


class TestTASessionSingleton:
    """TASession 单例缓存测试。"""

    def test_explicit_runner_bypasses_cache(self) -> None:
        """传入 runner 时应绕过缓存。"""
        mock_runner = MagicMock()
        session1 = TASession(runner=mock_runner)
        session2 = TASession(runner=mock_runner)
        assert session1 is not session2

    def test_default_kwargs_create_cache_key(self) -> None:
        """默认参数应正常缓存。"""
        session1 = TASession()
        session2 = TASession()
        assert session1 is session2


class TestTASessionPrivateAttrs:
    """TASession 私有属性访问测试。"""

    def test_llm_provider_private_attr(self) -> None:
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            MockRunner.return_value = MagicMock()
            session = TASession(no_cache=True, llm_provider="deepseek-chat")
        assert session._llm_provider == "deepseek-chat"

    def test_runner_property(self) -> None:
        mock_runner = MagicMock()
        session = TASession(no_cache=True, runner=mock_runner)
        assert session.runner is mock_runner
