"""
研究数据库包入口。

向后兼容：保留 trade_krono_cli.research_db 的导入路径。
所有公开 API 通过此模块导出，原有 import 语句无需修改。
"""

from __future__ import annotations

from trade_krono_cli.research_db.committee import CommitteeMixin
from trade_krono_cli.research_db.decisions import DecisionsMixin, ReportsMixin
from trade_krono_cli.research_db.experiments import ExperimentsMixin

# 从各子模块组装完整 ResearchDatabase 类
from trade_krono_cli.research_db.jobs import JobMixin
from trade_krono_cli.research_db.kronos_forecast import KronosForecastMixin
from trade_krono_cli.research_db.signals import SignalsMixin
from trade_krono_cli.research_db.snapshots import SnapshotsMixin
from trade_krono_cli.research_db.stats import StatsMixin
from trade_krono_cli.research_db.strategy_runs import StrategyRunsMixin
from trade_krono_cli.research_db.ta_analysis import TaAnalysisMixin
from trade_krono_cli.research_db.walkforward import WalkforwardMixin


# 组装完整类（MRO：最右基类最先被查找）
class ResearchDatabase(
    ExperimentsMixin,
    WalkforwardMixin,
    SnapshotsMixin,
    StrategyRunsMixin,
    CommitteeMixin,
    StatsMixin,
    ReportsMixin,
    DecisionsMixin,
    SignalsMixin,
    KronosForecastMixin,
    TaAnalysisMixin,
    JobMixin,
):
    """
    投研数据持久化层。

    由各领域 Mixin 组合而成，详见 research_db/ 子模块文档。
    """


# ── 向后兼容别名 ───────────────────────────────────────
# 这些名称在原 research_db.py 中直接导出，测试和其他模块通过它们访问。

# schema 常量（原 REASONING_TRUNCATE_LEN、_RESEARCH_TABLES、_validate_table_name）
REASONING_TRUNCATE_LEN: int = 500
RESEARCH_TABLES: frozenset[str] = frozenset(
    {
        "jobs",
        "ta_analysis",
        "kronos_forecast",
        "signals",
        "decisions",
        "raw_reports",
        "backtest_results",
        "strategy_runs",
        "evaluation_results",
        "signal_history",
        "committee_deliberations",
        "data_snapshots",
        "walkforward_runs",
        "experiments",
    }
)


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    """Validate a table name against an allowed set. Raises ValueError if invalid."""
    if table not in allowed:
        raise ValueError(f"Unauthorized table: {table}")
    return table


# ── 模块级单例 ─────────────────────────────────────────
_research: ResearchDatabase | None = None


def get_research() -> ResearchDatabase:
    global _research
    if _research is None:
        _research = ResearchDatabase()
    return _research


def clear_research_singleton() -> None:
    """清除研究数据库单例，使下一次 get_research() 重新初始化。用于测试隔离。"""
    global _research
    _research = None


__all__ = [
    "REASONING_TRUNCATE_LEN",
    "RESEARCH_TABLES",
    "CommitteeMixin",
    "DecisionsMixin",
    "ExperimentsMixin",
    "JobMixin",
    "KronosForecastMixin",
    "ReportsMixin",
    "ResearchDatabase",
    "SignalsMixin",
    "SnapshotsMixin",
    "StatsMixin",
    "StrategyRunsMixin",
    "TaAnalysisMixin",
    "WalkforwardMixin",
    "_validate_table_name",
    "clear_research_singleton",
    "get_research",
]
