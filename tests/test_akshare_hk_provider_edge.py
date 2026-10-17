"""akshare_hk_provider 扩展边界测试。

补充 test_akshare_hk_provider.py 未覆盖的分支：
- 新浪直连解析（base64/JSON 正常、列缺失、状态码/正则失败）
- 腾讯分钟线解析（正常、code!=0、分钟数据缺失、状态码失败）
- fetch_kline 缺少 date 列 / 过滤后为空 / 无 amount 列
- fetch_metadata 成功 / 字段不足 / 网络异常

注意：被 patch 的类方法内部通过 `cls._ak` / `cls._req` 访问（类属性），
必须用 patch.object(AkShareHKProvider, ...) 做类级替换，实例属性赋值不生效。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from trade_krono_cli.data_providers.akshare_hk_provider import AkShareHKProvider


def _sina_encoded(rows: list[dict]) -> str:
    encoded = base64.b64encode(json.dumps(rows).encode()).decode()
    return f'var KLC_K2_00700="{encoded}";'


def _sina_rows() -> list[dict]:
    return [
        {"date": "2026-09-01", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        {
            "date": "2026-09-02",
            "open": 100.5,
            "high": 102,
            "low": 100,
            "close": 101.5,
            "volume": 1200,
        },
    ]


class TestSinaDirect:
    def test_sina_direct_success(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(
                        return_value=SimpleNamespace(
                            status_code=200, text=_sina_encoded(_sina_rows())
                        )
                    )
                ),
            ),
        ):
            df = provider._fetch_via_sina_direct("00700")
        assert df is not None
        assert len(df) == 2
        assert "close" in df.columns

    def test_sina_direct_missing_columns(self) -> None:
        provider = AkShareHKProvider()
        rows = [{"date": "2026-09-01", "open": 100}]  # 缺 high/low/close/volume
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(
                        return_value=SimpleNamespace(status_code=200, text=_sina_encoded(rows))
                    )
                ),
            ),
        ):
            df = provider._fetch_via_sina_direct("00700")
        assert df is None

    def test_sina_direct_bad_status(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(return_value=SimpleNamespace(status_code=500, text=""))
                ),
            ),
        ):
            df = provider._fetch_via_sina_direct("00700")
        assert df is None

    def test_sina_direct_no_match_pattern(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(return_value=SimpleNamespace(status_code=200, text="nothing"))
                ),
            ),
        ):
            df = provider._fetch_via_sina_direct("00700")
        assert df is None


class TestTencentMinute:
    def _tencent_payload(self, minutes: list[str], code: int = 0) -> str:
        return json.dumps({"code": code, "data": {"hk00700": {"data": {"data": minutes}}}})

    def test_tencent_minute_success(self) -> None:
        minutes = [
            "0930 100.0 12000 1725000000",
            "0931 100.2 13000 1725000060",
            "bad_row_only_two",  # 被跳过（<4 字段）
        ]
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(
                        return_value=SimpleNamespace(
                            status_code=200,
                            text=f"test={self._tencent_payload(minutes)}",
                        )
                    )
                ),
            ),
        ):
            df = provider._fetch_via_tencent_minute("00700")
        assert df is not None
        assert len(df) == 2  # 第三行被跳过
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_tencent_minute_nonzero_code(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(
                        return_value=SimpleNamespace(
                            status_code=200,
                            text=f"test={self._tencent_payload([], code=1)}",
                        )
                    )
                ),
            ),
        ):
            df = provider._fetch_via_tencent_minute("00700")
        assert df is None

    def test_tencent_minute_empty_minutes(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(
                        return_value=SimpleNamespace(
                            status_code=200,
                            text=f"test={self._tencent_payload([])}",
                        )
                    )
                ),
            ),
        ):
            df = provider._fetch_via_tencent_minute("00700")
        assert df is None

    def test_tencent_minute_bad_status(self) -> None:
        provider = AkShareHKProvider()
        with (
            patch.object(AkShareHKProvider, "_ensure_import"),
            patch.object(AkShareHKProvider, "_ak", MagicMock()),
            patch.object(
                AkShareHKProvider,
                "_req",
                SimpleNamespace(
                    get=MagicMock(return_value=SimpleNamespace(status_code=404, text=""))
                ),
            ),
        ):
            df = provider._fetch_via_tencent_minute("00700")
        assert df is None


class TestFetchKlineEdge:
    def test_fetch_kline_missing_date_column(self) -> None:
        """数据无 date 列 → 返回 None。"""
        provider = AkShareHKProvider()
        df = pd.DataFrame(
            {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]}
        )
        with (
            patch.object(AkShareHKProvider, "_fetch_via_akshare_daily", return_value=df),
            patch.object(AkShareHKProvider, "_fetch_via_sina_direct", return_value=None),
            patch.object(AkShareHKProvider, "_fetch_via_tencent_minute", return_value=None),
        ):
            result = provider.fetch_kline("hk.00700")
        assert result is None

    def test_fetch_kline_filtered_empty_returns_none(self) -> None:
        """日期过滤后为空 → 返回 None。"""
        provider = AkShareHKProvider()
        df = pd.DataFrame(
            {
                "date": [datetime(2025, 1, 2)],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000],
            }
        )
        with patch.object(AkShareHKProvider, "_fetch_via_akshare_daily", return_value=df):
            result = provider.fetch_kline(
                "hk.00700", start_date="2026-01-01", end_date="2026-12-31"
            )
        assert result is None

    def test_fetch_kline_without_amount_column(self) -> None:
        """无 amount 列时以 0.0 填充。"""
        provider = AkShareHKProvider()
        df = pd.DataFrame(
            {
                "date": [datetime(2026, 9, 1), datetime(2026, 9, 2)],
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [1000, 1100],
            }
        )
        with patch.object(AkShareHKProvider, "_fetch_via_akshare_daily", return_value=df):
            result = provider.fetch_kline("hk.00700")
        assert result is not None
        assert result.amount == [0.0, 0.0]
        assert result.length == 2


class TestFetchMetadata:
    def test_fetch_metadata_success(self) -> None:
        provider = AkShareHKProvider()
        parts = [""] * 60
        parts[39] = "12.5"  # PE
        parts[58] = "1.8"  # PB
        resp = SimpleNamespace(text="~".join(parts), raise_for_status=MagicMock())
        with patch(
            "trade_krono_cli.data_providers.akshare_hk_provider.requests.get",
            return_value=resp,
        ):
            meta = provider.fetch_metadata("hk.00700")
        assert meta is not None
        assert meta.pe_ttm == 12.5
        assert meta.pb == 1.8
        assert meta.ticker == "hk.00700"

    def test_fetch_metadata_insufficient_fields(self) -> None:
        provider = AkShareHKProvider()
        resp = SimpleNamespace(text="a~b~c", raise_for_status=MagicMock())
        with patch(
            "trade_krono_cli.data_providers.akshare_hk_provider.requests.get",
            return_value=resp,
        ):
            assert provider.fetch_metadata("hk.00700") is None

    def test_fetch_metadata_network_error(self) -> None:
        provider = AkShareHKProvider()
        with patch(
            "trade_krono_cli.data_providers.akshare_hk_provider.requests.get",
            side_effect=Exception("network down"),
        ):
            assert provider.fetch_metadata("hk.00700") is None
