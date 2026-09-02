"""
trade_krono_cli.utils — 共享工具函数。

无外部依赖（仅 stdlib + typing），供 pipeline_config / pipeline.config_loader / cli_commands 等模块使用。
避免循环导入：所有 consuming 模块直接从这里导入，而非互相引用。

导出命名规范：
  - 公共 API：parse_range / parse_comma_list / parse_float / merge_with_nested
  - 内部别名（_parse_*）保留在 parser_helpers 中供旧调用方使用
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_range(s: str) -> tuple[float, float] | None:
    """将逗号分隔字符串解析为 (float, float) 区间，失败返回 None。"""
    if not s or not s.strip():
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def parse_comma_list(s: str) -> list[str]:
    """将逗号分隔字符串解析为去空白 list[str]。"""
    if not s or not s.strip():
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def parse_float(s: str) -> float | None:
    """将字符串解析为 float，失败返回 None。"""
    if not s or not s.strip():
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def merge_with_nested(obj: Any, overrides: dict) -> Any:  # noqa: ANN401 — type is unknown at merge-time; must accept dataclass | Pydantic BaseModel | plain object
    """
    递归合并嵌套 dict 到 dataclass 实例。

    支持 "__" 嵌套路径，例如 {"risk__weights__volatility": 0.35}。
    """
    if not hasattr(obj, "merge"):
        return obj
    nested: dict[str, Any] = {}
    flat: dict[str, Any] = {}
    for k, v in overrides.items():
        if "__" in k:
            outer, inner = k.split("__", 1)
            nested.setdefault(outer, {})[inner] = v
        else:
            flat[k] = v
    merged = obj.merge(**flat, **nested)
    return merged


def safe_float(value) -> float | None:
    """将值安全地转为 float，None / NaN / Inf 均返回 None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f == float("inf") or f == float("-inf"):
        return None
    return f


def pd_to_datetime_safe(values: list) -> list[datetime]:
    """将日期列表安全转为 datetime（使用 pandas）。"""
    import pandas as pd

    ts = pd.to_datetime(values)
    return ts.tolist()


def strip_ticker_prefix(ticker: str) -> str:
    """去除 ticker 前缀（sh./sz./bj.），返回纯6位代码。

    示例：
        >>> strip_ticker_prefix("sh.600519")
        '600519'
        >>> strip_ticker_prefix("600519")
        '600519'
    """
    for prefix in ("sh.", "sz.", "bj.", "SH.", "SZ.", "BJ."):
        if ticker.startswith(prefix):
            return ticker[len(prefix) :]
    return ticker


def add_ticker_prefix(code: str) -> str:
    """为纯6位股票代码添加交易所前缀。

    规则（与 A 股市场惯例一致）：
      - 6xx / 5xx → sh.
      - 9xx       → bj.
      - 其他      → sz.

    示例：
        >>> add_ticker_prefix("600519")
        'sh.600519'
        >>> add_ticker_prefix("000858")
        'sz.000858'
        >>> add_ticker_prefix("920208")
        'bj.920208'
    """
    code = code.strip()
    if len(code) == 6 and code.isdigit():
        if code[0] in ("6", "5"):
            return f"sh.{code}"
        if code[0] == "9":
            return f"bj.{code}"
        return f"sz.{code}"
    # 已有前缀或非数字，原样返回
    return code
