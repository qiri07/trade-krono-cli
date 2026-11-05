#!/usr/bin/env python3
"""增量同步：补齐 2026-09-17 及之后缺失的 K 线数据（已废弃，向后兼容包装）。

本脚本已废弃，所有功能已合并至 :mod:`scripts.fill_missing_latest`。
请使用：
    uv run python scripts/fill_missing_latest.py \
        --before 2026-09-17 --start 2026-09-15 --end 2026-09-17
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 注入特定日期参数后委托给 fill_missing_latest.main
sys.argv = [
    "fill_missing_latest.py",
    "--before", "2026-09-17",
    "--start", "2026-09-15",
    "--end", "2026-09-17",
]

from scripts.fill_missing_latest import main  # noqa: E402

__all__ = ["main"]

if __name__ == "__main__":
    main()
