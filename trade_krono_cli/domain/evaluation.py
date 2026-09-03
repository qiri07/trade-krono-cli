"""Evaluation — 预测评估领域对象。

EvaluationResult 封装单次预测的回测/评估结果，
EvaluationSummary 封装多次预测的聚合统计。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalRecord:
    """单次预测的评估记录。

    Fields
    ------
    ticker               股票代码
    eval_date            预测日期
    horizon_days         持有期（交易日）
    pred_direction       预测方向（UP/DOWN/FLAT）
    pred_return_pct      预测收益率（%）
    actual_return_pct    实际收益率（%）
    actual_direction     实际方向
    is_direction_correct 方向是否预测正确
    error_pct            预测误差（%）

    # 分布分位数（来自 PredictionDistribution）
    p10 / p25 / p50 / p75 / p90

    # 附加上下文
    ta_signal            TA 信号
    ranking_score        辅助排序分 0-100（原 composite_score 降级）
    expected_value       EV（%）—— 主要决策指标
    composite_score      向后兼容别名（同 ranking_score）
    risk_score           风险评分
    conflict             多源冲突标记
    entry_blocked        买入日涨停拦截
    exit_blocked         退出日跌停拦截
    cost_bps_applied     交易成本（bps）
    """

    ticker: str
    eval_date: str
    horizon_days: int
    pred_direction: str | None = None
    pred_return_pct: float | None = None
    actual_return_pct: float = 0.0
    actual_direction: str = "FLAT"
    is_direction_correct: bool = False
    error_pct: float = 0.0

    # 分位数
    p10: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p90: float | None = None

    # 上下文
    ta_signal: str | None = None
    ranking_score: float | None = None
    expected_value: float | None = None
    composite_score: float | None = None  # 向后兼容别名
    risk_score: float | None = None
    conflict: str = ""
    entry_blocked: bool = False
    exit_blocked: bool = False
    cost_bps_applied: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "horizon_days": self.horizon_days,
            "pred_direction": self.pred_direction,
            "pred_return_pct": self.pred_return_pct,
            "actual_return_pct": self.actual_return_pct,
            "actual_direction": self.actual_direction,
            "is_direction_correct": self.is_direction_correct,
            "error_pct": self.error_pct,
            "p10": self.p10,
            "p25": self.p25,
            "p50": self.p50,
            "p75": self.p75,
            "p90": self.p90,
            "ta_signal": self.ta_signal,
            "ranking_score": self.ranking_score,
            "expected_value": self.expected_value,
            "composite_score": self.composite_score,
            "risk_score": self.risk_score,
            "conflict": self.conflict,
            "entry_blocked": self.entry_blocked,
            "exit_blocked": self.exit_blocked,
            "cost_bps_applied": self.cost_bps_applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvalRecord:
        # 兼容旧版 composite_score → ranking_score
        rs = data.get("ranking_score") or data.get("composite_score")
        return cls(
            ticker=data["ticker"],
            eval_date=data.get("eval_date", ""),
            horizon_days=int(data.get("horizon_days", 5)),
            pred_direction=data.get("pred_direction"),
            pred_return_pct=data.get("pred_return_pct"),
            actual_return_pct=float(data.get("actual_return_pct", 0.0)),
            actual_direction=data.get("actual_direction", "FLAT"),
            is_direction_correct=bool(data.get("is_direction_correct", False)),
            error_pct=float(data.get("error_pct", 0.0)),
            p10=data.get("p10"),
            p25=data.get("p25"),
            p50=data.get("p50"),
            p75=data.get("p75"),
            p90=data.get("p90"),
            ta_signal=data.get("ta_signal"),
            ranking_score=rs,
            expected_value=data.get("expected_value"),
            composite_score=rs,
            risk_score=data.get("risk_score"),
            conflict=data.get("conflict", ""),
            entry_blocked=bool(data.get("entry_blocked", False)),
            exit_blocked=bool(data.get("exit_blocked", False)),
            cost_bps_applied=float(data.get("cost_bps_applied", 0.0)),
        )


@dataclass
class HorizonMetrics:
    """按单一 horizon 分组的指标汇总。"""

    kronos_dir_accuracy: float = 0.0
    ta_buy_win_rate: float = 0.0
    ta_buy_avg_return: float = 0.0
    ta_hold_avg_return: float = 0.0
    combined_buy_up_win_rate: float = 0.0
    combined_buy_up_avg_return: float = 0.0
    high_conf_win_rate: float = 0.0
    high_conf_avg_return: float = 0.0
    # 回测增强指标
    win_rate_pct: float = 0.0
    avg_return_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    # EV 相关
    avg_ev: float = 0.0
    ev_accuracy: float = 0.0  # EV 方向与实际方向的一致性

    def to_dict(self) -> dict:
        return {
            "kronos_dir_accuracy": self.kronos_dir_accuracy,
            "ta_buy_win_rate": self.ta_buy_win_rate,
            "ta_buy_avg_return": self.ta_buy_avg_return,
            "combined_buy_up_win_rate": self.combined_buy_up_win_rate,
            "high_conf_win_rate": self.high_conf_win_rate,
            "win_rate_pct": self.win_rate_pct,
            "avg_return_pct": self.avg_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "avg_ev": self.avg_ev,
            "ev_accuracy": self.ev_accuracy,
        }


@dataclass
class BacktestResult:
    """单次完整回测的结果。"""

    initial_capital: float = 1_000_000.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    metrics: dict = field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    n_trades: int = 0
    rebal_mode: str = "fixed_horizon"
    records: list = field(default_factory=list)

    @staticmethod
    def empty() -> BacktestResult:
        return BacktestResult()

    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "final_value": self.final_value,
            "total_return_pct": self.total_return_pct,
            "n_trades": self.n_trades,
            "rebal_mode": self.rebal_mode,
        }


@dataclass
class EvaluationSummary:
    """评估汇总统计。"""

    # 聚合计数
    kronos_n: int = 0
    ta_buy_n: int = 0
    ta_hold_n: int = 0
    combined_buy_up_n: int = 0
    high_conf_n: int = 0
    # 约束拦截计数
    entry_limit_up_blocked: int = 0
    exit_limit_down_blocked: int = 0
    cost_applied_n: int = 0
    # 按 horizon 分组的指标
    horizons: dict[int, HorizonMetrics] = field(default_factory=dict)
    records: list[EvalRecord] = field(default_factory=list)
    # 回测结果
    backtest: BacktestResult | None = None
    # 基准对比
    benchmark_cum_return_pct: float = 0.0
    excess_return_pct: float = 0.0
    benchmark_curve: dict[str, float] = field(default_factory=dict)
    excess_curve: dict[str, float] = field(default_factory=dict)

    @property
    def overall_win_rate(self) -> float:
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.is_direction_correct) / len(self.records) * 100

    @property
    def overall_avg_return(self) -> float:
        if not self.records:
            return 0.0
        rets = [r.actual_return_pct for r in self.records if r.actual_return_pct != 0]
        return sum(rets) / len(rets) if rets else 0.0

    def to_dict(self) -> dict:
        return {
            "kronos_n": self.kronos_n,
            "ta_buy_n": self.ta_buy_n,
            "combined_buy_up_n": self.combined_buy_up_n,
            "high_conf_n": self.high_conf_n,
            "overall_win_rate": round(self.overall_win_rate, 2),
            "overall_avg_return": round(self.overall_avg_return, 4),
            "entry_limit_up_blocked": self.entry_limit_up_blocked,
            "exit_limit_down_blocked": self.exit_limit_down_blocked,
            "benchmark_cum_return_pct": self.benchmark_cum_return_pct,
            "excess_return_pct": self.excess_return_pct,
            "horizons": {str(k): v.to_dict() for k, v in self.horizons.items()},
            "backtest": self.backtest.to_dict() if self.backtest else None,
        }
