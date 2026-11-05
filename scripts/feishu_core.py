"""飞书推送核心模块（已废弃，向后兼容重导出层）。

本模块已废弃，所有功能已下沉至 :mod:`scripts.feishu_utils`。
保留本文件仅为向后兼容，直接从 feishu_utils 重导出所有公共符号。

使用方式（新代码请直接导入 feishu_utils）：
    from scripts.feishu_utils import send_notification, load_config
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保仓库根在 sys.path 中（供直接执行时也能正常工作）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.feishu_utils import (  # noqa: F401
    build_buffett_card,
    build_ci_card,
    build_daily_card,
    build_text_card,
    get_webhook_url,
    load_config,
    push_feishu,
    send_feishu,
    send_notification,
)

__all__ = [
    "build_buffett_card",
    "build_ci_card",
    "build_daily_card",
    "build_text_card",
    "get_webhook_url",
    "load_config",
    "push_feishu",
    "send_feishu",
    "send_notification",
]
