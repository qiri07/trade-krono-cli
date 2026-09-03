"""全局状态清理工具。

本模块集中管理所有模块级单例的清除函数，供测试框架调用，
确保测试之间不会因全局状态污染而相互干扰。

使用示例：
    from trade_krono_cli.globals import clear_all_globals
    clear_all_globals()
"""

from __future__ import annotations


def clear_all_globals() -> None:
    """清除所有模块级全局单例，重置懒加载标志。

    被清除的状态：
      - config._settings        — get_settings() 下一次调用将重新初始化
      - cache._cache            — get_cache() 下一次调用将重新初始化
      - research_db._research   — get_research() 下一次调用将重新初始化
      - data._bs / _HAS_BS / _bs_logged_in / _bs_limiter — baostock 状态
      - ta_runner._TRADINGAGENTS_IMPORTED — TradingAgents 懒加载标志
      - kronos_runner._KRONOS_IMPORTED     — Kronos 懒加载标志
      - kronos_session._cache  — KronosSession 进程级单例缓存
      - ta_session._cache      — TASession 进程级单例缓存
      - retry_policy._failure_store — FailureStore 单例
    """
    from trade_krono_cli.cache import clear_cache_singleton
    from trade_krono_cli.config import clear_settings
    from trade_krono_cli.data import clear_baostock_globals
    from trade_krono_cli.kronos_runner import clear_kronos_imported
    from trade_krono_cli.research_db import clear_research_singleton
    from trade_krono_cli.retry_policy import clear_failure_store_singleton
    from trade_krono_cli.ta_runner import clear_tradingagents_imported

    clear_settings()
    clear_cache_singleton()
    clear_research_singleton()
    clear_baostock_globals()
    clear_tradingagents_imported()
    clear_kronos_imported()
    clear_failure_store_singleton()
    from trade_krono_cli.models.kronos_session import KronosSession
    from trade_krono_cli.models.ta_session import TASession

    KronosSession.clear_cache()
    TASession.clear_cache()
