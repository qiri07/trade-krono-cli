"""测试健康检查模块 (health.py)。"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from trade_krono_cli.health import (
    HealthResult,
    check_llm_api,
    check_kronos_import,
    check_database,
    check_disk_space,
    health_summary,
    print_health_report,
)


# ── check_llm_api ────────────────────────────────────────────────────────────

def test_check_llm_api_structure():
    """返回 HealthResult，名称正确，detail 非空。"""
    result = check_llm_api()
    assert isinstance(result, HealthResult)
    assert result.name == "LLM API"
    assert len(result.detail) > 0


def test_check_llm_api_no_keys(monkeypatch):
    """无任何 API Key 时应标记为失败且提示未配置。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    result = check_llm_api()
    assert result.ok is False
    assert "未配置" in result.detail


# ── check_kronos_import ──────────────────────────────────────────────────────

def test_check_kronos_import_structure():
    """应返回正确的 HealthResult 名称，不崩溃。"""
    result = check_kronos_import()
    assert isinstance(result, HealthResult)
    assert result.name == "Kronos"


def test_check_kronos_import_missing():
    """不可导入时应返回失败。"""
    import sys
    # 清除 cli_anything 相关缓存
    mods_to_remove = {k: None for k in list(sys.modules) if k.startswith("cli_anything")}
    for k in mods_to_remove:
        sys.modules.pop(k, None)
    with patch("builtins.__import__", side_effect=ImportError("no mod")):
        result = check_kronos_import()
        assert result.ok is False


# ── check_database ────────────────────────────────────────────────────────────

def test_check_database_ok(tmp_path):
    """正常数据库应通过。"""
    db = tmp_path / "test.db"
    sqlite3 = __import__("sqlite3")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    result = check_database(db)
    assert result.ok is True
    assert result.name == "数据库"


# ── check_disk_space ──────────────────────────────────────────────────────────

def test_check_disk_space_ok(tmp_path):
    """正常目录应通过（空间充足）。"""
    result = check_disk_space(tmp_path, min_gb=0.0001)  # 极低阈值
    assert result.ok is True
    assert "可用" in result.detail


def test_check_disk_space_low(monkeypatch):
    """磁盘空间不足时应返回失败。"""
    orig_statvfs = __import__("os").statvfs

    def fake_statvfs(path):
        s = MagicMock()
        s.f_bavail = 1
        s.f_frsize = 512
        return s

    monkeypatch.setattr("os.statvfs", fake_statvfs)
    result = check_disk_space(Path("/tmp"), min_gb=1.0)
    assert result.ok is False
    assert "可用空间" in result.detail


# ── health_summary ────────────────────────────────────────────────────────────

def test_health_summary_returns_results():
    """health_summary 应返回至少 4 项检查。"""
    from trade_krono_cli.config import get_settings
    results = health_summary(get_settings())
    assert len(results) >= 4
    names = [r.name for r in results]
    assert "LLM API" in names
    assert "Kronos" in names
    assert "数据库" in names
    assert "磁盘空间" in names


# ── print_health_report ───────────────────────────────────────────────────────

def test_print_health_report_all_ok(capsys):
    """全部通过时返回 True 并打印 OK。"""
    results = [
        HealthResult("A", True, "OK"),
        HealthResult("B", True, "OK"),
    ]
    ok = print_health_report(results)
    assert ok is True
    captured = capsys.readouterr()
    assert "全部通过" in captured.out


def test_print_health_report_has_failure(capsys):
    """存在失败时返回 False。"""
    results = [
        HealthResult("A", True, "OK"),
        HealthResult("B", False, "ERROR"),
    ]
    ok = print_health_report(results)
    assert ok is False
    captured = capsys.readouterr()
    assert "存在问题" in captured.out
