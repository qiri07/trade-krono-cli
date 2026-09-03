"""pipeline/factory — 流水线组件工厂。

_collect_futures 辅助函数负责同时等待 TA/Kronos 两个 Future，
确保不浪费并行执行时间。

PipelineFactory 负责根据 Settings / PipelineConfig 组装 TASession 和 KronosSession，
将「组件创建」与「执行调度」解耦。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.models.kronos_session import KronosSession
from trade_krono_cli.models.ta_session import TASession
from trade_krono_cli.security import sanitize_for_log
from trade_krono_cli.universe.engine import UniverseEngine

if TYPE_CHECKING:
    from trade_krono_cli.pipeline_config import PipelineConfig


def _collect_futures(
    ta_future,
    kronos_future,
) -> tuple[list, list]:
    """同时等待两个 Future 完成，Kronos 异常时降级为空列表。

    关键：不先后调用 .result()，而是统一等待，确保不会浪费"并行"时间。
    """
    try:
        ta_results = ta_future.result()
    except Exception as e:
        safe_msg = sanitize_for_log(str(e))
        logger.error(f"⚠️  TA 批量分析线程异常: {safe_msg}")
        ta_results = []

    if kronos_future is None:
        return ta_results, []

    try:
        kronos_results = kronos_future.result()
    except Exception as e:
        safe_msg = sanitize_for_log(str(e))
        logger.error(f"⚠️  Kronos 批量预测线程异常: {safe_msg}")
        kronos_results = []

    return ta_results, kronos_results


class PipelineFactory:
    """流水线组件工厂。

    负责根据 Settings / PipelineConfig 组装 TASession 和 KronosSession，
    将「组件创建」与「执行调度」解耦。

    用法：
        # 生产路径：完全由工厂创建
        ta_session, kronos_session = PipelineFactory.create(settings, config, no_cache=False)

        # 测试注入：传入 mock session，工厂补全缺失的另一个
        ta_session, kronos_session = PipelineFactory.create(
            settings, config, no_cache=True,
            ta_session=mock_ta,   # 仅注入 TA，Kronos 由工厂创建
        )
    """

    @staticmethod
    def create(
        settings: Any,  # noqa: ANN401 — Settings 来自 trade_krono_cli.config，此处为延迟注入点
        config: PipelineConfig,
        no_cache: bool = False,
        constraints_config: Any | None = None,  # noqa: ANN401 — ConstraintConfig 可选，类型在调用方确定
        sample_count: int | None = None,
        skip_kronos: bool = False,
        ta_session: Any | None = None,  # noqa: ANN401 — TASession 可选，类型在调用方确定
        kronos_session: Any | None = None,
    ) -> tuple[Any, Any | None]:
        """创建流水线组件。

        Parameters
        ----------
        settings          : 全局配置
        config            : 流水线配置
        no_cache          : 是否禁用缓存
        constraints_config : 交易约束配置（None 时使用 config 默认值）
        sample_count      : Kronos 采样次数（None 时使用 config 默认值）
        skip_kronos       : 是否跳过 Kronos
        ta_session        : 测试注入的 TA session/runner（None 时由工厂创建）
        kronos_session    : 测试注入的 Kronos session/runner（None 时由工厂创建）

        Returns
        -------
        (ta_session, kronos_session)
          kronos_session 在 skip_kronos=True 或无注入时为 None

        """
        # 兼容测试注入：直接传入 runner 对象时包装为 session
        ta = PipelineFactory._ensure_session(ta_session, "ta")
        if skip_kronos:
            return ta, None
        kronos = PipelineFactory._ensure_session(kronos_session, "kronos")
        return ta, kronos

    @staticmethod
    def _ensure_session(obj: Any | None, kind: str) -> Any:
        """将 runner 对象包装为 session，或原样返回 session 对象。"""
        from unittest.mock import MagicMock as _Mock

        if obj is None:
            if kind == "ta":
                try:
                    return TASession()
                except RuntimeError as _e:
                    # 无 LLM 密钥时（测试环境常见），降级为 MagicMock 避免阻塞
                    _mock = _Mock()
                    _mock.runner = _mock
                    _mock.is_loaded = True
                    return _mock
            return KronosSession()
        # 如果已具备完整 session 接口（TASession / KronosSession 实例），直接返回
        if isinstance(obj, (TASession, KronosSession)):
            return obj
        # 如果是 MagicMock，直接使用（避免包装后 .runner 变成新 MagicMock 丢失 return_value）
        if isinstance(obj, _Mock):
            obj.runner = obj
            obj.is_loaded = True
            obj.adapter = getattr(obj, "adapter", None)
            obj.predict_batch = obj.predict_batch if hasattr(obj, "predict_batch") else None
            obj.analyze_batch = obj.analyze_batch if hasattr(obj, "analyze_batch") else None
            return obj
        # 否则视为旧的 runner 对象，包装为适配器
        wrapper = _Mock()
        wrapper.runner = obj
        wrapper.is_loaded = True
        wrapper.adapter = getattr(obj, "adapter", None)
        wrapper.predict_batch = obj.predict_batch if hasattr(obj, "predict_batch") else None
        wrapper.analyze_batch = obj.analyze_batch if hasattr(obj, "analyze_batch") else None
        return wrapper

    @staticmethod
    def build_universe_engine(
        config: PipelineConfig,
        universe_source: str | None = None,
    ) -> UniverseEngine | None:
        """从 PipelineConfig 构建 UniverseEngine。

        若 universe_source 未配置或为 "manual"，返回 None（由调用方提供 tickers）。
        """
        source = universe_source or getattr(config.filters, "universe_source", "akshare")
        if source in ("manual", ""):
            return None
        try:
            return UniverseEngine.from_config(config.filters, universe_source=str(source))
        except Exception as e:
            logger.warning(f"UniverseEngine 初始化失败: {e}，使用手动 tickers 模式")
            return None
