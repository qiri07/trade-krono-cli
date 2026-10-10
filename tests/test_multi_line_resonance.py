"""tests for scripts.multi_line_resonance — 三周期 MACD 共振筛选。"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class TestIsSt:
    """ST 股票识别测试。"""

    def test_normal_stock_not_st(self) -> None:
        from scripts.multi_line_resonance import _is_st

        assert _is_st("贵州茅台") is False

    @pytest.mark.parametrize(
        "name",
        [
            "xxxST股票",
            "*ST风险股",
            "退市整理期",
            "N新股",
            "C次新股",
        ],
    )
    def test_st_keywords_detected(self, name: str) -> None:
        from scripts.multi_line_resonance import _is_st

        assert _is_st(name) is True


class TestTickerToThscode:
    """ticker → 同花顺 thscode 转换测试。"""

    def test_sh_ticker(self) -> None:
        from scripts._utils import ticker_to_thscode

        assert ticker_to_thscode("sh.600519") == "600519.SH"

    def test_sz_ticker(self) -> None:
        from scripts._utils import ticker_to_thscode

        assert ticker_to_thscode("sz.000001") == "000001.SZ"

    def test_hk_ticker_bare(self) -> None:
        from scripts._utils import ticker_to_thscode

        assert ticker_to_thscode("hk.00700") == "00700"

    def test_no_prefix(self) -> None:
        from scripts._utils import ticker_to_thscode

        assert ticker_to_thscode("600519") == "600519"


class TestStripPrefix:
    """交易所前缀剥离测试。"""

    def test_sh(self) -> None:
        from scripts.multi_line_resonance import _strip_prefix

        assert _strip_prefix("sh.600519") == "600519"

    def test_sz(self) -> None:
        from scripts.multi_line_resonance import _strip_prefix

        assert _strip_prefix("sz.000001") == "000001"

    def test_no_prefix(self) -> None:
        from scripts.multi_line_resonance import _strip_prefix

        assert _strip_prefix("600519") == "600519"


class TestMacd:
    """MACD 计算测试。"""

    def _make_prices(self, n: int = 50, start: float = 100.0) -> pd.Series:
        return pd.Series([start + i * 0.1 for i in range(n)])

    def test_upward_trend_positive_macd(self) -> None:
        from scripts.multi_line_resonance import _macd

        s = self._make_prices(50)
        result = _macd(s)
        assert isinstance(result, float)
        assert result > 0

    def test_downward_trend_negative_macd(self) -> None:
        from scripts.multi_line_resonance import _macd

        s = pd.Series([100.0 - i * 0.1 for i in range(50)])
        result = _macd(s)
        assert result < 0

    def test_flat_series_near_zero(self) -> None:
        from scripts.multi_line_resonance import _macd

        s = pd.Series([100.0] * 50)
        result = _macd(s)
        assert abs(result) < 1e-10

    def test_too_short_returns_nan(self) -> None:
        from scripts.multi_line_resonance import _macd

        s = pd.Series([100.0, 101.0])
        result = _macd(s)
        assert pd.isna(result)


class TestResampleClose:
    """收盘价重采样测试。"""

    def _make_df(self, n: int = 60) -> pd.DataFrame:
        dates = pd.date_range(start="2025-07-01", periods=n, freq="B")
        return pd.DataFrame({"timestamps": dates, "close": [100.0 + i * 0.5 for i in range(n)]})

    def test_weekly_resample(self) -> None:
        from scripts.multi_line_resonance import _resample_close

        close = _resample_close(self._make_df(60), "W")
        assert isinstance(close, pd.Series)
        assert len(close) > 0

    def test_monthly_resample(self) -> None:
        from scripts.multi_line_resonance import _resample_close

        close = _resample_close(self._make_df(60), "ME")
        assert isinstance(close, pd.Series)
        assert len(close) > 0


def _make_mock_kline(n: int = 100, trend: str = "up") -> bytes:
    """生成测试用 K 线 pickle 数据。"""
    dates = pd.date_range(start="2025-01-01", periods=n, freq="B")
    if trend == "up":
        close = [100.0 + i * 0.2 for i in range(n)]
    elif trend == "down":
        close = [100.0 - i * 0.1 for i in range(n)]
    else:
        close = [100.0] * n
    df = pd.DataFrame(
        {
            "timestamps": dates,
            "open": close,
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1_000_000] * n,
        }
    )
    buf = io.BytesIO()
    df.to_pickle(buf)
    return buf.getvalue()


class TestComputeMacdForTicker:
    """单只股票 MACD 共振计算测试（mock DB）。"""

    def test_upward_trend_all_macd_positive(self) -> None:
        """3 年数据确保周线/月线 MACD 可计算。"""
        from scripts.multi_line_resonance import _compute_macd_for_ticker

        mock_data = _make_mock_kline(750, "up")
        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            result = _compute_macd_for_ticker("sh.600519", Path("/tmp/fake.db"))
        assert result is not None
        assert result["d_macd"] > 0
        assert result["score"] > 0

    def test_downward_trend_macd_negative(self) -> None:
        from scripts.multi_line_resonance import _compute_macd_for_ticker

        mock_data = _make_mock_kline(500, "down")
        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            result = _compute_macd_for_ticker("sh.600519", Path("/tmp/fake.db"))
        assert result is None

    def test_missing_record_returns_none(self) -> None:
        from scripts.multi_line_resonance import _compute_macd_for_ticker

        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            assert _compute_macd_for_ticker("sh.999999", Path("/tmp/fake.db")) is None

    def test_too_short_data_returns_none(self) -> None:
        from scripts.multi_line_resonance import _compute_macd_for_ticker

        df = pd.DataFrame({"timestamps": ["2026-01-01"], "close": [100.0]})
        buf = io.BytesIO()
        df.to_pickle(buf)
        short_data = buf.getvalue()

        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (short_data,)
            assert _compute_macd_for_ticker("sh.600519", Path("/tmp/fake.db")) is None


class TestGetAllTickers:
    """全市场 ticker 列表获取测试。"""

    def test_excludes_bj(self) -> None:
        from scripts.multi_line_resonance import _get_all_tickers

        mock_rows = [("sh.600519",), ("sz.000001",), ("bj.920001",)]
        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.cursor.return_value.fetchall.return_value = mock_rows
            tickers = _get_all_tickers(Path("/tmp/fake.db"))
        assert "bj.920001" not in tickers
        assert "sh.600519" in tickers
        assert "sz.000001" in tickers

    def test_empty_result(self) -> None:
        from scripts.multi_line_resonance import _get_all_tickers

        with patch("scripts.multi_line_resonance.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.cursor.return_value.fetchall.return_value = []
            tickers = _get_all_tickers(Path("/tmp/fake.db"))
        assert tickers == []


class TestFetchPeSnapshot:
    """PE 快照获取测试（mock subprocess，直接 patch 模块变量）。"""

    def test_api_key_missing_returns_empty(self) -> None:
        with patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": ""}, clear=True):
            import importlib

            import scripts.multi_line_resonance as mod

            importlib.reload(mod)
            result = mod._fetch_pe_snapshot(["sh.600519"])
            assert result == {}

    def test_success_response(self) -> None:
        from scripts.multi_line_resonance import _fetch_pe_snapshot

        mock_output = (
            '{"code":0,"data":{"item":[{"thscode":"600519.SH",'
            '"pe_ttm":28.5,"name":"贵州茅台"}]}}\n200'
        )
        with (
            patch("scripts.multi_line_resonance.subprocess.run") as mock_run,
            patch("scripts.multi_line_resonance._FUYAO_API_KEY", "fake_key"),
        ):
            mock_run.return_value.stdout = mock_output
            result = _fetch_pe_snapshot(["sh.600519"])
        assert "600519" in result
        assert result["600519"]["pe"] == 28.5
        assert result["600519"]["name"] == "贵州茅台"

    def test_pe_zero_not_included(self) -> None:
        from scripts.multi_line_resonance import _fetch_pe_snapshot

        mock_output = (
            '{"code":0,"data":{"item":[{"thscode":"600519.SH","pe_ttm":0,"name":"X"}]}}\n200'
        )
        with (
            patch("scripts.multi_line_resonance.subprocess.run") as mock_run,
            patch("scripts.multi_line_resonance._FUYAO_API_KEY", "fake_key"),
        ):
            mock_run.return_value.stdout = mock_output
            result = _fetch_pe_snapshot(["sh.600519"])
        assert "600519" not in result

    def test_http_error_skipped(self) -> None:
        from scripts.multi_line_resonance import _fetch_pe_snapshot

        mock_output = '{"code":0}\n500'
        with (
            patch("scripts.multi_line_resonance.subprocess.run") as mock_run,
            patch("scripts.multi_line_resonance._FUYAO_API_KEY", "fake_key"),
        ):
            mock_run.return_value.stdout = mock_output
            result = _fetch_pe_snapshot(["sh.600519"])
        assert result == {}


class TestFetchHkPeSnapshot:
    """港股 PE 快照获取测试（mock requests）。"""

    def test_success(self) -> None:
        from scripts.multi_line_resonance import _fetch_hk_pe_snapshot

        # Tencent API: 60+ pipe-separated fields, PE at index 39
        fields = [""] * 60
        fields[1] = "腾讯控股"
        fields[39] = "15.52"
        mock_text = f"v_hk00700={'~'.join(fields)}"
        with patch("scripts.multi_line_resonance.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = mock_text
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp
            result = _fetch_hk_pe_snapshot(["hk.00700"])
        assert "hk00700" in result
        assert result["hk00700"]["pe"] == 15.52

    def test_missing_parts_skipped(self) -> None:
        from scripts.multi_line_resonance import _fetch_hk_pe_snapshot

        mock_text = "v_hk99999=~short~~"
        with patch("scripts.multi_line_resonance.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = mock_text
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp
            result = _fetch_hk_pe_snapshot(["hk.99999"])
        assert result == {}

    def test_network_error_handled(self) -> None:
        from scripts.multi_line_resonance import _fetch_hk_pe_snapshot

        with patch(
            "scripts.multi_line_resonance.requests.get", side_effect=RuntimeError("timeout")
        ):
            result = _fetch_hk_pe_snapshot(["hk.00700"])
        assert result == {}


class TestBuildSummaryCard:
    """飞书摘要卡片构建测试。"""

    def test_empty_results(self) -> None:
        from scripts.multi_line_resonance import _build_summary_card

        card = _build_summary_card([], "2026-10-10", {})
        assert "2026-10-10" in card
        assert "0 只" in card

    def test_with_results(self) -> None:
        from scripts.multi_line_resonance import _build_summary_card

        results = [
            {
                "ticker": "sh.600519",
                "name": "贵州茅台",
                "close": 1800.0,
                "change_pct": 1.5,
                "d_macd": 0.05,
                "w_macd": 0.03,
                "m_macd": 0.01,
                "pe": 28.5,
                "score": 85.0,
                "ma5": 1790.0,
                "ma10": 1780.0,
                "ma20": 1770.0,
                "ma_signal": "多头排列✅",
                "trend": "强势上涨",
            }
        ]
        card = _build_summary_card(results, "2026-10-10", {})
        assert "贵州茅台" in card
        assert "600519" in card
        assert "85.0" in card


class TestMainFunction:
    """主流程集成测试（mock 外部依赖）。"""

    def test_main_no_tickers_exits(self) -> None:
        from scripts.multi_line_resonance import main

        with patch("scripts.multi_line_resonance._get_all_tickers", return_value=[]):
            with patch("sys.argv", ["multi_line_resonance.py"]):
                with pytest.raises(SystemExit):
                    main()
