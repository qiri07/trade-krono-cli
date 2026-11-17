"""six_dim 选股 — 模块入口。

支持 `uv run python -m scripts.six_dim` 运行方式。
"""

from __future__ import annotations

import sys

from scripts.six_dim.cli import main

if __name__ == "__main__":
    sys.exit(main())
