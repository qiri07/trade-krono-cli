"""scripts/backup_cache.py 单元测试：一致性快照、去重、按族清理、命名保护。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import backup_cache

if TYPE_CHECKING:
    import pytest


def _make_db(path: Path, rows: int = 3) -> None:
    """创建一个带数据的 SQLite 库。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _make_backup(backup_dir: Path, stem: str, ts: str, suffix: str = ".db") -> Path:
    """按本脚本命名规则伪造一个备份文件。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{stem}_{ts}{suffix}"
    path.write_bytes(b"x")
    return path


class TestBackupFile:
    """backup_file() 行为。"""

    def test_sqlite_snapshot_is_consistent(self, tmp_path: Path) -> None:
        db = tmp_path / "pipeline_cache.db"
        _make_db(db, rows=5)
        backup_dir = tmp_path / "backups"

        result = backup_cache.backup_file(db, backup_dir)

        assert result is not None
        assert result.exists()
        assert backup_cache._BACKUP_NAME_RE.match(result.name) is not None
        assert not list(backup_dir.glob("*.tmp"))

        conn = sqlite3.connect(str(result))
        try:
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()

    def test_skips_when_source_unchanged(self, tmp_path: Path) -> None:
        db = tmp_path / "pipeline_cache.db"
        _make_db(db)
        backup_dir = tmp_path / "backups"

        first = backup_cache.backup_file(db, backup_dir)
        second = backup_cache.backup_file(db, backup_dir)

        assert first is not None
        assert second == first
        assert len(backup_cache.list_backups(backup_dir, "pipeline_cache")) == 1

    def test_force_creates_new_snapshot(self, tmp_path: Path) -> None:
        db = tmp_path / "pipeline_cache.db"
        _make_db(db)
        backup_dir = tmp_path / "backups"

        first = backup_cache.backup_file(db, backup_dir)
        second = backup_cache.backup_file(db, backup_dir, force=True)

        assert first is not None
        assert second is not None
        assert second != first
        assert len(backup_cache.list_backups(backup_dir, "pipeline_cache")) == 2

    def test_missing_source_returns_none(self, tmp_path: Path) -> None:
        assert backup_cache.backup_file(tmp_path / "nope.db", tmp_path / "backups") is None

    def test_json_backup_roundtrip(self, tmp_path: Path) -> None:
        src = tmp_path / "metadata.json"
        payload = {"sh.600519": {"pe": 28.5}}
        src.write_text(json.dumps(payload), encoding="utf-8")
        backup_dir = tmp_path / "backups"

        result = backup_cache.backup_file(src, backup_dir)

        assert result is not None
        assert json.loads(result.read_text(encoding="utf-8")) == payload
        assert not list(backup_dir.glob("*.tmp"))

    def test_json_backup_rejects_invalid_json(self, tmp_path: Path) -> None:
        src = tmp_path / "metadata.json"
        src.write_text('{"sh.600519": ', encoding="utf-8")
        backup_dir = tmp_path / "backups"

        assert backup_cache.backup_file(src, backup_dir) is None
        assert backup_cache.list_backups(backup_dir, "metadata") == []


class TestListAndPrune:
    """list_backups() / prune_backups() 的排序与命名保护。"""

    def test_list_sorted_by_filename_timestamp_not_mtime(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        old = _make_backup(backup_dir, "pipeline_cache", "20260101_000000")
        new = _make_backup(backup_dir, "pipeline_cache", "20260102_000000")
        # 人为把「新」备份的 mtime 调早，验证排序不依赖 mtime
        os.utime(new, (1000, 1000))
        os.utime(old, (time.time(), time.time()))

        assert backup_cache.list_backups(backup_dir, "pipeline_cache") == [new, old]

    def test_prune_keeps_per_family(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        for i in range(12):
            _make_backup(backup_dir, "pipeline_cache", f"20260101_0000{i:02d}")
        for i in range(3):
            _make_backup(backup_dir, "metadata", f"20260101_0000{i:02d}", suffix=".json")

        deleted = backup_cache.prune_backups(backup_dir, keep=10)

        assert len(deleted) == 2
        assert len(backup_cache.list_backups(backup_dir, "pipeline_cache")) == 10
        # metadata 族不足 keep，不受 pipeline_cache 清理影响
        assert len(backup_cache.list_backups(backup_dir, "metadata")) == 3

    def test_prune_never_deletes_manual_snapshots(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        manual = _make_backup(backup_dir, "pipeline_cache_pre1500clean", "20261023_084821")
        dot_manual = backup_dir / "pipeline_cache.pre_merge_20261022_185038.db"
        dot_manual.write_bytes(b"x")
        for i in range(3):
            _make_backup(backup_dir, "pipeline_cache", f"20260101_0000{i:02d}")

        backup_cache.prune_backups(backup_dir, keep=1)

        assert manual.exists()
        assert dot_manual.exists()
        assert len(backup_cache.list_backups(backup_dir, "pipeline_cache")) == 1

    def test_prune_keep_zero_disables_cleanup(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        for i in range(3):
            _make_backup(backup_dir, "pipeline_cache", f"20260101_0000{i:02d}")

        assert backup_cache.prune_backups(backup_dir, keep=0) == []
        assert len(backup_cache.list_backups(backup_dir, "pipeline_cache")) == 3


class TestBackupAll:
    """backup_all() 使用缓存目录并汇总结果。"""

    def test_backs_up_both_families(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(backup_cache, "get_cache_dir", lambda: tmp_path)
        _make_db(tmp_path / "pipeline_cache.db")
        (tmp_path / "metadata.json").write_text('{"sh.600519": null}', encoding="utf-8")

        results = backup_cache.backup_all(keep=10)

        assert results["pipeline_cache.db"] is not None
        assert results["metadata.json"] is not None
        assert len(backup_cache.list_backups(tmp_path / "backups", "pipeline_cache")) == 1
        assert len(backup_cache.list_backups(tmp_path / "backups", "metadata")) == 1

    def test_missing_sources_report_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup_cache, "get_cache_dir", lambda: tmp_path)

        results = backup_cache.backup_all(keep=10)

        assert results == {"pipeline_cache.db": None, "metadata.json": None}

    def test_second_run_skips_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup_cache, "get_cache_dir", lambda: tmp_path)
        _make_db(tmp_path / "pipeline_cache.db")

        backup_cache.backup_all(keep=10)
        backup_cache.backup_all(keep=10)

        assert len(backup_cache.list_backups(tmp_path / "backups", "pipeline_cache")) == 1


class TestCheckStatus:
    """check_status() 与命令行退出码。"""

    def test_verify_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(backup_cache, "get_cache_dir", lambda: tmp_path)
        _make_db(tmp_path / "pipeline_cache.db")
        (tmp_path / "metadata.json").write_text('{"sh.600519": null}', encoding="utf-8")
        backup_cache.backup_file(tmp_path / "pipeline_cache.db", tmp_path / "backups")

        assert backup_cache.check_status(verify=True) == 0
        assert "integrity_check: ok" in capsys.readouterr().out

    def test_missing_source_returns_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup_cache, "get_cache_dir", lambda: tmp_path)

        assert backup_cache.check_status() == 1

    def test_main_returns_one_when_backup_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            backup_cache,
            "backup_all",
            lambda *a, **kw: {"pipeline_cache.db": None, "metadata.json": None},
        )

        assert backup_cache.main([]) == 1

    def test_main_check_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(backup_cache, "check_status", lambda **kw: 0)

        assert backup_cache.main(["--check-only"]) == 0


class TestGetCacheDir:
    """get_cache_dir() 的测试隔离与回退。"""

    def test_falls_back_when_settings_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trade_krono_cli.config as config_module

        def _boom() -> None:
            msg = "no env"
            raise RuntimeError(msg)

        monkeypatch.setattr(config_module, "get_settings", _boom)

        assert backup_cache.get_cache_dir() == Path("outputs/cache")
