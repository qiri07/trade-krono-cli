#!/usr/bin/env python3
"""港股筛选器 CLI 入口（向后兼容薄包装）。

实际实现已统一到 :mod:`scripts.hk_daily_screen`，避免两套入口逻辑漂移。

用法：
    uv run python scripts/hk_screen/cli.py                    # 全量筛选
    uv run python scripts/hk_screen/cli.py --top 20           # 只输出前 20 名
    uv run python scripts/hk_screen/cli.py --no-push          # 不推送飞书
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.hk_daily_screen import main as _main


def main() -> int:
    """转发到统一入口 :func:`scripts.hk_daily_screen.main`。

    Returns:
        进程退出码。
    """
    return _main()


if __name__ == "__main__":
    sys.exit(main())
