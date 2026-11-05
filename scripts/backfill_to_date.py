#!/usr/bin/env python3
"""把 K 线缓存中滞后于目标日期的标的增量补齐到指定日期（向后兼容包装）。

本脚本已废弃，所有功能已合并至 :mod:`scripts.data_backfill`。
保留本文件仅为向后兼容，直接调用 data_backfill.main()。

与 ``fill_missing_and_today.py`` 的区别：本脚本的结束日**显式可控**（默认取
最近一个已收盘交易日，即昨天/周五），不会把盘中未收盘的部分日线写进缓存。

用法：
  uv run python scripts/backfill_to_date.py                  # 补齐到最近交易日
  uv run python scripts/backfill_to_date.py --dry-run        # 只统计不写库
  uv run python scripts/backfill_to_date.py --end-date 2026-11-02
  uv run python scripts/backfill_to_date.py --tickers "sh.510300,bj.920000"
  uv run python scripts/backfill_to_date.py --workers 8 --limit 20
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.data_backfill import main  # noqa: F401

__all__ = ["main"]

if __name__ == "__main__":
    main()
