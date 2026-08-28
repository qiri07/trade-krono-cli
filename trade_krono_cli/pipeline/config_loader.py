"""
pipeline.config_loader — 配置解析与加载工具。

提供：
  · 字符串解析辅助函数（_parse_range / _parse_comma_list / _parse_float）
  · 嵌套 dict 合并逻辑（_merge_with_nested）
  · YAML / JSON 配置文件加载

PipelineConfig 类本身保留在 trade_krono_cli.pipeline_config 模块中，
以保持向后兼容的 import 路径。
"""

from __future__ import annotations

from typing import Any


def _parse_range(s: str) -> tuple[float, float] | None:
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


def _parse_comma_list(s: str) -> list[str]:
    """将逗号分隔字符串解析为去空白 list[str]。"""
    if not s or not s.strip():
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _parse_float(s: str) -> float | None:
    """将字符串解析为 float，失败返回 None。"""
    if not s or not s.strip():
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def _merge_with_nested(obj: Any, overrides: dict) -> Any:
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
