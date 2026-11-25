"""测试 TushareProvider — Tushare 金融数据源（所有调用均 mock，无需真实 tushare 包）。

覆盖：
- TushareProvider 类属性与 _ensure_import（含缺失 token、ImportError、已加载）
- _to_ts_code 各分支（hk/sh/sz/bj 前缀、已带后缀、裸代码 6/5/900/43/83/87/920、默认）
- fetch_kline/fetch_quote/fetch_metadata（hk 代码返回 None、空 DataFrame、正常、异常）
- health_check（成功、空结果、异常）
- pd_notna 模块函数（正常值、NaN、None、异常降级）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.data_providers.tushare_provider import (
    TushareProvider,
    pd_notna,
)


@pytest.fixture
def provider() -> TushareProvider:
    return TushareProvider()


def _reset_class_state() -> None:
    TushareProvider._ts = None
    TushareProvider._token = ""


class TestTushareProviderClassAttrs:
    """TushareProvider 类属性测试。"""

    def test_name(self, provider: TushareProvider) -> None:
        assert provider.name == "tushare"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is True


class TestEnsureImport:
    """_ensure_import 方法测试。"""

    def test_ensure_import_success(self, provider: TushareProvider) -> None:
        """_ensure_import 成功时应设置 _ts 属性。"""
        mock_ts = MagicMock()
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.dict("sys.modules", {"tushare": mock_ts}),
        ):
            provider._ensure_import()
            assert provider._ts is not None
            assert provider._token == "fake_token"
        _reset_class_state()

    def test_ensure_import_already_loaded(self, provider: TushareProvider) -> None:
        """_ts 已加载时 _ensure_import 不应重复导入。"""
        mock_ts = MagicMock()
        _reset_class_state()
        provider.__class__._ts = mock_ts
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch("builtins.__import__") as mock_import,
        ):
            provider._ensure_import()
            mock_import.assert_not_called()
        _reset_class_state()

    def test_ensure_import_missing_token(self) -> None:
        """未设置 TUSHARE_TOKEN 时应抛出 RuntimeError。"""
        _reset_class_state()
        with patch.dict("os.environ", {}, clear=True):
            p = TushareProvider()
            p.__class__._ts = None
            p.__class__._token = ""
            with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
                p._ensure_import()
        _reset_class_state()

    def test_ensure_import_import_error(self) -> None:
        """tushare 包未安装时应抛出 RuntimeError。"""
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.dict("sys.modules", {"tushare": None}),
        ):
            p = TushareProvider()
            p._ts = None
            p._token = ""
            with (
                patch.object(p.__class__, "_ts", None),
                patch.object(p.__class__, "_token", ""),
                pytest.raises(RuntimeError, match="tushare 未安装"),
            ):
                p._ensure_import()
        _reset_class_state()


class TestToTsCode:
    """_to_ts_code 静态方法测试（不依赖 tushare 包）。"""

    def test_hk_ticker_returns_none(self) -> None:
        assert TushareProvider._to_ts_code("hk.00700") is None

    def test_sh_prefix(self) -> None:
        assert TushareProvider._to_ts_code("sh.600519") == "600519.SH"

    def test_sz_prefix(self) -> None:
        assert TushareProvider._to_ts_code("sz.000858") == "000858.SZ"

    def test_bj_prefix(self) -> None:
        assert TushareProvider._to_ts_code("bj.920001") == "920001.BJ"

    def test_already_formatted_sh(self) -> None:
        assert TushareProvider._to_ts_code("600519.SH") == "600519.SH"

    def test_already_formatted_sz(self) -> None:
        assert TushareProvider._to_ts_code("000858.SZ") == "000858.SZ"

    def test_already_formatted_bj(self) -> None:
        assert TushareProvider._to_ts_code("920001.BJ") == "920001.BJ"

    def test_bare_code_600_sh(self) -> None:
        assert TushareProvider._to_ts_code("600519") == "600519.SH"

    def test_bare_code_500_sh(self) -> None:
        assert TushareProvider._to_ts_code("510300") == "510300.SH"

    def test_bare_code_900_sh(self) -> None:
        assert TushareProvider._to_ts_code("900001") == "900001.SH"

    def test_bare_code_43_bj(self) -> None:
        assert TushareProvider._to_ts_code("430001") == "430001.BJ"

    def test_bare_code_83_bj(self) -> None:
        assert TushareProvider._to_ts_code("830001") == "830001.BJ"

    def test_bare_code_87_bj(self) -> None:
        assert TushareProvider._to_ts_code("870001") == "870001.BJ"

    def test_bare_code_920_bj(self) -> None:
        assert TushareProvider._to_ts_code("920001") == "920001.BJ"

    def test_bare_code_default_sz(self) -> None:
        assert TushareProvider._to_ts_code("000001") == "000001.SZ"

    def test_bare_code_unknown_prefix_sz(self) -> None:
        assert TushareProvider._to_ts_code("300001") == "300001.SZ"


class TestFetchKline:
    """fetch_kline 方法测试。"""

    def _mock_ts(self, pro_bar_return: object) -> MagicMock:
        mock_ts = MagicMock()
        mock_ts.pro_bar.return_value = pro_bar_return
        return mock_ts

    def test_fetch_kline_hk_returns_none(self, provider: TushareProvider) -> None:
        """hk. 代码转换为 None 时 fetch_kline 应返回 None。"""
        _reset_class_state()
        mock_ts = self._mock_ts(None)
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("hk.00700", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_success(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "trade_date": ["20260801", "20260804"],
                "open": [100.0, 102.0],
                "high": [103.0, 104.0],
                "low": [99.0, 101.0],
                "close": [101.0, 103.0],
                "vol": [1e6, 1.2e6],
                "amount": [1e8, 1.2e8],
            },
        )
        mock_ts = self._mock_ts(mock_df)
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 2
            assert result.open[0] == 100.0
            assert result.close[-1] == 103.0
        _reset_class_state()

    def test_fetch_kline_empty_result(self, provider: TushareProvider) -> None:
        mock_ts = self._mock_ts(pd.DataFrame())
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_none_df(self, provider: TushareProvider) -> None:
        mock_ts = self._mock_ts(None)
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_api_exception(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.pro_bar.side_effect = Exception("API rate limit")
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None
        _reset_class_state()

    def test_fetch_kline_sz_ticker(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "trade_date": ["20260801"],
                "open": [10.0],
                "high": [10.5],
                "low": [9.8],
                "close": [10.2],
                "vol": [5e5],
                "amount": [5e6],
            },
        )
        mock_ts = self._mock_ts(mock_df)
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_kline("sz.000001", "2026-01-01", "2026-08-13")
            assert result is not None
            mock_ts.pro_bar.assert_called_once()
        _reset_class_state()


class TestFetchMetadata:
    """fetch_metadata 方法测试。"""

    def test_fetch_metadata_hk_returns_none(self, provider: TushareProvider) -> None:
        """hk. 代码转换为 None 时 fetch_metadata 应返回 None。"""
        _reset_class_state()
        mock_ts = MagicMock()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_metadata("hk.00700")
            assert result is None
        _reset_class_state()

    def test_fetch_metadata_success(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "name": ["贵州茅台"],
                "industry": ["白酒"],
                "list_date": ["1999-11-10"],
                "delist_date": [None],
            },
        )
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            meta = provider.fetch_metadata("sh.600519")
            assert meta is not None
            assert meta.industry == "白酒"
            assert meta.ipo_date == "1999-11-10"
            assert meta.is_st is False
        _reset_class_state()

    def test_fetch_metadata_st_stock(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "ts_code": ["601234.SH"],
                "name": ["*ST某某"],
                "industry": ["机械"],
                "list_date": ["20200101"],
                "delist_date": [None],
            },
        )
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            meta = provider.fetch_metadata("sh.601234")
            assert meta is not None
            assert meta.is_st is True
        _reset_class_state()

    def test_fetch_metadata_empty_result(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = pd.DataFrame()
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_metadata("sh.600519")
            assert result is None
        _reset_class_state()

    def test_fetch_metadata_api_exception(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.stock_basic.side_effect = Exception("API error")
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_metadata("sh.600519")
            assert result is None
        _reset_class_state()


class TestFetchQuote:
    """fetch_quote 方法测试。"""

    def test_fetch_quote_hk_returns_none(self, provider: TushareProvider) -> None:
        """hk. 代码转换为 None 时 fetch_quote 应返回 None。"""
        _reset_class_state()
        mock_ts = MagicMock()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_quote("hk.00700")
            assert result is None
        _reset_class_state()

    def test_fetch_quote_success(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame(
            {
                "last_close": [1750.0],
                "price": [1800.0],
                "pe": [35.5],
                "pb": [9.2],
                "total_mv": [2.26e12],
            },
        )
        mock_ts = MagicMock()
        mock_ts.realtime_quote.return_value = mock_df
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is not None
            assert result.price == 1750.0
            assert result.pe == 35.5
            assert result.pb == 9.2
            assert result.source == "tushare"
        _reset_class_state()

    def test_fetch_quote_empty_result(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.realtime_quote.return_value = pd.DataFrame()
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is None
        _reset_class_state()

    def test_fetch_quote_api_exception(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.realtime_quote.side_effect = Exception("API error")
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            result = provider.fetch_quote("sh.600519")
            assert result is None
        _reset_class_state()


class TestHealthCheck:
    """health_check 方法测试。"""

    def test_health_check_success(self, provider: TushareProvider) -> None:
        mock_df = pd.DataFrame({"ts_code": ["600519.SH"]})
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = mock_df
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            assert provider.health_check() is True
        _reset_class_state()

    def test_health_check_failure(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.stock_basic.side_effect = Exception("fail")
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            assert provider.health_check() is False
        _reset_class_state()

    def test_health_check_empty_result(self, provider: TushareProvider) -> None:
        mock_ts = MagicMock()
        mock_ts.stock_basic.return_value = pd.DataFrame()
        _reset_class_state()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "fake_token"}),
            patch.object(provider.__class__, "_ts", mock_ts),
        ):
            assert provider.health_check() is False
        _reset_class_state()


class TestPdNotNa:
    """pd_notna 模块函数测试。"""

    def test_pd_notna_normal_value(self) -> None:
        assert pd_notna(42) is True
        assert pd_notna("hello") is True
        assert pd_notna(3.14) is True

    def test_pd_notna_nan(self) -> None:
        assert pd_notna(float("nan")) is False
        assert pd_notna(pd.NA) is False

    def test_pd_notna_none(self) -> None:
        # pandas 将 None 视为 NA，pd.isna(None) = True，not True = False
        assert pd_notna(None) is False

    def test_pd_notna_exception_fallback(self) -> None:
        """pd.isna 抛异常时应降级为 value is not None。"""
        with patch("pandas.isna", side_effect=RuntimeError("boom")):
            assert pd_notna(42) is True
            assert pd_notna(None) is False
