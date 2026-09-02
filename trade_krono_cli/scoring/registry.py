"""
scoring.registry — 打分策略与风险加分策略的注册表工厂。

类似 DataProviderFactory，采用懒加载 + 进程级缓存模式。
"""

from __future__ import annotations

import threading
from typing import Optional

from loguru import logger

from trade_krono_cli.scoring.base import CompositeScorer, RiskBoostStrategy

# ═══════════════════════════════════════════════════════
# 综合打分器注册表
# ═══════════════════════════════════════════════════════


class ScorerRegistry:
    """综合打分策略注册表。"""

    _registry: dict[str, type[CompositeScorer]] = {}
    _instance_cache: dict[str, CompositeScorer] = {}
    _lock = threading.RLock()

    def register(self, cls: type[CompositeScorer]) -> None:
        """注册一个打分策略类。"""
        with self._lock:
            self._registry[cls.name] = cls
        logger.debug(f"✅ 打分策略已注册: {cls.name}")

    def get(self, name: str) -> Optional[CompositeScorer]:
        """
        获取指定名称的打分策略实例（进程级缓存）。
        返回 None 表示未找到。
        """
        with self._lock:
            if name in self._instance_cache:
                return self._instance_cache[name]

            cls = self._registry.get(name)
            if cls is None:
                # 尝试懒加载内置策略
                cls = self._lazy_load(name)
            if cls is None:
                return None

            instance = cls()
            self._instance_cache[name] = instance
            return instance

    def _lazy_load(self, name: str) -> Optional[type[CompositeScorer]]:
        """按需导入内置策略类。"""
        if name == "linear":
            from trade_krono_cli.scoring.scorers import LinearScorer

            self.register(LinearScorer)
            return LinearScorer
        elif name == "multiplicative":
            from trade_krono_cli.scoring.scorers import MultiplicativeScorer

            self.register(MultiplicativeScorer)
            return MultiplicativeScorer
        elif name == "rank_based":
            from trade_krono_cli.scoring.scorers import RankBasedScorer

            self.register(RankBasedScorer)
            return RankBasedScorer
        return None

    def list_all(self) -> list[str]:
        """返回所有已注册的策略名称。"""
        return list(self._registry.keys())

    def reset(self) -> None:
        """清空实例缓存（用于测试隔离），保留注册表。"""
        with self._lock:
            self._instance_cache.clear()


# ═══════════════════════════════════════════════════════
# 风险加分策略注册表
# ═══════════════════════════════════════════════════════


class RiskBoostRegistry:
    """风险加分策略注册表。"""

    _registry: dict[str, type[RiskBoostStrategy]] = {}
    _instance_cache: dict[str, RiskBoostStrategy] = {}
    _lock = threading.RLock()

    def register(self, cls: type[RiskBoostStrategy]) -> None:
        with self._lock:
            self._registry[cls.name] = cls
        logger.debug(f"✅ 风险加分策略已注册: {cls.name}")

    def get(self, name: str) -> Optional[RiskBoostStrategy]:
        with self._lock:
            if name in self._instance_cache:
                return self._instance_cache[name]

            cls = self._registry.get(name)
            if cls is None:
                cls = self._lazy_load(name)
            if cls is None:
                return None

            instance = cls()
            self._instance_cache[name] = instance
            return instance

    def _lazy_load(self, name: str) -> Optional[type[RiskBoostStrategy]]:
        """按需导入内置策略类。"""
        if name == "fixed_boost":
            from trade_krono_cli.scoring.risk_boosters import FixedBoostBooster

            self.register(FixedBoostBooster)
            return FixedBoostBooster
        elif name == "scaled_boost":
            from trade_krono_cli.scoring.risk_boosters import ScaledBoostBooster

            self.register(ScaledBoostBooster)
            return ScaledBoostBooster
        elif name == "diminishing_boost":
            from trade_krono_cli.scoring.risk_boosters import DiminishingBoostBooster

            self.register(DiminishingBoostBooster)
            return DiminishingBoostBooster
        return None

    def list_all(self) -> list[str]:
        return list(self._registry.keys())

    def reset(self) -> None:
        with self._lock:
            self._instance_cache.clear()


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_scorer_registry: Optional[ScorerRegistry] = None
_boost_registry: Optional[RiskBoostRegistry] = None
_global_lock = threading.Lock()


def get_scorer_registry() -> ScorerRegistry:
    global _scorer_registry
    if _scorer_registry is None:
        with _global_lock:
            if _scorer_registry is None:
                _scorer_registry = ScorerRegistry()
                # 注册内置策略
                from trade_krono_cli.scoring.scorers import (
                    LinearScorer,
                    MultiplicativeScorer,
                    RankBasedScorer,
                )

                _scorer_registry.register(LinearScorer)
                _scorer_registry.register(MultiplicativeScorer)
                _scorer_registry.register(RankBasedScorer)
    return _scorer_registry


def get_risk_boost_registry() -> RiskBoostRegistry:
    global _boost_registry
    if _boost_registry is None:
        with _global_lock:
            if _boost_registry is None:
                _boost_registry = RiskBoostRegistry()
                from trade_krono_cli.scoring.risk_boosters import (
                    DiminishingBoostBooster,
                    FixedBoostBooster,
                    ScaledBoostBooster,
                )

                _boost_registry.register(FixedBoostBooster)
                _boost_registry.register(ScaledBoostBooster)
                _boost_registry.register(DiminishingBoostBooster)
    return _boost_registry


def reset_scoring_registries() -> None:
    """重置所有注册表（用于测试隔离）。"""
    global _scorer_registry, _boost_registry
    with _global_lock:
        _scorer_registry = None
        _boost_registry = None
    ScorerRegistry._instance_cache.clear()
    ScorerRegistry._registry.clear()
    RiskBoostRegistry._instance_cache.clear()
    RiskBoostRegistry._registry.clear()
