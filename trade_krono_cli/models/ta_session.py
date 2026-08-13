"""
ta_session — TradingAgents 模型资源管理。

职责边界（仅负责资源生命周期）：
  · LLM 密钥校验
  · 适配器懒加载（TradingAgentsAdapterImpl）
  · 配置构建

业务逻辑（分析执行 / 报告提取 / 决策解析 / 缓存读写）由 TradingAgentsRunner 负责。
"""
from __future__ import annotations

from typing import Optional, Any

from loguru import logger
from trade_krono_cli.ta_runner import TradingAgentsRunner


# ── 进程级单例缓存（同进程内相同配置的 session 复用，避免重复初始化 adapter）──
_SESSION_CACHE: dict[tuple, "TASession"] = {}


class TASession:
    """
    TradingAgents 模型资源会话。

    负责：
      - _validate_provider  ：检查 LLM 密钥是否可用
      - _get_adapter        ：懒加载 TradingAgentsAdapterImpl
      - is_loaded           ：graph 是否已初始化
      - adapter             ：返回适配器实例

    不负责：
      - 分析调度、报告提取、决策解析（这些在 TradingAgentsRunner 中）

    进程级单例：相同配置的调用会复用同一实例，避免重复初始化 adapter。
    """
    # 类级别缓存，key = (llm_provider,)
    _cache: dict[tuple, "TASession"] = {}

    def __new__(cls, *args, **kwargs):
        # 跳过显式传入 runner 的测试场景（不命中缓存）
        if kwargs.get("runner") is not None:
            return super().__new__(cls)
        key = (kwargs.get("llm_provider", None),)
        if key in cls._cache:
            return cls._cache[key]
        instance = super().__new__(cls)
        cls._cache[key] = instance
        return instance

    def __init__(
        self,
        llm_provider: Optional[str] = None,
        runner: Optional[TradingAgentsRunner] = None,
        no_cache: bool = False,
    ):
        # 跳过 __new__ 缓存路径下的重复初始化
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self._llm_provider = llm_provider
        self._runner = runner or TradingAgentsRunner(no_cache=no_cache)
        self._adapter: Optional[Any] = None
        self._initialized = False

    @classmethod
    def clear_cache(cls) -> None:
        """清除进程级单例缓存（测试或重启前调用）。"""
        cls._cache.clear()
        logger.debug("🧹 TASession: 单例缓存已清除")

    # ── 资源状态 ─────────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """adapter 是否已初始化。"""
        return self._adapter is not None

    @property
    def adapter(self) -> Any:
        """返回底层 adapter 实例。"""
        return self._get_adapter()

    @property
    def runner(self) -> TradingAgentsRunner:
        """返回底层 runner 实例（用于直接调用业务方法）。"""
        return self._runner

    # ── 资源管理 ─────────────────────────────────────────────────────────────

    def _validate_provider(self) -> None:
        """检查 LLM 密钥是否可用。"""
        from trade_krono_cli.security import KeyVault
        vault = KeyVault()
        available = vault.available_providers()
        if not available:
            raise RuntimeError(
                "❌ 未检测到任何 LLM API 密钥。请在 .env 中设置 "
                "DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY 之一"
            )
        if self._llm_provider and self._llm_provider not in available:
            logger.warning(
                f"⚠️  选定 provider '{self._llm_provider}' 无可用密钥，"
                f"回退到: {available[0]}"
            )

    def _get_adapter(self) -> Any:
        """懒加载 TradingAgentsAdapterImpl。"""
        if self._adapter is not None:
            return self._adapter
        self._adapter = self._runner._make_adapter()
        self._initialized = True
        logger.info("✅ TASession: TradingAgents adapter 已就绪")
        return self._adapter

    def ensure_loaded(self) -> None:
        """确保 adapter 已初始化。"""
        if self._initialized:
            return
        logger.info("🤖 TASession: 首次初始化 TradingAgents adapter...")
        self._get_adapter()
        logger.info("✅ TASession: adapter 已就绪")

    def unload(self) -> None:
        """释放 adapter 资源（测试或手动清理时使用）。"""
        self._adapter = None
        self._initialized = False
        logger.info("🗑️  TASession: adapter 资源已释放")
