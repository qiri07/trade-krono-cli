"""
data_providers — 多数据源抽象层。

提供统一的 DataProvider 接口，支持 baostock / akshare / mootdx / tushare
四种 A 股数据源，并内置主备降级机制。

架构：
  应用层（data.py / stock_filter.py / abnormal_stock.py）
    → DataProviderFactory.get_provider(name) → 具体 Provider 实例
      → 各 Provider 内部处理不同 API 的调用差异
      → 失败时 Factory 自动切换备用源
  缓存层（cache.py）— 对上层透明，key 格式不变
"""

from __future__ import annotations

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    get_data_factory,
    reset_data_factory,
)

__all__ = [
    # 数据模型
    "DataProvider",
    "KlineData",
    "RealtimeQuote",
    "StockMetadata",
    # 工厂
    "DataProviderFactory",
    "get_data_factory",
    "reset_data_factory",
]
