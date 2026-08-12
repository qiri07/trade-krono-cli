"""
pipeline — 投研流水线核心模块。

包含：
  - orchestrator   : 调度主循环（TA+Kronos 并行）
  - data_fetcher   : K 线数据获取封装
  - scorer         : 打分逻辑（从 merge.py 细化）
  - reporter       : 输出格式化
"""
from trade_krono_cli.pipeline.orchestrator import QuantPipeline
from trade_krono_cli.pipeline.data_fetcher import fetch_stock_data
from trade_krono_cli.pipeline.scorer import score_merged_results
from trade_krono_cli.pipeline.reporter import (
    save_json_report,
    save_html_report,
    print_results_table,
    print_results_summary,
)

__all__ = [
    "QuantPipeline",
    "fetch_stock_data",
    "score_merged_results",
    "save_json_report",
    "save_html_report",
    "print_results_table",
    "print_results_summary",
]
