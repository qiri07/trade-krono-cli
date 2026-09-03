"""Domain Model — A 股量化投研系统的核心领域对象。

领域分层（单向依赖，底部稳定、顶部灵活）：

    MarketSnapshot          市场状态（最底层，不可变事实）
        ├── TAAnalysis         技术面/基本面分析结果
        ├── KronosPrediction   时序预测结果
        └── RiskAssessment     风险评估结果
                │
                ▼
        SignalAssessment     多源信号融合 + EV 计算
                │
                ▼
        InvestmentDecision   最终投资决策
                │
                ▼
        EvaluationResult     回测/评估结果
                │
                ▼
        Experiment           假设检验与实验追踪

设计原则：
  · 领域对象是 immutable 的（frozen dataclass），代表"已发生的事实"
  · 变更通过 replace() 生成新对象，不原地修改
  · Pipeline 不再拼 dict，直接使用领域对象协作
  · 旧 dict 接口通过 from_dict() / to_dict() 保持兼容

用法：
    from trade_krono_cli.domain import (
        Stock, MarketSnapshot, TAAnalysis, KronosPrediction,
        RiskAssessment, SignalAssessment, InvestmentDecision,
        EvalRecord, EvaluationSummary, Experiment,
    )
    from trade_krono_cli.domain.factory import (
        build_signal_assessment, build_investment_decision,
        build_eval_record,
    )
"""

from __future__ import annotations  # fmt: skip

# ── 枚举（必须在其他模块之前导入，避免循环依赖）──────────────────────────
from trade_krono_cli.domain.types import Direction, ExperimentType, Signal

# ── 决策 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.decision import InvestmentDecision

# ── 评估 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.evaluation import (
    BacktestResult,
    EvalRecord,
    EvaluationSummary,
    HorizonMetrics,
)

# ── 实验 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.experiment import (
    Experiment,
    Hypothesis,
    build_alpha_experiment,
)

# ── 工厂 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.factory import (
    build_eval_record,
    build_investment_decision,
    build_signal_assessment,
)

# ── 市场状态 ───────────────────────────────────────────────────────────────
from trade_krono_cli.domain.market import MarketSnapshot

# ── 预测 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.prediction import (
    KronosPrediction,
    PredictionDistribution,
    TAAnalysis,
)

# ── Kronos 预测结果 ────────────────────────────────────────────────────────
from trade_krono_cli.domain.kronos_result import KronosForecastResult

# ── 风险 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.risk import RiskAssessment, RiskFactor

# ── 信号 ───────────────────────────────────────────────────────────────────
from trade_krono_cli.domain.signal import (
    SignalAssessment,
    SignalConflict,
)

# ── 基础实体 ───────────────────────────────────────────────────────────────
from trade_krono_cli.domain.stock import Stock

# ── 统一导出 ───────────────────────────────────────────────────────────────
__all__ = [
    "BacktestResult",
    "Direction",
    # 评估
    "EvalRecord",
    "EvaluationSummary",
    "Experiment",
    "ExperimentType",
    "HorizonMetrics",
    # 实验
    "Hypothesis",
    # 决策
    "InvestmentDecision",
    "KronosForecastResult",
    "KronosPrediction",
    # 市场
    "MarketSnapshot",
    # 预测
    "PredictionDistribution",
    # 风险
    "RiskAssessment",
    "RiskFactor",
    # 枚举
    "Signal",
    # 信号
    "SignalAssessment",
    "SignalConflict",
    # 实体
    "Stock",
    "TAAnalysis",
    "build_alpha_experiment",
    "build_eval_record",
    "build_investment_decision",
    # 工厂
    "build_signal_assessment",
]
