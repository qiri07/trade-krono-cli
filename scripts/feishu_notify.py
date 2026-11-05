"""飞书 Webhook 通知 CLI（已废弃，向后兼容重导出层）。

本模块已废弃，所有功能已合并至 :mod:`scripts.feishu_cli`（统一入口）。
保留本文件仅为向后兼容，直接从 feishu_cli / feishu_utils 重导出公共符号。

迁移建议：
    # 旧：
    python scripts/feishu_notify.py buffett --result-file xxx.txt --url "https://..."
    # 新：
    python scripts/feishu_cli.py buffett --result-file xxx.txt --url "https://..."
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保仓库根在 sys.path 中（供直接执行时也能正常工作）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.feishu_cli import _read_top3_from_results  # noqa: F401
from scripts.feishu_utils import (  # noqa: F401
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    send_feishu,
)

__all__ = [
    "_read_top3_from_results",
    "build_buffett_card",
    "build_ci_card",
    "build_daily_card",
    "send_feishu",
]
