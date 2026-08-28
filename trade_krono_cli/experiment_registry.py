"""
ExperimentRegistry — 实验追踪与比较引擎。

核心职责：
  · 记录每次实验的假设（hypothesis）、配置、数据快照、结果
  · 支持按假设维度比较多次实验的 walk-forward 结果
  · 与 DataSnapshot / WalkForwardEngine / ArtifactManifest 联动

实验类型：
  · alpha_experiment   — 测试新的 alpha 信号/策略
  · model_experiment   — 测试不同模型（Kronos 版本 / LLM provider）
  · config_experiment  — 测试不同评分/风险参数
  · data_experiment    — 测试不同数据源或 cut_date

设计原则：
  · 每个实验有唯一 experiment_id（人工可读的短 ID + SHA-256 全 ID）
  · 假设可被 falsified（证伪）—— 记录"什么条件下假设不成立"
  · 结果可复现：experiment_id → 完整配置 → 完整结果
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from trade_krono_cli.data_snapshot import DataSnapshot
from trade_krono_cli.domain.experiment import Hypothesis
from trade_krono_cli.domain.types import ExperimentType

# ═══════════════════════════════════════════════════════
#  实验记录
# ═══════════════════════════════════════════════════════


@dataclass
class ExperimentRecord:
    """
    一次实验的完整记录。

    字段
    ----
    experiment_id     唯一标识（短格式 + 全 ID）
    experiment_type   实验类型
    hypothesis        待检验的假设
    description       人类可读描述
    config            实验配置（JSON-serializable）
    data_snapshot_id  关联的 DataSnapshot ID
    run_ids           关联的 walk-forward run ID 列表
    result_summary    关键结果摘要（win_rate, sharpe, ev, ...）
    passed            假设是否通过
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
        """SHA-256 全 ID（用于数据库主键）。"""
        raw = json.dumps(
            {
                "id": self.experiment_id,
                "type": self.experiment_type.value,
                "created_at": self.created_at,
                "hypothesis": self.hypothesis.statement,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def evaluate(self) -> tuple[bool, str]:
        """根据 result_summary 验证假设。"""
        metric_val = self.result_summary.get(self.hypothesis.metric)
        if metric_val is None:
            return False, f"结果中缺少指标 '{self.hypothesis.metric}'"
        return self.hypothesis.check(metric_val)

    def to_dict(self) -> dict:
        d = {
            "experiment_id": self.experiment_id,
            "full_id": self.full_id,
            "experiment_type": self.experiment_type.value,
            "hypothesis": {
                "statement": self.hypothesis.statement,
                "prediction": self.hypothesis.prediction,
                "falsification": self.hypothesis.falsification,
                "metric": self.hypothesis.metric,
                "threshold": self.hypothesis.threshold,
                "direction": self.hypothesis.direction,
            },
            "description": self.description,
            "config": self.config,
            "data_snapshot_id": self.data_snapshot_id,
            "run_ids": self.run_ids,
            "result_summary": self.result_summary,
            "passed": self.passed,
            "notes": self.notes,
            "created_at": self.created_at,
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentRecord":
        hyp_data = data.pop("hypothesis", {})
        hyp = (
            Hypothesis(**hyp_data)
            if hyp_data
            else Hypothesis(statement="", prediction="", falsification="")
        )
        exp_type = ExperimentType(data.pop("experiment_type", "alpha"))
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
#  ExperimentRegistry
# ═══════════════════════════════════════════════════════


class ExperimentRegistry:
    """
    实验注册中心。

    功能：
      · register(experiment)     注册新实验
      · add_run(experiment_id, run_id)  关联 walk-forward run
      · set_result(experiment_id, summary)  填入结果并验证假设
      · list_experiments()       列出所有实验
      · compare(experiment_ids)  横向比较多个实验
      · save(path) / load(path)  持久化到 JSON 文件
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._experiments: dict[str, ExperimentRecord] = {}
        self._db_path = db_path

    def register(
        self,
        experiment_id: str,
        hypothesis: Hypothesis,
        exp_type: ExperimentType = ExperimentType.ALPHA,
        description: str = "",
        config: Optional[dict] = None,
        data_snapshot: Optional[DataSnapshot] = None,
    ) -> ExperimentRecord:
        """注册新实验。"""
        record = ExperimentRecord(
            experiment_id=experiment_id,
            experiment_type=exp_type,
            hypothesis=hypothesis,
            description=description,
            config=config or {},
            data_snapshot_id=data_snapshot.snapshot_id if data_snapshot else None,
        )
        self._experiments[experiment_id] = record
        logger.info(
            f"📝 实验已注册: {experiment_id} [{exp_type.value}] — {hypothesis.statement[:60]}"
        )
        return record

    def add_run(self, experiment_id: str, run_id: str) -> None:
        """关联一次 walk-forward run。"""
        if experiment_id in self._experiments:
            self._experiments[experiment_id].run_ids.append(run_id)

    def set_result(
        self,
        experiment_id: str,
        summary: dict,
    ) -> tuple[bool, str]:
        """
        填入实验结果，自动验证假设。

        Parameters
        ----------
        experiment_id  实验 ID
        summary        结果摘要 dict，需包含 hypothesis.metric 对应的值

        Returns
        -------
        (passed, explanation)
        """
        if experiment_id not in self._experiments:
            raise KeyError(f"实验不存在: {experiment_id}")
        record = self._experiments[experiment_id]
        record.result_summary = summary
        passed, expl = record.evaluate()
        record.passed = passed
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"📊 实验 {experiment_id}: {status} — {expl}")
        return passed, expl

    def get(self, experiment_id: str) -> Optional[ExperimentRecord]:
        return self._experiments.get(experiment_id)

    def list_experiments(
        self,
        exp_type: Optional[ExperimentType] = None,
        only_passed: Optional[bool] = None,
    ) -> list[ExperimentRecord]:
        """列出实验，支持按类型和通过状态过滤。"""
        results = list(self._experiments.values())
        if exp_type:
            results = [r for r in results if r.experiment_type == exp_type]
        if only_passed is not None:
            results = [r for r in results if r.passed == only_passed]
        return results

    def compare(self, experiment_ids: list[str]) -> dict:
        """
        横向比较多个实验的关键指标。

        Returns
        -------
        {
          experiment_id: {
            "win_rate": ...,
            "sharpe": ...,
            "ev": ...,
            "passed": ...,
          }
        }
        """
        comparison = {}
        for eid in experiment_ids:
            rec = self._experiments.get(eid)
            if rec is None:
                continue
            comparison[eid] = {
                "type": rec.experiment_type.value,
                "hypothesis": rec.hypothesis.statement,
                **rec.result_summary,
                "passed": rec.passed,
                "n_runs": len(rec.run_ids),
            }
        return comparison

    def save(self, path: Path) -> None:
        """持久化到 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {eid: rec.to_dict() for eid, rec in self._experiments.items()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"💾 实验注册表已保存: {path}")

    def load(self, path: Path) -> None:
        """从 JSON 文件加载。"""
        if not path.exists():
            return
        data = json.loads(path.read_text())
        for eid, rec_data in data.items():
            self._experiments[eid] = ExperimentRecord.from_dict(rec_data)
        logger.info(f"📂 实验注册表已加载: {len(self._experiments)} 条实验")


# ═══════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════


def register_alpha_experiment(
    registry: ExperimentRegistry,
    experiment_id: str,
    hypothesis_statement: str,
    *,
    prediction_metric: str = "win_rate",
    prediction_threshold: float = 55.0,
    prediction_direction: str = ">",
    description: str = "",
    config: Optional[dict] = None,
    data_snapshot: Optional[DataSnapshot] = None,
) -> ExperimentRecord:
    """
    快速注册一个 Alpha 假设实验。

    示例：
        reg = ExperimentRegistry()
        exp = register_alpha_experiment(
            reg, "exp_001",
            "Kronos UP 信号的胜率超过 55%",
            prediction_threshold=55.0,
            data_snapshot=snapshot,
        )
    """
    hyp = Hypothesis(
        statement=hypothesis_statement,
        prediction=f"{prediction_metric} {prediction_direction} {prediction_threshold}",
        falsification=f"{prediction_metric} <= {prediction_threshold}",
        metric=prediction_metric,
        threshold=prediction_threshold,
        direction=prediction_direction,
    )
    return registry.register(
        experiment_id=experiment_id,
        hypothesis=hyp,
        exp_type=ExperimentType.ALPHA,
        description=description,
        config=config,
        data_snapshot=data_snapshot,
    )
