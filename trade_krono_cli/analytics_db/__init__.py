from __future__ import annotations

from trade_krono_cli.analytics_db._helpers import _HAS_DUCKDB, _duckdb_available, _ensure_duckdb
from trade_krono_cli.analytics_db.engine import (
    ParquetPaths,
    ParquetWriter,
    ResearchAnalytics,
    clear_analytics_singleton,
    get_analytics,
)

__all__ = [
    "ParquetPaths",
    "ParquetWriter",
    "ResearchAnalytics",
    "_duckdb_available",
    "_ensure_duckdb",
    "_HAS_DUCKDB",
    "get_analytics",
    "clear_analytics_singleton",
]
