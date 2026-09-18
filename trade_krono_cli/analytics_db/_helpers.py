"""Analytics DB helpers — DuckDB availability checks."""

from __future__ import annotations

# ── 可选 DuckDB 导入 ───────────────────────────────────────────────────────────
try:
    import duckdb  # noqa: PLC0414 (optional dependency)

    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False
    duckdb = None  # type: ignore[misc]


# Expose _HAS_DUCKDB for test patching (backward compat)
__all__ = ("_duckdb_available", "_ensure_duckdb", "_HAS_DUCKDB", "duckdb")


def _duckdb_available() -> bool:
    """检查 DuckDB 是否已安装。"""
    return _HAS_DUCKDB


def _ensure_duckdb() -> None:
    """确保 DuckDB 可用，否则抛出 RuntimeError。"""
    if not _HAS_DUCKDB:
        msg = (
            "DuckDB 未安装，无法使用 Analytics 引擎。\n请运行: pip install duckdb 或 uv add duckdb"
        )
        raise RuntimeError(msg)
