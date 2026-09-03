"""TradingAgents 适配器实现。

封装 cli_anything.tradingagents 的全部导入和调用，
业务代码只通过 TradingAgentsAdapter 与 TA 外部项目交互。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from trade_krono_cli.adapters.base import TradingAgentsAdapter
from trade_krono_cli.errors import ModelLoadError
from trade_krono_cli.security import ensure_import_path


class TradingAgentsAdapterImpl(TradingAgentsAdapter):
    """基于 cli_anything.tradingagents 的 TradingAgents 适配器实现。"""

    def __init__(self) -> None:
        self._run_analysis: Any = None
        self._build_config: Any = None

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def load(self, settings: Any) -> None:  # noqa: ANN401 — Settings 来自 trade_krono_cli.config，此处为懒加载注入点
        """将 TradingAgents-astock/agent-harness 加入 sys.path，
        并导入核心模块（run_analysis / build_config）。
        """
        if self._run_analysis is not None:
            return

        harness_root = settings.tradingagents_root / "agent-harness"
        ta_root = settings.tradingagents_root
        ensure_import_path(harness_root, ta_root)
        logger.debug(f"TradingAgents-astock 路径已加入: {harness_root} + {ta_root}")

        try:
            from cli_anything.tradingagents.core.analysis import (
                build_config,
                run_analysis,
            )
        except ImportError as e:
            msg = (
                f"无法导入 TradingAgents 核心模块：{e}。"
                f"请确认已安装 tradingagents（pip install -e {settings.tradingagents_root}）"
            )
            raise ModelLoadError(
                msg,
            ) from e

        self._run_analysis = run_analysis
        self._build_config = build_config
        logger.info("✅ TradingAgentsAdapter 核心模块加载完成")

    # ── 接口实现 ─────────────────────────────────────────────────────────────

    def build_config(self, **kwargs: Any) -> dict:  # noqa: ANN401 — 委托 cli_anything.tradingagents 动态函数
        """委托 cli_anything.tradingagents.core.analysis.build_config。"""
        if self._build_config is None:
            msg = "TradingAgentsAdapter 尚未加载，请先调用 load() 或确保在分析前完成初始化"
            raise RuntimeError(
                msg,
            )
        return self._build_config(**kwargs)

    def run_analysis(self, ticker: str, config: dict) -> dict:
        """委托 cli_anything.tradingagents.core.analysis.run_analysis。"""
        if self._run_analysis is None:
            msg = "TradingAgentsAdapter 尚未加载，请先调用 load() 或确保在分析前完成初始化"
            raise RuntimeError(
                msg,
            )
        # run_analysis(ticker, trade_date, config, analysts=[...], ...)
        return self._run_analysis(
            ticker,
            config["trade_date"],
            config,
            **config.get("extra_kwargs", {}),
        )
