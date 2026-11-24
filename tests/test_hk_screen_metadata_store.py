"""hk_metadata 本地元数据表读写测试（三态语义与向后兼容迁移）。"""

from __future__ import annotations

import sqlite3

from scripts.hk_screen.metadata_store import (
    ensure_hk_metadata_table,
    has_financial_data,
    has_profile_data,
    is_stale,
    is_stale_hours,
    read_hk_metadata,
    upsert_hk_metadata,
)


def make_conn() -> sqlite3.Connection:
    """创建内存数据库连接。"""
    conn = sqlite3.connect(":memory:")
    ensure_hk_metadata_table(conn)
    return conn


class TestUpsert:
    def test_boolean_tri_state_round_trip(self) -> None:
        """布尔字段 True/False/None 三态必须无损往返。"""
        conn = make_conn()
        try:
            upsert_hk_metadata(
                conn,
                {
                    "ticker": "hk.00700",
                    "is_hk_connect": True,
                    "clean_audit_2y": False,
                    "dividend_3y": None,
                },
            )
            row = read_hk_metadata(conn, ["hk.00700"])["hk.00700"]

            assert row["is_hk_connect"] is True
            assert row["clean_audit_2y"] is False
            assert row["dividend_3y"] is None
        finally:
            conn.close()

    def test_partial_upsert_does_not_erase_other_fields(self) -> None:
        """只刷新行情时不能冲掉财务字段。"""
        conn = make_conn()
        try:
            upsert_hk_metadata(conn, {"ticker": "hk.00700", "avg_roe_3y": 0.19})
            upsert_hk_metadata(conn, {"ticker": "hk.00700", "pe_ttm": 11.4})

            row = read_hk_metadata(conn, ["hk.00700"])["hk.00700"]
            assert row["avg_roe_3y"] == 0.19
            assert row["pe_ttm"] == 11.4
        finally:
            conn.close()

    def test_missing_ticker_raises(self) -> None:
        conn = make_conn()
        try:
            try:
                upsert_hk_metadata(conn, {"pe_ttm": 10.0})
            except ValueError:
                pass
            else:
                raise AssertionError("缺少 ticker 应抛出 ValueError")
        finally:
            conn.close()


class TestLegacySchema:
    def test_legacy_defaults_are_rebuilt_to_null(self) -> None:
        """旧 schema 的 DEFAULT 0 必须重建为 NULL（未知语义）。"""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE hk_metadata ("
            "ticker TEXT PRIMARY KEY, name TEXT, clean_audit_2y INTEGER DEFAULT 0, "
            "is_hkd INTEGER DEFAULT 1, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO hk_metadata (ticker, name) VALUES ('hk.00700', '腾讯控股')"
        )
        conn.commit()

        ensure_hk_metadata_table(conn)
        try:
            defaults = [r[4] for r in conn.execute("PRAGMA table_info(hk_metadata)")]
            assert all(value is None for value in defaults)

            row = read_hk_metadata(conn, ["hk.00700"])["hk.00700"]
            assert row["name"] == "腾讯控股"
            assert row["clean_audit_2y"] is None
            assert row["is_hkd"] is None
            assert "avg_roe_3y" in row  # 新列已补齐
        finally:
            conn.close()


class TestStaleness:
    def test_is_stale_days(self) -> None:
        from datetime import datetime, timedelta

        old = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        assert is_stale(old, 30) is True
        assert is_stale(old, 0) is False
        assert is_stale(None, 30) is True

    def test_is_stale_hours(self) -> None:
        from datetime import datetime, timedelta

        old = (datetime.now() - timedelta(hours=13)).strftime("%Y-%m-%d %H:%M:%S")
        assert is_stale_hours(old, 12) is True
        assert is_stale_hours(old, 24) is False
        assert is_stale_hours(None, 12) is True


class TestCompleteness:
    def test_has_financial_data(self) -> None:
        assert has_financial_data(None) is False
        assert has_financial_data({"debt_to_asset": 0.4}) is False
        assert has_financial_data(
            {
                "debt_to_asset": 0.4,
                "non_gaap_profit_2y": True,
                "operating_cashflow_3y": True,
                "revenue_growth_3y": True,
            }
        ) is True

    def test_has_profile_data(self) -> None:
        assert has_profile_data({"listing_date": "2004-06-16", "is_hkd": True}) is False
        assert has_profile_data(
            {
                "listing_date": "2004-06-16",
                "listing_days": 8000,
                "is_hk_connect": True,
            }
        ) is True


class TestSpotRecordBuild:
    def test_missing_quote_fields_are_not_written(self) -> None:
        """新浪快照无 PE/市值时不能把已有估值写成 NULL。"""
        from scripts.fetch_hk_metadata import build_spot_record

        class Row(dict):
            """模拟 pandas Series 的 get 接口。"""

        row = Row({"中文名称": "腾讯控股", "最新价": 344.0, "代码": "00700"})
        record = build_spot_record("hk.00700", row, hk_connect=None, now_str="2026-11-24 16:00:00")  # type: ignore[arg-type]

        assert record["name"] == "腾讯控股"
        assert record["price"] == 344.0
        assert "pe_ttm" not in record
        assert "market_cap" not in record
        assert "is_hk_connect" not in record
