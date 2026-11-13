"""fill_missing 选股 — 补拉计划加载。

读取 ``check_data_status.py`` 产出的补拉计划 JSON。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sync_plan(plan_path: str | Path) -> tuple[list[str], list[str]]:
    """读取 check_data_status.py 产出的补拉计划 JSON，返回 (failed, need_today)。

    计划文件结构：
        {"plan": {"fetch_kline": {"tickers": [...],
                                   "reasons": {"failed": [...], "need_today": [...]}}}}

    Args:
        plan_path: 计划 JSON 文件路径。

    Returns:
        (failed_tickers, need_today_tickers) 两个列表；文件缺失或字段缺省对应列表为空。
    """
    path = Path(plan_path)
    if not path.exists():
        print(f"⚠️  补拉计划文件不存在: {path}，回退到全库扫描")
        return [], []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"⚠️  补拉计划 JSON 解析失败: {e}，回退到全库扫描")
        return [], []

    fetch: dict[str, Any] = (data.get("plan") or {}).get("fetch_kline") or {}
    reasons: dict[str, Any] = fetch.get("reasons") or {}
    failed = [t for t in (reasons.get("failed") or []) if isinstance(t, str)]
    need_today = [t for t in (reasons.get("need_today") or []) if isinstance(t, str)]
    if not failed and not need_today:
        # reasons 缺省时直接用顶层 tickers 列表（全部视为需补今日）
        fallback = [t for t in (fetch.get("tickers") or []) if isinstance(t, str)]
        need_today = fallback
    print(
        f"📋 补拉计划: 历史失败 {len(failed)} 只, 需补今日 {len(need_today)} 只"
        f"（来源: {path.name}）"
    )
    return failed, need_today
