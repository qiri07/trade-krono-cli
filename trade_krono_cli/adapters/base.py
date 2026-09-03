"""适配器基类 — 定义与外部项目交互的接口。

所有外部依赖（cli_anything / TradingAgents-astock / Kronos）均通过
本层封装，内部业务代码只依赖本地接口，不感知外部项目的导入路径。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TradingAgentsAdapter(ABC):
    """TradingAgents 适配器接口。"""

    @abstractmethod
    def load(self, settings: Any) -> None:  # noqa: ANN401 — 外部模块 settings 类型未知
        """加载外部模块并建立可用能力。"""

    @abstractmethod
    def build_config(self, **kwargs: Any) -> dict:  # noqa: ANN401 — 外部框架配置字典结构动态
        """构建分析配置字典。"""

    @abstractmethod
    def run_analysis(self, ticker: str, config: dict) -> dict:
        """执行单只股票分析，返回包含 final_state 等的结果 dict。"""


class KronosAdapter(ABC):
    """Kronos 预测适配器接口。"""

    @abstractmethod
    def load_model(self, settings: Any) -> None:  # noqa: ANN401 — 外部模块 settings 类型未知
        """懒加载 Kronos 模型，内部处理 device 选择和路径注入。"""

    @abstractmethod
    def predict(
        self,
        df: Any,  # noqa: ANN401 — 外部框架 DataFrame 类型
        x_timestamp: Any,  # noqa: ANN401 — 外部框架时间戳类型
        y_timestamp: Any,  # noqa: ANN401 — 外部框架时间戳类型
        pred_len: int,
        T: float,
        top_p: float,
        sample_count: int = 1,
    ) -> Any:
        """单只股票预测，返回原始预测 DataFrame。"""

    @abstractmethod
    def predict_batch(
        self,
        df_list: list,
        x_timestamp_list: list,
        y_timestamp_list: list,
        pred_len: int,
        T: float,
        top_p: float,
        sample_count: int = 1,
    ) -> list:
        """批量预测，返回列表形式预测结果（每个元素为 DataFrame）。"""
