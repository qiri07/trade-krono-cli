"""测试 orchestrator 模块 — PipelineFactory / _collect_futures / QuantPipeline 分支。"""

from __future__ import annotations

from concurrent.futures import Future
from unittest.mock import MagicMock

# ── _collect_futures ──────────────────────────────────────────────────────────


def test_collect_futures_both_success():
    """两个 Future 都成功时返回各自的结果。"""
    from trade_krono_cli.pipeline.orchestrator import _collect_futures

    ta_fut = Future()
    kr_fut = Future()
    ta_fut.set_result([MagicMock()])
    kr_fut.set_result([MagicMock()])
    ta_results, kr_results = _collect_futures(ta_fut, kr_fut)
    assert len(ta_results) == 1
    assert len(kr_results) == 1


def test_collect_futures_ta_exception():
    """TA Future 异常时降级为空列表，Kronos 正常返回。"""
    from trade_krono_cli.pipeline.orchestrator import _collect_futures

    ta_fut = Future()
    kr_fut = Future()
    ta_fut.set_exception(ValueError("network error"))
    kr_fut.set_result([MagicMock()])
    ta_results, kr_results = _collect_futures(ta_fut, kr_fut)
    assert ta_results == []
    assert len(kr_results) == 1


def test_collect_futures_kronos_exception():
    """Kronos Future 异常时降级为空列表，TA 正常返回。"""
    from trade_krono_cli.pipeline.orchestrator import _collect_futures

    ta_fut = Future()
    kr_fut = Future()
    ta_fut.set_result([MagicMock()])
    kr_fut.set_exception(RuntimeError("OOM"))
    ta_results, kr_results = _collect_futures(ta_fut, kr_fut)
    assert len(ta_results) == 1
    assert kr_results == []


def test_collect_futures_kronos_none():
    """kronos_future=None 时只返回 TA 结果。"""
    from trade_krono_cli.pipeline.orchestrator import _collect_futures

    ta_fut = Future()
    ta_fut.set_result([MagicMock()])
    ta_results, kr_results = _collect_futures(ta_fut, None)
    assert len(ta_results) == 1
    assert kr_results == []


def test_collect_futures_both_exception():
    """两个 Future 都异常时均降级为空列表。"""
    from trade_krono_cli.pipeline.orchestrator import _collect_futures

    ta_fut = Future()
    kr_fut = Future()
    ta_fut.set_exception(ValueError("timeout"))
    kr_fut.set_exception(RuntimeError("OOM"))
    ta_results, kr_results = _collect_futures(ta_fut, kr_fut)
    assert ta_results == []
    assert kr_results == []


# ── PipelineFactory.create ────────────────────────────────────────────────────


def test_factory_create_with_skip_kronos():
    """skip_kronos=True 时 kronos_session 应为 None。"""
    from trade_krono_cli.pipeline.orchestrator import PipelineFactory
    from trade_krono_cli.pipeline_config import PipelineConfig

    mock_ta = MagicMock()
    ta, kr = PipelineFactory.create(
        settings=MagicMock(),
        config=PipelineConfig.default(),
        skip_kronos=True,
        ta_session=mock_ta,
    )
    assert ta is mock_ta
    assert kr is None


def test_factory_create_with_both_sessions():
    """同时传入 ta_session 和 kronos_session 时应原样返回。"""
    from trade_krono_cli.pipeline.orchestrator import PipelineFactory
    from trade_krono_cli.pipeline_config import PipelineConfig

    mock_ta = MagicMock()
    mock_kr = MagicMock()
    ta, kr = PipelineFactory.create(
        settings=MagicMock(),
        config=PipelineConfig.default(),
        skip_kronos=False,
        ta_session=mock_ta,
        kronos_session=mock_kr,
    )
    assert ta is mock_ta
    assert kr is mock_kr


def test_factory_create_with_mock_runner():
    """传入非 session 的 runner 对象时应包装为 MagicMock。"""
    from trade_krono_cli.pipeline.orchestrator import PipelineFactory
    from trade_krono_cli.pipeline_config import PipelineConfig

    runner = MagicMock()
    runner.predict_batch = MagicMock()
    ta, kr = PipelineFactory.create(
        settings=MagicMock(),
        config=PipelineConfig.default(),
        skip_kronos=True,
        ta_session=runner,
    )
    # runner 被包装成带有 .runner 属性的 MagicMock
    assert hasattr(ta, "runner")
    assert ta.runner is runner


def test_factory_build_universe_engine_manual():
    """universe_source='manual' 时应返回 None。"""
    from trade_krono_cli.pipeline.orchestrator import PipelineFactory
    from trade_krono_cli.pipeline_config import PipelineConfig

    config = PipelineConfig.default()
    config.filters.universe_source = "manual"
    engine = PipelineFactory.build_universe_engine(config)
    assert engine is None


def test_factory_build_universe_engine_empty():
    """universe_source='' 时应返回 None。"""
    from trade_krono_cli.pipeline.orchestrator import PipelineFactory
    from trade_krono_cli.pipeline_config import PipelineConfig

    config = PipelineConfig.default()
    config.filters.universe_source = ""
    engine = PipelineFactory.build_universe_engine(config)
    assert engine is None


# ── QuantPipeline 初始化 ─────────────────────────────────────────────────────


def test_pipeline_init_skip_kronos():
    """skip_kronos=True 时不应创建 KronosSession。"""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig

    mock_ta = MagicMock()
    pipeline = QuantPipeline(
        ta_runner=mock_ta,
        skip_kronos=True,
        config=PipelineConfig.default(),
    )
    assert pipeline.kronos is None
    assert pipeline.ta is mock_ta


def test_pipeline_init_with_config():
    """使用自定义 config 初始化时应正确使用参数。"""
    from trade_krono_cli.pipeline import QuantPipeline
    from trade_krono_cli.pipeline_config import PipelineConfig

    config = PipelineConfig.default()
    config.min_confidence = 70.0
    config.allowed_signals = ("BUY",)
    mock_ta = MagicMock()
    pipeline = QuantPipeline(
        ta_runner=mock_ta,
        config=config,
        skip_kronos=True,
    )
    assert pipeline.min_confidence == 70.0
    assert pipeline.allowed_signals == ("BUY",)
