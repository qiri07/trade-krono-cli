"""TradingAgents 分析配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class TAConfig:
    """TradingAgents 多 Agent 分析参数。"""

    llm_provider: str = "deepseek"
    deep_think_llm: str = "deepseek-chat"
    quick_think_llm: str = "deepseek-chat"
    max_debate_rounds: int = 1
    output_language: str = "Chinese"

    def merge(self, **overrides) -> "TAConfig":
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update({k: v for k, v in overrides.items() if v is not None})
        return TAConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.llm_provider or not self.llm_provider.strip():
            errors.append("ta.llm_provider 不能为空")
        if not self.output_language or not self.output_language.strip():
            errors.append("ta.output_language 不能为空")
        if self.max_debate_rounds < 1:
            errors.append(f"ta.max_debate_rounds={self.max_debate_rounds} 必须 >= 1")
        return errors
