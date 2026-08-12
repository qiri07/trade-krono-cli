"""
reporter — 输出格式化。

从 report.py 导出，提供结构化输出接口。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from trade_krono_cli.report import (
    save_json as _save_json,
    save_html as _save_html,
    print_table as _print_table,
    print_summary as _print_summary,
)


def save_json_report(merged: list[dict], path: str) -> str:
    """保存 JSON 报告。"""
    return _save_json(merged, path)


def save_html_report(merged: list[dict], path: str, date: str) -> str:
    """保存 HTML 报告。"""
    return _save_html(merged, path, date)


def print_results_table(merged: list[dict]) -> None:
    """在控制台输出富文本表格。"""
    _print_table(merged)


def print_results_summary(merged: list[dict], date: str) -> None:
    """打印简要摘要。"""
    _print_summary(merged, date)
