"""
scoring.base — 评分与风险插件抽象基类。

提供三个 ABC：
  CompositeScorer   : 综合打分器（对单只股票合并结果打分）
  RiskBoostStrategy : 异常标记风险加分策略
  RatingMapper      : LLM Rating → (Signal, confidence) 映射策略

所有具体实现类必须在子类中声明 name 属性，并在 Registry 中注册。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ═══════════════════════════════════════════════════════
# 综合打分器
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class ScoreResult:
    """单次打分的结构化结果。"""

    score: float  # 最终综合分 0–100
    raw_components: dict[str, float] = field(default_factory=dict)
    """各子项原始得分，供调试和回测分析。"""
    strategy_name: str = ""
    """使用的打分策略名称。"""


class CompositeScorer(ABC):
    """
    综合打分器抽象基类。

    实现要点：
      - name: 策略标识符（小写），用于 Registry 注册
      - score(merged, config) → float: 核心打分接口
      - metadata(merged, config) → dict: 返回中间分数（可选，默认 {}）
    """

    name: str = "base"

    def score(self, merged: dict, config: Any = None) -> float:
        """
        对合并后的股票结果打分，返回 0–100 的浮点数。

        Parameters
        ----------
        merged : dict
            merge_results() 输出的单只股票合并结果字典
        config : any
            打分配置（通常为 ScoringConfig 或 StrategyParams）

        Returns
        -------
        float : 0–100
        """
        return self._score_impl(merged, config)

    @abstractmethod
    def _score_impl(self, merged: dict, config: Any) -> float:
        """子类实现核心打分逻辑。"""
        ...

    def describe(self) -> str:
        """返回策略可读描述。"""
        return f"CompositeScorer[{self.name}]"


# ═══════════════════════════════════════════════════════
# 风险加分策略
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class BoostResult:
    """风险加分结果。"""

    boosted_risk: float  # 上调后的风险分 0–100
    base_risk: float  # 原始风险分
    total_boost: float  # 累计加分
    flags_applied: list[str] = field(default_factory=list)
    """实际应用的异常标记列表。"""


class RiskBoostStrategy(ABC):
    """
    异常标记风险加分策略抽象基类。

    实现要点：
      - name: 策略标识符
      - boost(base_risk, flags, params) → float: 核心接口
    """

    name: str = "base"

    def boost(self, base_risk: float, flags: list[str], params: Any = None) -> float:
        """
        根据异常标记上调风险分。

        Parameters
        ----------
        base_risk : float
            原始风险分（0–100）
        flags : list[str]
            异常类型列表，如 ["ST", "SUSPENDED"]
        params : any
            策略特定参数（通常为 RiskBoostParams）

        Returns
        -------
        float : 上调后的风险分（0–100）
        """
        result = self._boost_impl(base_risk, flags, params)
        return result if isinstance(result, float) else result.boosted_risk

    @abstractmethod
    def _boost_impl(self, base_risk: float, flags: list[str], params: Any) -> float | BoostResult:
        """子类实现核心风险加分逻辑。"""
        ...

    def describe(self) -> str:
        return f"RiskBoostStrategy[{self.name}]"


# ═══════════════════════════════════════════════════════
# Rating 映射策略（LLM 输出 → Signal + Confidence）
# ═══════════════════════════════════════════════════════

from trade_krono_cli.ta_decision import Signal  # noqa: E402 (avoid circular at module level)


class RatingMapper(ABC):
    """
    LLM Rating 文本 → (Signal, confidence) 映射策略。

    不同 Prompt 模板可能输出不同格式的 Rating，
    此插件允许切换解析策略而不改主逻辑。
    """

    name: str = "base"

    @abstractmethod
    def map_rating(self, rating_text: str) -> tuple[Signal, float]:
        """
        将 LLM 输出的评级文本映射为标准 Signal + confidence。

        Parameters
        ----------
        rating_text : str
            LLM 输出的评级原文（如 "strong buy", "buy", "持有" 等）

        Returns
        -------
        (Signal, confidence)
          Signal : BUY / HOLD / SELL
          confidence : 0–100 的置信度
        """
        ...

    def describe(self) -> str:
        return f"RatingMapper[{self.name}]"
