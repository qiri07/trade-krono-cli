"""
研究数据库入口（向后兼容薄包装）。

本文件已被 trade_krono_cli/research_db/ 包替代。
为保持旧 import 路径兼容，此处仅重新导出所有公开 API。
"""

from __future__ import annotations

# 所有导出由 research_db 包提供
from trade_krono_cli.research_db.__init__ import (  # noqa: F401
    REASONING_TRUNCATE_LEN,
    RESEARCH_TABLES,
    ResearchDatabase,
    _validate_table_name,
    clear_research_singleton,
    get_research,
)

__all__ = [
    "ResearchDatabase",
    "get_research",
    "clear_research_singleton",
    "REASONING_TRUNCATE_LEN",
    "RESEARCH_TABLES",
    "_validate_table_name",
]
