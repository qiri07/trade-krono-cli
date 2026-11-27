"""trade_krono_cli.data_providers.factory_providers — Provider 实例生命周期管理。

职责（单一职责原则）：
  · Provider 懒加载与进程级缓存（线程安全）
  · 批量获取可用 Provider
  · Provider 类注册与动态导入
  · 可用性检查与全员健康检查
  · 缓存重置（测试隔离用）
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.data_providers.base import DataProvider


class ProviderInstanceManager:
    """Provider 实例管理器：懒加载、缓存、类注册、健康检查。"""

    # 进程级缓存（线程安全）
    _instance_cache: dict[str, DataProvider] = {}
    _cache_lock = threading.Lock()

    # Provider 类注册表：名称 → 类
    _PROVIDER_REGISTRY: dict[str, type[DataProvider]] = {}

    def get_provider(self, name: str) -> DataProvider | None:
        """获取指定名称的 Provider 实例（进程级缓存）。

        Returns
        -------
        DataProvider | None
            成功返回实例，失败（未安装依赖 / 未配置 key）返回 None
        """
        with self._cache_lock:
            if name in self._instance_cache:
                return self._instance_cache[name]

        cls = self._get_provider_class(name)
        if cls is None:
            return None

        try:
            instance = cls()
        except RuntimeError as e:
            logger.debug(f"Provider {name} 初始化失败: {e}")
            return None
        except ImportError as e:
            logger.debug(f"Provider {name} 依赖未安装: {e}")
            return None

        with self._cache_lock:
            self._instance_cache[name] = instance

        logger.debug(f"✅ Provider {name} 初始化成功")
        return instance

    def get_providers(self, names: list[str] | None = None) -> list[DataProvider]:
        """批量获取 Provider 实例，过滤掉不可用的。

        Parameters
        ----------
        names : list[str] | None
            Provider 名称列表，缺省使用主源 + 降级链。

        Returns
        -------
        list[DataProvider]
            可用的 Provider 列表（按 names 顺序）
        """
        # 子类或调用方需提供 provider_chain
        chain = getattr(self, "provider_chain", []) or []
        names = names or chain
        result: list[DataProvider] = []
        for name in names:
            p = self.get_provider(name)
            if p is not None:
                result.append(p)
        return result

    def available_providers(self) -> list[str]:
        """返回当前已初始化的可用 Provider 名称列表."""
        chain = getattr(self, "provider_chain", []) or []
        return [name for name in chain if self.get_provider(name) is not None]

    def health_check_all(self) -> dict[str, bool]:
        """检查所有 Provider 的健康状态."""
        result: dict[str, bool] = {}
        chain = getattr(self, "provider_chain", []) or []
        for name in chain:
            provider = self.get_provider(name)
            if provider is None:
                result[name] = False
            else:
                try:
                    result[name] = bool(provider.health_check())
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{name} health check 异常: {e}")
                    result[name] = False
        return result

    def reset_cache(self) -> None:
        """清空进程级实例缓存（用于测试隔离）."""
        with self._cache_lock:
            self._instance_cache.clear()

    @classmethod
    def _get_provider_class(cls, name: str) -> type[DataProvider] | None:
        """延迟注册：按需导入 Provider 类."""
        registry = cls._PROVIDER_REGISTRY
        if name in registry:
            return registry[name]

        try:
            if name == "baostock":
                from trade_krono_cli.data_providers.baostock_provider import (
                    BaostockProvider,
                )

                registry[name] = BaostockProvider
            elif name == "akshare":
                from trade_krono_cli.data_providers.akshare_provider import (
                    AkShareProvider,
                )

                registry[name] = AkShareProvider
            elif name == "mootdx":
                from trade_krono_cli.data_providers.mootdx_provider import (
                    MootDxProvider,
                )

                registry[name] = MootDxProvider
            elif name == "tushare":
                from trade_krono_cli.data_providers.tushare_provider import (
                    TushareProvider,
                )

                registry[name] = TushareProvider
            elif name == "tonghuashun":
                from trade_krono_cli.data_providers.tonghuashun_provider import (
                    TongHuaShunProvider,
                )

                registry[name] = TongHuaShunProvider
            elif name == "akshare_hk":
                from trade_krono_cli.data_providers.akshare_hk_provider import (
                    AkShareHKProvider,
                )

                registry[name] = AkShareHKProvider
            elif name == "efinance":
                from trade_krono_cli.data_providers.efinance_provider import (
                    EFinanceProvider,
                )

                registry[name] = EFinanceProvider
            elif name == "easyquotation":
                from trade_krono_cli.data_providers.easyquotation_provider import (
                    EasyQuotationProvider,
                )

                registry[name] = EasyQuotationProvider
            elif name == "tencent":
                from trade_krono_cli.data_providers.tencent_provider import (
                    TencentProvider,
                )

                registry[name] = TencentProvider
            elif name == "adk":
                from trade_krono_cli.data_providers.adk_data_provider import (
                    AdkDataProvider,
                )

                registry[name] = AdkDataProvider
            else:
                logger.warning(f"未知的 Provider 名称: {name}")
                return None
            return registry[name]
        except (ImportError, RuntimeError) as e:
            logger.debug(f"Provider {name} 加载失败: {e}")
            return None

    @classmethod
    def clear_registry(cls) -> None:
        """清空 Provider 类注册表（用于测试隔离）."""
        cls._PROVIDER_REGISTRY.clear()
