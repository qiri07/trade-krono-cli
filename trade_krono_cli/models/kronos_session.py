"""
kronos_session — Kronos 模型资源管理。

职责边界（仅负责资源生命周期）：
  · 设备判断（CUDA 回退 CPU）
  · 模型懒加载（首次预测时才加载到显存）
  · 适配器初始化与缓存

业务逻辑（数据准备 / 预测执行 / 结果解析 / 缓存读写）由 KronosRunner 负责。
"""
from __future__ import annotations

from typing import Optional, Any

from loguru import logger
from trade_krono_cli.kronos_runner import KronosRunner
from trade_krono_cli.errors import ModelLoadError
from trade_krono_cli.security import ensure_import_path


# ── 进程级单例缓存（同进程内相同配置的 session 复用，避免重复加载模型）────────
_SESSION_CACHE: dict[tuple, "KronosSession"] = {}


class KronosSession:
    """
    Kronos 模型资源会话。

    负责：
      - _resolve_device  ：判断 CUDA 是否可用，回退 CPU
      - _load            ：懒加载模型到显存/CPU
      - _get_adapter     ：懒加载 KronosAdapterImpl
      - is_loaded        ：模型是否已加载

    不负责：
      - 数据准备、预测调度、结果解析（这些在 KronosRunner 中）

    进程级单例：相同 (device, model_name, sample_count) 配置的调用会复用同一实例，
    避免多次 run() 时重复加载模型到显存。
    """
    # 类级别缓存，key = (device, model_name, sample_count, T, top_p, lookback)
    _cache: dict[tuple, "KronosSession"] = _SESSION_CACHE

    def __new__(cls, *args, **kwargs):
        # 跳过显式传入 runner 的测试场景（不命中缓存）
        if kwargs.get("runner") is not None:
            return super().__new__(cls)
        # 计算缓存 key（纳入所有影响推理行为的参数）
        device = kwargs.get("device", "cpu")
        model_name = kwargs.get("model_name", "kronos-base")
        sample_count = kwargs.get("sample_count", None)
        # T / top_p / lookback 直接影响适配器行为，纳入 key
        T = kwargs.get("T", None)
        top_p = kwargs.get("top_p", None)
        lookback = kwargs.get("lookback", None)
        key = (device.lower(), model_name, sample_count, T, top_p, lookback)
        if key in cls._cache:
            return cls._cache[key]
        instance = super().__new__(cls)
        cls._cache[key] = instance
        return instance

    def __init__(
        self,
        device: Optional[str] = None,
        model_name: Optional[str] = None,
        sample_count: Optional[int] = None,
        runner: Optional[KronosRunner] = None,
        no_cache: bool = False,
        T: Optional[float] = None,
        top_p: Optional[float] = None,
        lookback: Optional[int] = None,
    ):
        # 跳过 __new__ 缓存路径下的重复初始化（同一实例已初始化过）
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._device_pref = (device or "cpu").lower()
        self._model_name = model_name or "kronos-base"
        self._sample_count = sample_count
        self._runner = runner or KronosRunner(
            session=self,
            no_cache=no_cache,
            sample_count=sample_count,
        )
        self._kronos_adapter: Optional[Any] = None
        self._predictor: Optional[Any] = None
        self._device: str = "cpu"
        self._max_context: int = 512
        self._loaded = False

    # ── 资源状态 ─────────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """模型是否已加载到内存。"""
        return self._predictor is not None

    @property
    def device(self) -> str:
        """当前实际使用的设备（cpu / cuda:0 等）。"""
        return self._device

    @property
    def predictor(self) -> Optional[Any]:
        """暴露内部预测器（测试 / 高级场景使用）。"""
        return self._predictor

    @property
    def adapter(self) -> Any:
        """返回适配器实例。"""
        return self._get_adapter()

    @property
    def runner(self) -> KronosRunner:
        """返回底层 runner 实例（用于直接调用业务方法）。"""
        return self._runner

    # ── 设备管理 ─────────────────────────────────────────────────────────────

    def _resolve_device(self) -> str:
        """根据设备偏好返回实际可用设备（CUDA 回退 CPU）。"""
        if self._device_pref.startswith("cuda"):
            try:
                import torch
                if torch.cuda.is_available():
                    return self._device_pref
                logger.warning("⚠️  CUDA 不可用，回退到 CPU")
            except ImportError:
                pass
        return "cpu"

    # ── 适配器懒加载 ─────────────────────────────────────────────────────────

    def _get_adapter(self) -> Any:
        """懒加载 KronosAdapterImpl。"""
        if self._kronos_adapter is None:
            from trade_krono_cli.adapters import KronosAdapterImpl
            self._kronos_adapter = KronosAdapterImpl()
        return self._kronos_adapter

    # ── 模型加载 ─────────────────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """确保模型已加载（懒加载）。"""
        if self._loaded:
            return
        logger.info("🧠 KronosSession: 首次加载模型...")
        self._load()
        self._loaded = True
        logger.info("✅ KronosSession: 模型已就绪")

    def _load(self) -> None:
        """懒加载 Kronos 模型（通过适配器层）。"""
        if self._predictor is not None:
            return

        settings = self._runner._settings_obj
        adapter = self._get_adapter()
        adapter.load_model(settings)

        self._predictor = adapter.predictor
        self._device = adapter.device if adapter else "cpu"
        self._max_context = adapter._max_context if adapter else 512

        logger.info(
            f"✅ Kronos 模型加载完成 (device={self._device})"
        )

    def unload(self) -> None:
        """释放模型资源（测试或手动清理时使用）。"""
        self._predictor = None
        self._loaded = False
        self._kronos_adapter = None
        logger.info("🗑️  KronosSession: 模型资源已释放")

    @classmethod
    def clear_cache(cls) -> None:
        """清除进程级单例缓存（测试或重启前调用）。"""
        cls._cache.clear()
        logger.debug("🧹 KronosSession: 单例缓存已清除")
