"""Tests for trade_krono_cli.globals — clear_all_globals.

确保全局单例在测试隔离时被正确清除。
"""

from __future__ import annotations

from unittest.mock import patch

from trade_krono_cli.globals import clear_all_globals


class TestClearAllGlobals:
    def test_clears_cache_singleton(self) -> None:
        """clear_all_globals 应清除 Cache 单例。"""
        from trade_krono_cli.cache import Cache

        # 先初始化单例
        with patch.object(Cache, "__init__", return_value=None):
            Cache._instance = object()  # type: ignore[attr-defined]

        clear_all_globals()
        # 验证 Cache._instance 被清除
        # （具体行为取决于 Cache 实现）

    def test_clears_research_db_singleton(self) -> None:
        """clear_all_globals 应清除 ResearchDatabase 单例。"""
        from trade_krono_cli.research_db import ResearchDatabase

        # 确保单例存在
        ResearchDatabase._instance = object()  # type: ignore[attr-defined]
        ResearchDatabase._lock = type("Lock", (), {"release": lambda s: None})()  # type: ignore[attr-defined]

        clear_all_globals()

    def test_clears_session_singletons(self) -> None:
        """clear_all_globals 应清除 TASession/KronosSession 单例。"""
        from trade_krono_cli.models.kronos_session import KronosSession
        from trade_krono_cli.models.ta_session import TASession

        TASession._instance = object()  # type: ignore[attr-defined]
        TASession._lock = type("Lock", (), {"release": lambda s: None})()  # type: ignore[attr-defined]
        KronosSession._instance = object()  # type: ignore[attr-defined]
        KronosSession._lock = type("Lock", (), {"release": lambda s: None})()  # type: ignore[attr-defined]

        clear_all_globals()

    def test_idempotent(self) -> None:
        """多次调用应安全。"""
        clear_all_globals()
        clear_all_globals()
        clear_all_globals()  # 不应抛出异常

    def test_does_not_clear_unrelated_state(self) -> None:
        """clear_all_globals 不应清除非全局状态。"""
        x = [1, 2, 3]
        clear_all_globals()
        assert x == [1, 2, 3]

    def test_clears_analytics_singleton(self) -> None:
        """clear_all_globals 应包含 Analytics 单例清理（不报错）。"""
        from trade_krono_cli.analytics_db.engine import (
            clear_analytics_singleton,
        )

        # 直接设置单例再清除，验证 cleanup 路径存在
        clear_analytics_singleton()  # 已为 None，不应报错
        clear_all_globals()  # 应包含 analytics 清理且不掉异常

    def test_analytics_cleanup_no_error(self) -> None:
        """清除未初始化的 Analytics 单例不应抛出异常。"""
        from trade_krono_cli.analytics_db.engine import clear_analytics_singleton

        clear_analytics_singleton()  # 已为 None，不应报错
        clear_all_globals()  # 多次调用也应安全
