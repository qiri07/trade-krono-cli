"""tests for scripts.check_data_status — 补拉计划生成与保存。"""

from __future__ import annotations

import json

import pytest

import scripts.check_data_status as cds


class TestBuildSyncPlan:
    """build_sync_plan 字段完整性与 metadata mode 判定。"""

    @pytest.fixture()
    def fake_db(self, tmp_path, monkeypatch) -> None:
        """指向 tmp DB，不污染生产缓存。"""
        db_path = tmp_path / "pipeline_cache.db"
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS kline_cache (
                ticker TEXT, start TEXT, end TEXT, data BLOB, PRIMARY KEY(ticker)
            )"""
        )
        # 一只正常票（100+ 条）+ 一只失败票（<100 条）
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519','2020-01-01','2026-10-30',X'00')"
        )
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sz.000001','2026-10-25','2026-10-30',X'00')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(cds, "CACHE_DB", db_path)

    def test_plan_structure(self, fake_db, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(cds, "METADATA_FILE", tmp_path / "metadata.json")
        plan = cds.build_sync_plan(ttl_hours=24.0)
        assert plan["kline"]["total_tickers"] == 2
        assert plan["kline"]["latest_date"] == "2026-10-30"
        assert plan["plan"]["fetch_kline"]["reasons"]["failed"] is not None
        assert plan["plan"]["fetch_metadata"]["mode"] in ("full", "partial", "skip")

    def test_metadata_mode_skip_when_fresh(self, fake_db, monkeypatch, tmp_path) -> None:
        meta = tmp_path / "metadata.json"
        meta.write_text(json.dumps({f"sh.{i:06d}": {"pe": 1.0} for i in range(3000)}))
        monkeypatch.setattr(cds, "METADATA_FILE", meta)
        monkeypatch.setattr(cds, "metadata_age_hours", lambda: 1.0)
        plan = cds.build_sync_plan(ttl_hours=24.0)
        assert plan["plan"]["fetch_metadata"]["mode"] == "skip"
        assert plan["metadata"]["is_stale"] is False

    def test_metadata_mode_full_when_missing(self, fake_db, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(cds, "METADATA_FILE", tmp_path / "nope.json")
        monkeypatch.setattr(cds, "metadata_age_hours", lambda: None)
        plan = cds.build_sync_plan(ttl_hours=24.0)
        assert plan["plan"]["fetch_metadata"]["mode"] == "full"

    def test_metadata_mode_full_when_low_entries(self, fake_db, monkeypatch, tmp_path) -> None:
        meta = tmp_path / "metadata.json"
        meta.write_text(json.dumps({"sh.600519": {"pe": 1.0}}))
        monkeypatch.setattr(cds, "METADATA_FILE", meta)
        monkeypatch.setattr(cds, "metadata_age_hours", lambda: 0.5)
        plan = cds.build_sync_plan(ttl_hours=24.0)
        # 条目 <100 视为污染 → 全量
        assert plan["plan"]["fetch_metadata"]["mode"] == "full"


class TestSavePlan:
    """save_plan 写 JSON 到指定目录。"""

    def test_writes_and_returns_path(self, tmp_path) -> None:
        plan = {"kline": {}, "metadata": {}, "plan": {}}
        out = cds.save_plan(plan, out_dir=tmp_path)
        assert out.exists()
        assert out.name.startswith("sync_plan_")
        reloaded = json.loads(out.read_text(encoding="utf-8"))
        assert reloaded == plan


class TestPrintPlanSummary:
    """print_plan_summary 不抛异常。"""

    def test_no_exception(self, capsys) -> None:
        plan = {
            "generated_at": "2026-10-30T00:00:00",
            "kline": {
                "total_records": 10,
                "total_tickers": 2,
                "latest_date": "2026-10-30",
                "expected_date": "2026-10-30",
                "is_up_to_date": True,
                "failed_tickers": [],
                "need_today_tickers": [],
            },
            "metadata": {
                "file": "m.json",
                "entries": 5,
                "age_hours": 1.0,
                "ttl_hours": 24.0,
                "is_stale": False,
                "needs_full_refresh": False,
            },
            "plan": {"fetch_kline": {"tickers": []}, "fetch_metadata": {"mode": "skip"}},
        }
        cds.print_plan_summary(plan)
        captured = capsys.readouterr()
        assert "数据现状检查报告" in captured.out
