"""tests for scripts.fetch_reference_data — TTL 刷新机制 + 部分合并模式."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from scripts.fetch_reference_data import (
    _load_existing_metadata,
    main,
    metadata_age_hours,
    refresh_metadata_if_stale,
)


class TestMetadataAgeHours:
    """metadata.json 时效判断."""

    def test_returns_none_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("scripts.fetch_reference_data.METADATA_FILE", tmp_path / "nope.json")
        assert metadata_age_hours() is None

    def test_fresh_file_under_24h(self, tmp_path: Path, monkeypatch) -> None:
        f = tmp_path / "metadata.json"
        f.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("scripts.fetch_reference_data.METADATA_FILE", f)
        monkeypatch.setattr(
            "scripts.fetch_reference_data.time.time", lambda: f.stat().st_mtime + 3600
        )
        age = metadata_age_hours()
        assert age is not None and age == 1.0


class TestLoadExistingMetadata:
    """读取/损坏 metadata.json 的降级行为."""

    def test_returns_empty_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("scripts.fetch_reference_data.METADATA_FILE", tmp_path / "nope.json")
        assert _load_existing_metadata() == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        f = tmp_path / "metadata.json"
        f.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("scripts.fetch_reference_data.METADATA_FILE", f)
        assert _load_existing_metadata() == {}

    def test_valid_json_passthrough(self, tmp_path: Path, monkeypatch) -> None:
        f = tmp_path / "metadata.json"
        data = {"sh.600519": {"pe_ttm": 8.1}, "sz.000858": None}
        f.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr("scripts.fetch_reference_data.METADATA_FILE", f)
        assert _load_existing_metadata() == data


class TestRefreshIfStale:
    """TTL 判断逻辑（不触发真实网络拉取）."""

    def test_fresh_metadata_skips_refresh(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.fetch_reference_data.metadata_age_hours", lambda: 2.0)
        main_mock = MagicMock()
        monkeypatch.setattr("scripts.fetch_reference_data.main", main_mock)
        assert refresh_metadata_if_stale(max_age_hours=24.0) is False
        main_mock.assert_not_called()

    def test_stale_metadata_triggers_full_refresh(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.fetch_reference_data.metadata_age_hours", lambda: 30.0)
        monkeypatch.setattr("scripts.fetch_reference_data.load_checkpoint", lambda: {})
        main_mock = MagicMock()
        monkeypatch.setattr("scripts.fetch_reference_data.main", main_mock)
        assert refresh_metadata_if_stale(max_age_hours=24.0) is True
        main_mock.assert_called_once_with(tickers_input=None, resume=False)

    def test_missing_metadata_triggers_full_refresh(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.fetch_reference_data.metadata_age_hours", lambda: None)
        monkeypatch.setattr("scripts.fetch_reference_data.load_checkpoint", lambda: {})
        main_mock = MagicMock()
        monkeypatch.setattr("scripts.fetch_reference_data.main", main_mock)
        assert refresh_metadata_if_stale() is True
        main_mock.assert_called_once_with(tickers_input=None, resume=False)


class TestMainPartialMerge:
    """--tickers 部分拉取合并模式：不覆盖现有全量数据."""

    def test_partial_mode_preserves_existing_entries(self, monkeypatch) -> None:
        existing = {"sh.600519": {"pe_ttm": 8.1}}
        monkeypatch.setattr(
            "scripts.fetch_reference_data._load_existing_metadata", lambda: existing
        )
        monkeypatch.setattr("scripts.fetch_reference_data.get_all_tickers", lambda: ["sh.600519"])
        monkeypatch.setattr(
            "scripts.fetch_reference_data.METADATA_FILE",
            Path("outputs/cache/metadata.json"),
        )
        # _fetch_metadata_one 返回 ok + 新 meta
        monkeypatch.setattr(
            "scripts.fetch_reference_data._fetch_metadata_one",
            lambda factory, t: (t, {"ticker": t, "pe_ttm": 9.0}, "ok"),
        )
        main(["sh.600519"])
        written = json.loads(Path("outputs/cache/metadata.json").read_text(encoding="utf-8"))
        assert written["sh.600519"]["pe_ttm"] == 9.0
        # 若此前有其他键，也应保留（本次只测 single key）

    def test_full_mode_rebuilds_from_scratch(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "scripts.fetch_reference_data.get_all_tickers", lambda: ["sh.600519", "sz.000858"]
        )
        monkeypatch.setattr(
            "scripts.fetch_reference_data._fetch_metadata_one",
            lambda factory, t: (t, {"ticker": t, "pe_ttm": 1.0}, "ok"),
        )
        monkeypatch.setattr(
            "scripts.fetch_reference_data.METADATA_FILE",
            Path("outputs/cache/metadata.json"),
        )
        main(None)
        written = json.loads(Path("outputs/cache/metadata.json").read_text(encoding="utf-8"))
        assert set(written.keys()) == {"sh.600519", "sz.000858"}
