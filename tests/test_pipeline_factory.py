"""trade_krono_cli.pipeline.factory 单元测试。

覆盖 PipelineFactory.create / _ensure_session / build_universe_engine 及
_collect_futures 异常降级。TASession/KronosSession 通过 unittest.mock 注入，
不触发真实 LLM 或模型加载。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from trade_krono_cli.pipeline.factory import PipelineFactory, _collect_futures


def _make_config(**overrides) -> SimpleNamespace:
    filters = SimpleNamespace(universe_source="manual")
    defaults = {"filters": filters}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_settings(**overrides) -> SimpleNamespace:
    defaults = {"cache_dir": "/tmp/test-cache"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCollectFutures:
    """_collect_futures 同时等待两个 Future，异常降级。"""

    def test_both_succeed(self) -> None:
        ta_future = MagicMock()
        ta_future.result.return_value = ["ta1", "ta2"]
        kronos_future = MagicMock()
        kronos_future.result.return_value = ["k1"]

        ta, kronos = _collect_futures(ta_future, kronos_future)
        assert ta == ["ta1", "ta2"]
        assert kronos == ["k1"]

    def test_ta_exception_degrades_to_empty(self) -> None:
        ta_future = MagicMock()
        ta_future.result.side_effect = RuntimeError("ta down")
        kronos_future = MagicMock()
        kronos_future.result.return_value = ["k1"]

        ta, kronos = _collect_futures(ta_future, kronos_future)
        assert ta == []
        assert kronos == ["k1"]

    def test_kronos_none(self) -> None:
        ta_future = MagicMock()
        ta_future.result.return_value = ["ta1"]
        ta, kronos = _collect_futures(ta_future, None)
        assert ta == ["ta1"]
        assert kronos == []

    def test_kronos_exception_degrades_to_empty(self) -> None:
        ta_future = MagicMock()
        ta_future.result.return_value = ["ta1"]
        kronos_future = MagicMock()
        kronos_future.result.side_effect = RuntimeError("kronos down")

        ta, kronos = _collect_futures(ta_future, kronos_future)
        assert ta == ["ta1"]
        assert kronos == []


class TestPipelineFactoryCreate:
    """PipelineFactory.create 与 _ensure_session 测试。"""

    def test_create_skip_kronos(self) -> None:
        settings = _make_settings()
        config = _make_config()
        ta_session = MagicMock()
        ta, kronos = PipelineFactory.create(
            settings, config, no_cache=True, skip_kronos=True, ta_session=ta_session
        )
        assert ta is ta_session
        assert kronos is None

    def test_create_ta_session_passed_through(self) -> None:
        settings = _make_settings()
        config = _make_config()
        ta_session = MagicMock()
        ta, kronos = PipelineFactory.create(
            settings, config, no_cache=True, skip_kronos=True, ta_session=ta_session
        )
        assert ta is ta_session

    def test_create_magicmock_runner_wrapped(self) -> None:
        settings = _make_settings()
        config = _make_config()
        # 测试环境：PYTEST_CURRENT_TEST 存在，TASession 失败时降级为 MagicMock
        import os

        os.environ["PYTEST_CURRENT_TEST"] = "1"
        try:
            ta, kronos = PipelineFactory.create(
                settings, config, no_cache=True, skip_kronos=True
            )
            assert ta is not None
        finally:
            del os.environ["PYTEST_CURRENT_TEST"]

    def test_build_universe_engine_manual_returns_none(self) -> None:
        config = _make_config(filters=SimpleNamespace(universe_source="manual"))
        assert PipelineFactory.build_universe_engine(config) is None

    def test_build_universe_engine_empty_source_returns_none(self) -> None:
        config = _make_config(filters=SimpleNamespace(universe_source=""))
        assert PipelineFactory.build_universe_engine(config) is None

    def test_build_universe_engine_unknown_source_logs_and_none(self) -> None:
        config = _make_config(filters=SimpleNamespace(universe_source="akshare"))
        # UniverseEngine.from_config 不存在时会抛 AttributeError，工厂应捕获并返回 None
        engine = PipelineFactory.build_universe_engine(config)
        assert engine is None
