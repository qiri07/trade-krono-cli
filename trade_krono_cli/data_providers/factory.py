"""
data_providers.factory — 数据源工厂 + 自动降级路由。

职责：
  · 根据配置创建并缓存 Provider 实例（进程级单例）
  · 按优先级顺序尝试多个 Provider，失败自动切换备用
  · 不同数据维度可从不同 Provider 获取，统一合并后返回

使用方式：
    factory = DataProviderFactory()
    # 获取单个 provider
    provider = factory.get_provider("baostock")
    # 带降级的 K 线拉取
    df = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
    # 合并多维度（K 线 + 行情 + 元数据）
    result = factory.fetch_merged("sh.600519", "2026-01-01", "2026-08-13")
"""
from __future__ import annotations

import threading
from typing import Optional

from loguru import logger

from trade_krono_cli.config import get_settings
from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)


# ═══════════════════════════════════════════════════════
# 工厂实现
# ═══════════════════════════════════════════════════════

class DataProviderFactory:
    """
    数据源工厂，管理 Provider 实例生命周期并实现自动降级。

    设计要点：
      - 进程级单例缓存，避免重复创建 Provider
      - lazy import：只在首次使用时导入依赖包
      - 健康检查优先：尝试前检查 Provider 可用性
      - 维度拆分：K 线 / 行情 / 元数据可来自不同源
    """

    # 支持的 Provider 名称 → 类映射
    _PROVIDER_REGISTRY: dict[str, type[DataProvider]] = {}

    # 进程级缓存（线程安全）
    _instance_cache: dict[str, DataProvider] = {}
    _cache_lock = threading.Lock()

    def __init__(self, primary: str = "baostock", fallbacks: Optional[list[str]] = None):
        """
        Parameters
        ----------
        primary : str
            主数据源名称，默认 "baostock"
        fallbacks : list[str] | None
            备用数据源列表，按优先级排列
        """
        self.primary = primary
        self.fallbacks = fallbacks or self._default_fallbacks()

    @staticmethod
    def _default_fallbacks() -> list[str]:
        """默认降级顺序：baostock → akshare → mootdx → tushare"""
        return ["akshare", "mootdx", "tushare"]

    @property
    def provider_chain(self) -> list[str]:
        """返回按优先级排列的完整 Provider 链"""
        chain = [self.primary] + [f for f in self.fallbacks if f != self.primary]
        return chain

    # ── Provider 实例管理 ───────────────────────────────────────────────

    def get_provider(self, name: str) -> Optional[DataProvider]:
        """
        获取指定名称的 Provider 实例（进程级缓存）。

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

    def get_providers(self, names: Optional[list[str]] = None) -> list[DataProvider]:
        """
        批量获取 Provider 实例，过滤掉不可用的。

        Returns
        -------
        list[DataProvider]
            可用的 Provider 列表（按 names 顺序）
        """
        names = names or self.provider_chain
        result = []
        for name in names:
            p = self.get_provider(name)
            if p is not None:
                result.append(p)
        return result

    # ── 核心接口：带降级的拉取方法 ─────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> Optional[KlineData]:
        """
        拉取 K 线数据，按优先级尝试各 Provider，失败自动降级。

        Returns
        -------
        KlineData | None
        """
        if not self._try_kline_support(ticker, start_date, end_date, frequency, adjustflag):
            return None
        return None

    def _try_kline_support(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> bool:
        """尝试从各 Provider 拉取 K 线，返回是否成功。"""
        for name in self.provider_chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_kline:
                logger.debug(f"{name}: 不支持 K 线，跳过")
                continue
            if not provider.health_check():
                logger.debug(f"{name}: 健康检查未通过，跳过")
                continue
            try:
                data = provider.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
                if data is not None and not data.is_empty:
                    logger.info(f"✅ K 线获取成功: {ticker} ← {name} ({len(data.timestamps)} 条)")
                    return True
            except Exception as e:
                logger.warning(f"{name} K 线拉取异常 {ticker}: {str(e)[:100]}")
                continue
        logger.warning(f"❌ 所有 Provider 均无法获取 K 线: {ticker}")
        return False

    def fetch_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        """
        获取实时行情，按优先级尝试各 Provider。

        Returns
        -------
        RealtimeQuote | None
        """
        for name in self.provider_chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_quote:
                continue
            if not provider.health_check():
                continue
            try:
                quote = provider.fetch_quote(ticker)
                if quote is not None:
                    logger.debug(f"✅ 行情获取成功: {ticker} ← {name}")
                    return quote
            except Exception as e:
                logger.warning(f"{name} 行情拉取异常 {ticker}: {str(e)[:100]}")
                continue
        return None

    def fetch_metadata(self, ticker: str) -> Optional[StockMetadata]:
        """
        获取股票元数据，按优先级尝试各 Provider。

        Returns
        -------
        StockMetadata | None
        """
        for name in self.provider_chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_metadata:
                continue
            if not provider.health_check():
                continue
            try:
                meta = provider.fetch_metadata(ticker)
                if meta is not None:
                    logger.debug(f"✅ 元数据获取成功: {ticker} ← {name}")
                    return meta
            except Exception as e:
                logger.warning(f"{name} 元数据拉取异常 {ticker}: {str(e)[:100]}")
                continue
        return None

    def fetch_merged(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> dict:
        """
        从不同 Provider 分别获取 K 线 / 行情 / 元数据，合并返回。

        每个维度独立降级，确保最大可用性。

        Returns
        -------
        dict with keys: kline, quote, metadata
        """
        kline = self.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
        quote = self.fetch_quote(ticker)
        metadata = self.fetch_metadata(ticker)

        return {
            "kline": kline,
            "quote": quote,
            "metadata": metadata,
        }

    # ── 工具方法 ────────────────────────────────────────────────────────

    def available_providers(self) -> list[str]:
        """返回当前已初始化的可用 Provider 名称列表"""
        return [
            name for name in self.provider_chain
            if self.get_provider(name) is not None
        ]

    def health_check_all(self) -> dict[str, bool]:
        """检查所有 Provider 的健康状态"""
        result = {}
        for name in self.provider_chain:
            provider = self.get_provider(name)
            if provider is None:
                result[name] = False
            else:
                try:
                    result[name] = provider.health_check()
                except Exception:
                    result[name] = False
        return result

    # ── 内部工具 ────────────────────────────────────────────────────────

    @classmethod
    def _get_provider_class(cls, name: str) -> Optional[type[DataProvider]]:
        """延迟注册：按需导入 Provider 类"""
        registry = cls._PROVIDER_REGISTRY
        if name in registry:
            return registry[name]

        try:
            if name == "baostock":
                from trade_krono_cli.data_providers.baostock_provider import BaostockProvider
                registry[name] = BaostockProvider
            elif name == "akshare":
                from trade_krono_cli.data_providers.akshare_provider import AkShareProvider
                registry[name] = AkShareProvider
            elif name == "mootdx":
                from trade_krono_cli.data_providers.mootdx_provider import MootDxProvider
                registry[name] = MootDxProvider
            elif name == "tushare":
                from trade_krono_cli.data_providers.tushare_provider import TushareProvider
                registry[name] = TushareProvider
            else:
                logger.warning(f"未知的 Provider 名称: {name}")
                return None
            return registry[name]
        except (ImportError, RuntimeError) as e:
            logger.debug(f"Provider {name} 加载失败: {e}")
            return None

    def reset_cache(self) -> None:
        """清空进程级缓存（用于测试隔离）"""
        with self._cache_lock:
            self._instance_cache.clear()


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_factory_instance: Optional[DataProviderFactory] = None
_factory_lock = threading.Lock()


def get_data_factory() -> DataProviderFactory:
    """
    获取全局 DataProviderFactory 单例。
    自动从 Settings 读取 primary / fallbacks 配置。
    """
    global _factory_instance
    if _factory_instance is None:
        with _factory_lock:
            if _factory_instance is None:
                s = get_settings()
                primary = getattr(s, "data_provider", "baostock")
                fallback_str = getattr(s, "data_fallback", "")
                fallbacks = [
                    x.strip()
                    for x in fallback_str.split(",")
                    if x.strip()
                ] if fallback_str else []
                _factory_instance = DataProviderFactory(
                    primary=primary,
                    fallbacks=fallbacks,
                )
    return _factory_instance


def reset_data_factory() -> None:
    """重置全局工厂单例（用于测试隔离）"""
    global _factory_instance
    with _factory_lock:
        _factory_instance = None
    DataProviderFactory._instance_cache.clear()
    DataProviderFactory._PROVIDER_REGISTRY.clear()
