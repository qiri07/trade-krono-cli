"""研究数据库入口（向后兼容薄包装）。

本文件已被 trade_krono_cli/research_db/ 包替代。
为保持旧 import 路径兼容，此处仅重新导出所有公开 API。
"""

from __future__ import annotations

import warnings

# 所有导出由 research_db 包提供
warnings.warn(
    "trade_krono_cli.research_db is deprecated; use trade_krono_cli.research_db package instead.",
    DeprecationWarning,
    stacklevel=2,
)
from trade_krono_cli.research_db.__init__ import (  # noqa: E402, F401
    REASONING_TRUNCATE_LEN,
    RESEARCH_TABLES,
    ResearchDatabase,
    clear_research_singleton,
    get_research,
    validate_table_name,
)

__all__ = [
    "REASONING_TRUNCATE_LEN",
    "RESEARCH_TABLES",
    "ResearchDatabase",
    "clear_research_singleton",
    "get_research",
    "validate_table_name",
]
