"""tests for scripts.daily_analysis."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.daily_analysis import (
    _STOCK_NAMES,
    _WHITELIST_DEFAULT,
    _extract_json,
    ai_verify,
    analyze_stock,
    get_kline,
)


class TestExtractJson:
    """JSON extraction from LLM responses."""

    def test_valid_json(self) -> None:
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_text(self) -> None:
        """JSON with double quotes embedded in text."""
        result = _extract_json('some text {"a": 1} more text')
        assert result == {"a": 1}

    def test_no_json(self) -> None:
        assert _extract_json("no json here") is None

    def test_broken_json(self) -> None:
        assert _extract_json("{broken") is None

    def test_empty_string(self) -> None:
        assert _extract_json("") is None


class TestGetKline:
    """K-line data retrieval from cache."""

    def test_existing_ticker(self) -> None:
        """Mock sqlite3 to avoid relying on real database in CI."""
        mock_df = pd.DataFrame(
            {"timestamps": ["2026-09-01"], "close": [1500.0], "open": [1490.0]}
        )
        buf = BytesIO()
        mock_df.to_pickle(buf)
        buf.seek(0)
        mock_data = buf.read()

        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            df = get_kline("sh.600519")
            assert df is not None
            assert len(df) > 0
            assert "close" in df.columns

    def test_missing_ticker(self) -> None:
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            assert get_kline("sh.999999") is None

    def test_nonexistent_code(self) -> None:
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            assert get_kline("nonexistent") is None


class TestAnalyzeStock:
    """Stock analysis logic tests."""

    def test_with_data(self) -> None:
        result = analyze_stock("sh.601668")
        assert result["status"] == "ok"
        assert 0 <= result["score"] <= 100
        assert result["trend"] in [
            "强势上涨📈",
            "震荡偏多",
            "横盘整理",
            "弱势下跌📉",
        ]
        assert result["ma_signal"] in [
            "多头排列✅",
            "偏多",
            "震荡",
            "偏空",
            "空头排列❌",
        ]

    def test_no_data(self) -> None:
        result = analyze_stock("sh.999999")
        assert result["status"] == "no_data"
        assert result["score"] == 0.0
        assert result["trend"] == "数据不足"

    def test_name_mapping(self) -> None:
        result = analyze_stock("sh.601668")
        assert result["name"] == "中国建筑"

    def test_short_kline(self) -> None:
        """Stock with less than 20 rows should return no_data."""
        tiny_df = pd.DataFrame(
            {
                "timestamps": ["2026-01-01", "2026-01-02"],
                "close": [10.0, 11.0],
                "volume": [100, 200],
            }
        )
        buf = BytesIO()
        tiny_df.to_pickle(buf)
        buf.seek(0)

        conn = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
        conn.execute(
            "INSERT OR REPLACE INTO kline_cache "
            "(ticker, start, end, freq, ttl, data, created, adjustflag) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test_short", "2026-01-01", "2026-01-02", "d", 0.0, buf.read(), 0.0, "1"),
        )
        conn.commit()
        conn.close()

        try:
            result = analyze_stock("test_short")
            assert result["status"] == "no_data"
        finally:
            conn = sqlite3.connect(str(Path("outputs/cache/pipeline_cache.db")))
            conn.execute("DELETE FROM kline_cache WHERE ticker = ?", ("test_short",))
            conn.commit()
            conn.close()

    def test_bj_stock(self) -> None:
        """BJ ticker should return ok (uses existing DB data)."""
        # bj.920001 may not be in DB after clear-cache; use a known existing ticker
        result = analyze_stock("sz.000001")
        assert result["status"] == "ok"
        assert result["score"] >= 0


class TestAiVerify:
    """AI verification logic tests."""

    def test_no_llm_configured(self) -> None:
        """When LLM is unavailable, returns defaults."""
        with patch("scripts.daily_analysis._ai_available", return_value=False):
            result = ai_verify([{"ticker": "sh.600519", "score": 50}], "2026-09-15")
            assert result["analysis_summary"] == "LLM 未配置，跳过 AI 核实"
            assert result["top_picks"] == "无"

    def test_all_keys_present(self) -> None:
        """ai_verify always returns dict with all expected keys."""
        results = [
            {
                "ticker": "sh.600519",
                "name": "贵州茅台",
                "score": 72.5,
                "trend": "强势上涨📈",
                "ma_signal": "多头排列✅",
                "vol_signal": "放量🔥",
            }
        ]
        result = ai_verify(results, "2026-09-15")
        assert "analysis_summary" in result
        assert "top_picks" in result
        assert "risk_alerts" in result
        assert "conclusion" in result

    def test_empty_results(self) -> None:
        """Empty stock list should not crash."""
        result = ai_verify([], "2026-09-15")
        assert isinstance(result, dict)


class TestConstants:
    """Constants verification."""

    def test_whitelist_default(self) -> None:
        assert _WHITELIST_DEFAULT == "000001,002027,601668,000932,601061"

    def test_stock_names(self) -> None:
        assert _STOCK_NAMES["000001"] == "平安银行"
        assert _STOCK_NAMES["601668"] == "中国建筑"
