"""
Experiment — 实验与假设检验领域对象。

Experiment 记录一次假设检验的完整生命周期：
  注册 → 运行 → 评估 → 验证 → 归档
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional

from trade_krono_cli.domain.types import ExperimentType

# ═══════════════════════════════════════════════════════
#  Hypothesis
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class Hypothesis:
    """
    一个可被回测证伪的科学假设。

    Fields
    ------
    statement       假设陈述
    prediction      具体预测（人类可读）
    falsification   什么结果会证伪此假设
    metric          用于验证的指标名（"win_rate" / "sharpe" / "ev" 等）
    threshold       阈值
    direction       ">" / "<" / "=="
    """

    statement: str
    prediction: str
    falsification: str
    metric: str = "win_rate"
    threshold: float = 0.0
    direction: str = ">"

    def check(self, actual_value: float) -> tuple[bool, str]:
        """验证假设是否成立，返回 (passed, explanation)。"""
        if self.direction == ">":
            passed = actual_value > self.threshold
        elif self.direction == "<":
            passed = actual_value < self.threshold
        elif self.direction == "==":
            passed = abs(actual_value - self.threshold) < 0.01
        else:
            passed = False
        expl = (
            f"假设 '{self.statement}' — "
            f"预测 {self.metric} {self.direction} {self.threshold}, "
            f"实际 {actual_value:.4f}, "
            f"{'✅ 通过' if passed else '❌ 未通过'}"
        )
        return passed, expl

    def to_dict(self) -> dict:
        return {
            "statement": self.statement,
            "prediction": self.prediction,
            "falsification": self.falsification,
            "metric": self.metric,
            "threshold": self.threshold,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Hypothesis":
        return cls(
            statement=data["statement"],
            prediction=data["prediction"],
            falsification=data["falsification"],
            metric=data.get("metric", "win_rate"),
            threshold=float(data.get("threshold", 0.0)),
            direction=data.get("direction", ">"),
        )


# ═══════════════════════════════════════════════════════
#  Experiment
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class Experiment:
    """
    一次完整的假设检验实验记录。

    Fields
    ------
    experiment_id     唯一标识（短格式）
    full_id           SHA-256 全 ID（用于数据库主键）
    experiment_type   实验类型
    hypothesis        待检验的假设
    description       人类可读描述
    config            实验配置（JSON-serializable）
    data_snapshot_id  关联的 DataSnapshot ID
    run_ids           关联的 walk-forward run ID 列表
    result_summary    关键结果摘要
    passed            假设是否通过（None = 未评估）
    notes             自由备注
    created_at        创建时间戳
    """

    experiment_id: str
    experiment_type: ExperimentType
    hypothesis: Hypothesis
    description: str = ""
    config: dict = field(default_factory=dict)
    data_snapshot_id: Optional[str] = None
    run_ids: list[str] = field(default_factory=list)
    result_summary: dict = field(default_factory=dict)
    passed: Optional[bool] = None
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def full_id(self) -> str:
        raw = json.dumps(
            {
                "id": self.experiment_id,
                "type": self.experiment_type.value,
                "created_at": self.created_at,
                "hypothesis": self.hypothesis.statement,
            },
            sort_keys=True,
        )
        return sha256(raw.encode()).hexdigest()[:32]

    def evaluate(self) -> tuple[bool, str]:
        """根据 result_summary 验证假设。"""
        metric_val = self.result_summary.get(self.hypothesis.metric)
        if metric_val is None:
            return False, f"结果中缺少指标 '{self.hypothesis.metric}'"
        return self.hypothesis.check(float(metric_val))

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "full_id": self.full_id,
            "experiment_type": self.experiment_type.value,
            "hypothesis": self.hypothesis.to_dict(),
            "description": self.description,
            "config": self.config,
            "data_snapshot_id": self.data_snapshot_id,
            "run_ids": self.run_ids,
            "result_summary": self.result_summary,
            "passed": self.passed,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Experiment":
        hyp_data = data.get("hypothesis", {})
        hyp = (
            Hypothesis.from_dict(hyp_data)
            if hyp_data
            else Hypothesis(statement="", prediction="", falsification="")
        )
        exp_type = ExperimentType(data.get("experiment_type", "alpha"))
        return cls(
            experiment_id=data["experiment_id"],
            experiment_type=exp_type,
            hypothesis=hyp,
            description=data.get("description", ""),
            config=data.get("config", {}),
            data_snapshot_id=data.get("data_snapshot_id"),
            run_ids=data.get("run_ids", []),
            result_summary=data.get("result_summary", {}),
            passed=data.get("passed"),
            notes=data.get("notes", ""),
            created_at=data.get("created_at", ""),
        )


# ═══════════════════════════════════════════════════════
#  便捷工厂
# ═══════════════════════════════════════════════════════


def build_alpha_experiment(
    experiment_id: str,
    hypothesis_statement: str,
    *,
    prediction_metric: str = "win_rate",
    prediction_threshold: float = 55.0,
    description: str = "",
    config: Optional[dict] = None,
    data_snapshot_id: Optional[str] = None,
) -> Experiment:
    """
    快速构建一个 Alpha 假设实验。

    示例：
        exp = build_alpha_experiment(
            "exp_001",
            "Kronos UP 信号的胜率超过 55%",
            prediction_threshold=55.0,
        )
    """
    hyp = Hypothesis(
        statement=hypothesis_statement,
        prediction=f"{prediction_metric} > {prediction_threshold}",
        falsification=f"{prediction_metric} <= {prediction_threshold}",
        metric=prediction_metric,
        threshold=prediction_threshold,
        direction=">",
    )
    return Experiment(
        experiment_id=experiment_id,
        experiment_type=ExperimentType.ALPHA,
        hypothesis=hyp,
        description=description,
        config=config or {},
        data_snapshot_id=data_snapshot_id,
    )
