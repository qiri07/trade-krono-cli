#!/usr/bin/env python3
"""优化版全量 K 线同步脚本（已废弃，向后兼容包装）。

本脚本已废弃，所有功能已合并至 :mod:`scripts.full_history_sync`。
保留本文件仅为向后兼容，直接调用 full_history_sync.main()。

原优化点（沪市优先 tonghuashun、北交所只用 tonghuashun、缩短超时等）
已吸收进 full_history_sync 的 provider 降级链中。

用法：
  uv run python scripts/full_sync_v2.py   # 等价于 uv run python scripts/full_history_sync.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.full_history_sync import main  # noqa: F401

__all__ = ["main"]

if __name__ == "__main__":
    main()
