"""tests for trade_krono_cli.data_providers.efinance_provider (efinance 免费开源数据源)。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trade_krono_cli.data_providers.efinance_provider import EFinanceProvider


class TestEFinanceProvider:
    @pytest.fixture
    def provider(self):
        return EFinanceProvider()

    def test_name_and_capabilities(self, provider) -> None:
        assert provider.name == "efinance"
        assert provider.supports_kline is True
        assert provider.supports_quote is True
        assert provider.supports_metadata is True

    def test_ticker_to_code(self, provider) -> None:
        """带前缀 ticker 转 6 位裸代码；非法格式返回 None。"""
        assert provider._ticker_to_code("sh.600519") == "600519"
        assert provider._ticker_to_code("sz.000858") == "000858"
        assert provider._ticker_to_code("bj.920000") == "920000"
        assert provider._ticker_to_code("600519") == "600519"
        assert provider._ticker_to_code("hk.00700") is None
        assert provider._ticker_to_code("invalid") is None

    def test_fetch_kline_import_error(self, provider) -> None:
        with patch.object(
            EFinanceProvider, "_ensure_import", side_effect=RuntimeError("efinance not installed")
        ), pytest.raises(RuntimeError, match="efinance not installed"):
            provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")

    def test_fetch_kline_success(self, provider) -> None:
        """efinance 返回中文列名的 DataFrame，应标准化为 KlineData。"""
        df = pd.DataFrame(
            {
                "股票名称": ["贵州茅台", "贵州茅台"],
                "股票代码": ["600519", "600519"],
                "日期": ["2026-10-01", "2026-10-08"],
                "开盘": [1250.0, 1252.9],
                "收盘": [1255.0, 1255.79],
                "最高": [1260.0, 1258.0],
                "最低": [1245.0, 1242.0],
                "成交量": [25000, 25168],
                "成交额": [3.1e9, 3.145e9],
            }
        )

        # 预置类属性，避免 _ensure_import 尝试真实导入 efinance
        mock_ef = MagicMock()
        mock_ef.stock.get_quote_history.return_value = df
        EFinanceProvider._ef = mock_ef

        with patch(
            "trade_krono_cli.data_providers.efinance_provider.pd_to_datetime_safe"
        ) as mock_pd:
            mock_pd.side_effect = lambda s: [datetime.strptime(str(v), "%Y-%m-%d") for v in s]

            result = provider.fetch_kline("sh.600519", "2026-10-01", "2026-10-08")
            assert result is not None
            assert not result.is_empty
            assert result.length == 2
            assert result.close[0] == 1255.0
            assert result.close[1] == 1255.79

            # 验证调用了 efinance API，beg/end 格式为 YYYYMMDD
            mock_ef.stock.get_quote_history.assert_called_once_with(
                "600519", beg="20261001", end="20261008"
            )

    def test_fetch_kline_unsupported_frequency(self, provider) -> None:
        """efinance 仅支持日线，分钟线应返回 None。"""
        result = provider.fetch_kline("sh.600519", "2026-10-01", "2026-10-08", frequency="5min")
        assert result is None

    def test_fetch_kline_empty_result(self, provider) -> None:
        """efinance 返回空 DataFrame 时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_quote_history.return_value = pd.DataFrame()
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_kline("sh.600519", "2026-10-01", "2026-10-08")
        assert result is None

    def test_fetch_quote_success(self, provider) -> None:
        """efinance 实时行情返回 DataFrame，提取最新价/PE/PB/市值/换手率。"""
        df = pd.DataFrame(
            {
                "代码": ["600519"],
                "名称": ["贵州茅台"],
                "最新价": [1451.14],
                "动态市盈率": [21.1],
                "市净率": [6.69],
                "总市值": [1814043414475.0],
                "换手率": [0.23],
            }
        )

        mock_ef = MagicMock()
        mock_ef.stock.get_latest_quote.return_value = df
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_quote("sh.600519")
        assert result is not None
        assert result.ticker == "sh.600519"
        assert result.price == 1451.14
        assert result.pe == 21.1
        assert result.pb == 6.69
        assert result.market_cap == 1814043414475.0
        assert result.turnover == 0.23
        assert result.source == "efinance"

    def test_fetch_metadata_success(self, provider) -> None:
        """efinance 基本面返回单行 DataFrame，提取行业/PE/PB。"""
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "股票名称": ["贵州茅台"],
                "所处行业": ["白酒Ⅱ"],
                "市盈率(动)": [21.1],
                "市净率": [6.69],
            }
        )

        mock_ef = MagicMock()
        mock_ef.stock.get_base_info.return_value = df
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_metadata("sh.600519")
        assert result is not None
        assert result.ticker == "sh.600519"
        assert result.industry == "白酒Ⅱ"
        assert result.pe_ttm == 21.1
        assert result.pb == 6.69
        assert result.source == "efinance"
        # efinance base_info 不提供上市日期
        assert result.ipo_date is None

    def test_fetch_metadata_unsupported_ticker(self, provider) -> None:
        """非 6 位数字代码（如港股）应返回 None。"""
        result = provider.fetch_metadata("hk.00700")
        assert result is None

    def test_ensure_import_import_error(self, provider) -> None:
        """efinance 未安装时 _ensure_import 应抛出 RuntimeError。"""
        import sys

        EFinanceProvider._ef = None
        with (
            patch.dict(sys.modules, {"efinance": None}),
            pytest.raises(RuntimeError, match="efinance 未安装"),
        ):
            provider._ensure_import()

    def test_fetch_kline_unsupported_ticker_returns_none(self, provider) -> None:
        """非 6 位数字 ticker（如港股）在 fetch_kline 中应返回 None。"""
        result = provider.fetch_kline("hk.00700", "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_kline_exception_returns_none(self, provider) -> None:
        """efinance API 异常时 fetch_kline 应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_quote_history.side_effect = RuntimeError("network error")
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_kline_missing_columns_returns_none(self, provider) -> None:
        """efinance 返回缺少必要列的 DataFrame 时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_quote_history.return_value = pd.DataFrame(
            {"日期": ["2026-01-01"], "开盘": [100.0]}
        )
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
        assert result is None

    def test_fetch_quote_unsupported_ticker_returns_none(self, provider) -> None:
        """非 6 位数字 ticker 在 fetch_quote 中应返回 None。"""
        result = provider.fetch_quote("hk.00700")
        assert result is None

    def test_fetch_quote_exception_returns_none(self, provider) -> None:
        """efinance 实时行情异常时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_latest_quote.side_effect = RuntimeError("network error")
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_quote("sh.600519")
        assert result is None

    def test_fetch_quote_empty_df_returns_none(self, provider) -> None:
        """efinance 实时行情返回空 DataFrame 时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_latest_quote.return_value = pd.DataFrame()
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_quote("sh.600519")
        assert result is None

    def test_fetch_metadata_exception_returns_none(self, provider) -> None:
        """efinance 基本面异常时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_base_info.side_effect = RuntimeError("network error")
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_metadata("sh.600519")
        assert result is None

    def test_fetch_metadata_result_none_returns_none(self, provider) -> None:
        """get_base_info 返回 None 时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_base_info.return_value = None
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_metadata("sh.600519")
        assert result is None

    def test_fetch_metadata_result_other_returns_none(self, provider) -> None:
        """get_base_info 返回非 Series/非 DataFrame 时应返回 None。"""
        mock_ef = MagicMock()
        mock_ef.stock.get_base_info.return_value = "not a dataframe"
        EFinanceProvider._ef = mock_ef

        result = provider.fetch_metadata("sh.600519")
        assert result is None

    def test_get_cell_nan_returns_none(self, provider) -> None:
        """_get_cell 对 NaN 值应返回 None。"""

        from trade_krono_cli.data_providers.efinance_provider import _get_cell

        assert _get_cell({"a": float("nan")}, "a") is None

    def test_get_cell_missing_key_returns_none(self, provider) -> None:
        """_get_cell 对缺失键应返回 None。"""
        from trade_krono_cli.data_providers.efinance_provider import _get_cell

        assert _get_cell({"a": 1}, "b") is None

    def test_get_cell_exception_returns_none(self, provider) -> None:
        """_get_cell 对没有 .get 的对象应返回 None。"""
        from trade_krono_cli.data_providers.efinance_provider import _get_cell

        assert _get_cell([1, 2, 3], "a") is None

    def test_pd_to_datetime_safe_exception(self, provider) -> None:
        """pd_to_datetime_safe 解析失败时应回退到 strptime。"""
        from trade_krono_cli.data_providers.efinance_provider import pd_to_datetime_safe

        result = pd_to_datetime_safe(["2026-01-01", "2026-01-02"])
        assert len(result) == 2
